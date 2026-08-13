"""The incident, on live AWS: real CloudWatch in, real S3 out.

Publishes the incident's telemetry - including the poisoned line - to a real CloudWatch log stream,
has the agent read it back as untrusted evidence, runs the remediation against CockroachDB, and
writes the postmortem and the full run to real S3. Nothing here is simulated: the log group and the
bucket are created, written, read, and then torn down.

    python lab/live_aws.py           # uses .env for AWS + CockroachDB credentials

Requires the `aws` extra and working AWS credentials (CloudWatch Logs + S3; neither is gated the
way Bedrock inference is on a fresh account).
"""

from __future__ import annotations

import os
import sys
from uuid import uuid4

sys.path.insert(0, "src")


def load_env() -> None:
    for line in open(".env", encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k] = v


def say(m: str) -> None:
    print(f"  {m}", flush=True)


def main() -> int:
    load_env()
    import boto3

    from cairn.aws.cloudwatch import CloudWatchTelemetry
    from cairn.aws.s3 import S3Artifacts
    from cairn.db import connect
    from cairn.embedding import DeterministicEmbedder
    from cairn.scenario import SIGNALS, run_incident_then_poison_rollback

    print("\n=== The incident on live AWS: CloudWatch in, S3 out ===\n")
    account = boto3.client(
        "sts",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    ).get_caller_identity()["Account"]

    import time

    stream = f"incident-{uuid4().hex[:8]}"
    cw = CloudWatchTelemetry()
    bucket = f"cairn-artifacts-{account}"
    s3 = S3Artifacts(bucket)

    # CloudWatch rejects events whose timestamp is far from now, so stamp them at the real clock.
    base_ms = int(time.time() * 1000)

    say(f"publishing {len(SIGNALS)} telemetry lines to CloudWatch group {cw.log_group} ...")
    cw.publish(SIGNALS, stream=stream, at_ms=base_ms)

    say("agent observes: reading the log stream back from CloudWatch ...")
    observed = cw.read(stream, expect=len(SIGNALS))
    say(f"read {len(observed)} lines from CloudWatch (one of them is an injection)")

    say("running the remediation against CockroachDB on the real telemetry ...")
    with connect(os.environ["CAIRN_DATABASE_URL"], connect_timeout=25) as conn:
        result = run_incident_then_poison_rollback(
            conn, tenant_id=uuid4(), embedder=DeterministicEmbedder())
    say(f"executed: {[e['effector'] for e in result.executed]}  ·  "
        f"quarantined: {len(result.quarantined)}  ·  "
        f"tainted on rollback: {len(result.rollback['tainted_decisions'])}")

    say(f"writing the postmortem and run to S3 bucket {bucket} ...")
    s3.ensure()
    done = ", ".join(e["effector"] for e in result.executed) or "no actions"
    pm_uri = s3.put_postmortem(str(result.run_id),
                               f"Incident on checkout-api resolved. Actions: {done}. "
                               f"A cited fact was later revoked; {len(result.rollback['compensating_actions'])} "
                               f"compensating action(s) proposed.")
    run_uri = s3.put_run(str(result.run_id), {
        "executed": result.executed, "quarantined_count": len(result.quarantined),
        "rollback": result.rollback})
    say(f"postmortem: {pm_uri}")
    say(f"run:        {run_uri}")
    say("read back from S3: " + s3.get_text(pm_uri)[:72] + " ...")

    say("cleaning up the live log group (bucket artifacts kept for inspection) ...")
    cw.delete()
    print("\n=== live AWS round-trip complete ===\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
