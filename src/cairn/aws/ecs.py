"""Real ECS Fargate effectors.

These implement the same effector contract as the demo effectors, but the side effect is a call to
the real ECS API against a real service. Idempotency still runs through `effect_log` in
CockroachDB: an effect is applied once, keyed by the step's idempotency key, and a replayed step
reads the recorded outcome instead of calling ECS again. Verification reads the service's live state
back from AWS - never a self-reported counter.

The demo runs the service at desired count 0 (no tasks, no cost); the effector genuinely changes
that desired count and the task-definition revision on the real service.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


def _ecs(region: str = "us-east-1"):
    import boto3

    return boto3.client("ecs", region_name=region)


class _RealEcsBase:
    def __init__(self, cluster: str, region: str = "us-east-1", client=None) -> None:
        self.cluster = cluster
        self._ecs = client or _ecs(region)

    def _describe(self, service: str) -> dict[str, Any]:
        s = self._ecs.describe_services(cluster=self.cluster, services=[service])["services"][0]
        rev = s["taskDefinition"].rsplit(":", 1)[-1]
        return {"service": service, "desired_count": s["desiredCount"], "task_def_rev": int(rev)}

    def _once(self, conn, tenant_id, idem_key, worker, do) -> dict[str, Any]:
        """Apply `do()` exactly once, keyed by idem_key, recording the observed ECS state."""
        with conn.transaction():
            existing = conn.execute(
                "SELECT observed_state FROM effect_log WHERE tenant_id = %s AND idem_key = %s",
                (tenant_id, idem_key),
            ).fetchone()
            if existing is not None:
                return existing[0]
            observed = do()
            conn.execute(
                "INSERT INTO effect_log (tenant_id, idem_key, applied_by, observed_state) "
                "VALUES (%s, %s, %s, %s)",
                (tenant_id, idem_key, worker, Jsonb(observed)),
            )
            return observed


class EcsUpdateServiceEffector(_RealEcsBase):
    name = "ecs.update_service"
    destructive = False

    def apply(self, conn: psycopg.Connection, tenant_id: UUID, idem_key: str, worker: str,
              params: dict[str, Any]) -> dict[str, Any]:
        def do():
            self._ecs.update_service(cluster=self.cluster, service=params["service"],
                                     desiredCount=params["desired_count"])
            return self._describe(params["service"])
        return self._once(conn, tenant_id, idem_key, worker, do)


class EcsRollbackDeploymentEffector(_RealEcsBase):
    name = "ecs.rollback_deployment"
    destructive = True

    def apply(self, conn: psycopg.Connection, tenant_id: UUID, idem_key: str, worker: str,
              params: dict[str, Any]) -> dict[str, Any]:
        service = params["service"]
        family = params.get("family", service)
        target = params["task_definition"]

        def do():
            self._ecs.update_service(cluster=self.cluster, service=service,
                                     taskDefinition=f"{family}:{target}")
            return self._describe(service)
        return self._once(conn, tenant_id, idem_key, worker, do)


def real_registry(cluster: str, region: str = "us-east-1") -> dict[str, Any]:
    effectors = [EcsUpdateServiceEffector(cluster, region),
                 EcsRollbackDeploymentEffector(cluster, region)]
    return {e.name: e for e in effectors}
