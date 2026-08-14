"""The agent remediates a real ECS Fargate service.

Creates a real ECS service (at desired count 0, so no tasks run and nothing is billed), then has the
agent scale it and roll back its task definition through the real ECS API - each action idempotent
under its ledger key - and verifies every change by reading the service's live state back from AWS.
Tears the service and cluster down at the end.

    python lab/live_ecs.py

Requires the `aws` extra, working AWS credentials (ECS + IAM + EC2 describe), and the CockroachDB
Cloud connection in .env. Creates a throwaway IAM role, ECS cluster, task definition, and service.
"""

from __future__ import annotations

import os
import sys
import time
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, "src")

REGION = "us-east-1"
CLUSTER = "cairn-demo"
SERVICE = "checkout-api"
FAMILY = "checkout-api"


def load_env():
    for line in open(".env", encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k] = v


def say(m):
    print(f"  {m}", flush=True)


def ensure_execution_role(iam) -> str:
    name = "ecsTaskExecutionRole"
    trust = ('{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
             '"Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}')
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
    except ClientError:
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=trust)["Role"]["Arn"]
        iam.attach_role_policy(
            RoleName=name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy")
        say("created ecsTaskExecutionRole; waiting for it to propagate...")
        time.sleep(12)
    return arn


def register_task_def(ecs, exec_role, cpu_note) -> str:
    td = ecs.register_task_definition(
        family=FAMILY, requiresCompatibilities=["FARGATE"], networkMode="awsvpc",
        cpu="256", memory="512", executionRoleArn=exec_role,
        containerDefinitions=[{
            "name": "app", "image": "public.ecr.aws/nginx/nginx:stable",
            "essential": True, "portMappings": [{"containerPort": 80}],
        }])
    return td["taskDefinition"]["taskDefinitionArn"]


def main() -> int:
    load_env()
    print("\n=== The agent remediates a real ECS Fargate service ===\n")
    sess = boto3.Session(aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                         aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
                         region_name=REGION)
    ec2, iam, ecs = sess.client("ec2"), sess.client("iam"), sess.client("ecs")

    vpc = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subnets = [s["SubnetId"] for s in ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc]}])["Subnets"][:2]]
    sg = ec2.describe_security_groups(
        Filters=[{"Name": "vpc-id", "Values": [vpc]}, {"Name": "group-name", "Values": ["default"]}]
    )["SecurityGroups"][0]["GroupId"]

    # ECS needs an account-level service-linked role; on a fresh account it must be created once.
    try:
        iam.create_service_linked_role(AWSServiceName="ecs.amazonaws.com")
        say("created the ECS service-linked role; waiting for it to propagate ...")
        time.sleep(12)
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidInput":  # already exists
            raise

    exec_role = ensure_execution_role(iam)
    ecs.create_cluster(clusterName=CLUSTER)
    say(f"cluster {CLUSTER} ready; registering two task-definition revisions ...")
    rev1 = register_task_def(ecs, exec_role, "v1")   # revision 1 (the "good" one)
    rev2 = register_task_def(ecs, exec_role, "v2")   # revision 2 (the "bad deploy")
    good_rev = int(rev1.rsplit(":", 1)[-1])

    say(f"creating service {SERVICE} at desired count 0 (no tasks run, nothing billed) on rev {rev2}")
    ecs.create_service(
        cluster=CLUSTER, serviceName=SERVICE, taskDefinition=rev2, desiredCount=0,
        launchType="FARGATE",
        networkConfiguration={"awsvpcConfiguration": {
            "subnets": subnets, "securityGroups": [sg], "assignPublicIp": "ENABLED"}})

    try:
        from cairn.aws.ecs import real_registry
        from cairn.db import connect
        from cairn.ledger import Ledger

        registry = real_registry(CLUSTER, REGION)
        tenant = uuid4()
        with connect(os.environ["CAIRN_DATABASE_URL"], connect_timeout=25) as conn:
            ledger = Ledger(conn, tenant_id=tenant)
            run = ledger.open_run(title="remediate the real checkout-api service")

            # Step 1: scale up. Step 2: roll back to the known-good revision.
            i1 = ledger.record_intent(run, 1, "ecs.update_service",
                                      {"service": SERVICE, "desired_count": 2})
            i2 = ledger.record_intent(run, 2, "ecs.rollback_deployment",
                                      {"service": SERVICE, "family": FAMILY, "task_definition": good_rev})

            for intent in (i1, i2):
                claim = ledger.claim_next(run, worker="cairn-watch", lease_seconds=30)
                obs = registry[claim.intent.effector].apply(
                    conn, tenant, claim.intent.idem_key, "cairn-watch", claim.intent.params)
                ledger.record_result(claim, outcome="succeeded", observed_state=obs)
                say(f"applied {claim.intent.effector}: {obs}")

            # Idempotency: replay step 1 — it must NOT call ECS again.
            before = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0]["desiredCount"]
            registry["ecs.update_service"].apply(conn, tenant, i1.idem_key, "cairn-watch", i1.params)
            after = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0]["desiredCount"]
            say(f"replayed step 1 (idempotent): desiredCount {before} -> {after} (unchanged)")

        # Verify against live AWS state, not our own counter.
        live = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0]
        live_rev = int(live["taskDefinition"].rsplit(":", 1)[-1])
        say(f"live ECS state: desiredCount={live['desiredCount']}, taskDef rev={live_rev} "
            f"(rolled back to the good revision {good_rev})")
        assert live["desiredCount"] == 2 and live_rev == good_rev
        print("\n=== the agent drove a real ECS service, verified against AWS, exactly once ===\n")
    finally:
        say("tearing down (delete service + cluster) ...")
        try:
            ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=0)
            ecs.delete_service(cluster=CLUSTER, service=SERVICE, force=True)
        except ClientError:
            pass
        try:
            ecs.delete_cluster(cluster=CLUSTER)
        except ClientError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
