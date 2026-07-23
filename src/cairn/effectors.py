"""Effectors: the only code that touches the outside world.

An effector is idempotent under the step's idempotency key. It records what it did in
`effect_log`, keyed by that key, so a replayed step - one whose worker died before recording a
result - finds its effect already applied and returns the same observed state instead of
applying it twice. That is the contract that makes the write-ahead ledger safe: the ledger
guarantees an intent exists before the effect; the effector guarantees the effect happens once.

The demo effector simulates an ECS service. In the deployed build it is replaced by one that
calls the real ECS API, keeping the same key-first contract.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


class Effector(Protocol):
    name: str

    def apply(
        self, conn: psycopg.Connection, tenant_id: UUID, idem_key: str, worker: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        ...


def seed_service(
    conn: psycopg.Connection, tenant_id: UUID, service: str, desired_count: int
) -> None:
    conn.execute(
        """
        INSERT INTO service_state (tenant_id, service, desired_count)
        VALUES (%s, %s, %s)
        ON CONFLICT (tenant_id, service) DO UPDATE SET desired_count = excluded.desired_count
        """,
        (tenant_id, service, desired_count),
    )


class EcsUpdateServiceEffector:
    """Sets a service's desired count. Idempotent under the step key."""

    name = "ecs.update_service"

    def apply(self, conn, tenant_id, idem_key, worker, params):
        with conn.transaction():
            existing = conn.execute(
                "SELECT observed_state FROM effect_log WHERE tenant_id = %s AND idem_key = %s",
                (tenant_id, idem_key),
            ).fetchone()
            if existing is not None:
                return existing[0]  # already applied by someone (maybe a worker that then died)

            row = conn.execute(
                """
                UPDATE service_state SET desired_count = %s
                 WHERE tenant_id = %s AND service = %s
                RETURNING desired_count, task_def_rev
                """,
                (params["desired_count"], tenant_id, params["service"]),
            ).fetchone()
            observed = {
                "service": params["service"],
                "desired_count": row[0],
                "task_def_rev": row[1],
            }
            conn.execute(
                """
                INSERT INTO effect_log (tenant_id, idem_key, applied_by, observed_state)
                VALUES (%s, %s, %s, %s)
                """,
                (tenant_id, idem_key, worker, Jsonb(observed)),
            )
            return observed


def demo_registry() -> dict[str, Effector]:
    effectors = [EcsUpdateServiceEffector()]
    return {e.name: e for e in effectors}
