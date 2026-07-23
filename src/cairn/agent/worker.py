"""The durable worker loop.

A worker claims the next unfinished step, applies its effect, and records the result - then
repeats until the run has no claimable work left. Every step of that is crash-safe: the intent
was committed before the effect, the effect is idempotent under the step key, and the result is
fenced by the lease epoch. A worker that dies is simply one that stopped calling this loop; a
fresh worker calling it finishes the run.

Runnable as a module so a test (or an operator) can start a worker as its own process and kill
it. Chaos hooks live behind environment variables and do nothing unless set.
"""

from __future__ import annotations

import os
import sys
import time
from uuid import UUID

import psycopg

from ..effectors import Effector, demo_registry
from ..ledger import Ledger


class _Chaos:
    """Fault injection driven by the environment; inert when unset."""

    def __init__(self) -> None:
        self.die_after_effect_on_step = _int_env("CAIRN_CHAOS_DIE_AFTER_EFFECT_ON_STEP")

    def maybe_die_after_effect(self, step_no: int) -> None:
        if self.die_after_effect_on_step == step_no:
            # Skip every cleanup path, exactly as a hard kill would.
            os._exit(137)


def _int_env(name: str) -> int | None:
    value = os.environ.get(name)
    return int(value) if value else None


def run_until_done(
    conn: psycopg.Connection,
    tenant_id: UUID,
    run_id: UUID,
    worker: str,
    registry: dict[str, Effector],
    lease_seconds: int = 30,
    chaos: _Chaos | None = None,
    poll_idle: float = 0.0,
    max_steps: int | None = None,
) -> int:
    """Drive a run to completion from this worker. Returns the number of steps it finished.

    `max_steps` stops the worker early after that many steps, which lets a caller interleave a
    fault - killing a database node, say - between steps to show the run survive it.
    """
    ledger = Ledger(conn, tenant_id=tenant_id)
    finished = 0
    while True:
        if max_steps is not None and finished >= max_steps:
            return finished
        claim = ledger.claim_next(run_id, worker=worker, lease_seconds=lease_seconds)
        if claim is None:
            if poll_idle and _has_outstanding_work(conn, tenant_id, run_id):
                time.sleep(poll_idle)
                continue
            return finished

        effector = registry[claim.intent.effector]
        observed = effector.apply(
            conn, tenant_id, claim.intent.idem_key, worker, claim.intent.params
        )
        if chaos is not None:
            chaos.maybe_die_after_effect(claim.intent.step_no)
        ledger.record_result(claim, outcome="succeeded", observed_state=observed)
        finished += 1


def _has_outstanding_work(conn: psycopg.Connection, tenant_id: UUID, run_id: UUID) -> bool:
    row = conn.execute(
        """
        SELECT count(*) FROM step_intent i
        LEFT JOIN step_result r ON r.tenant_id = i.tenant_id AND r.idem_key = i.idem_key
        WHERE i.tenant_id = %s AND i.run_id = %s AND r.idem_key IS NULL
        """,
        (tenant_id, run_id),
    ).fetchone()
    return row[0] > 0


def main() -> None:
    url = os.environ["CAIRN_DATABASE_URL"]
    tenant = UUID(os.environ["CAIRN_TENANT"])
    run = UUID(os.environ["CAIRN_RUN"])
    worker = os.environ.get("CAIRN_WORKER", "worker")
    lease = int(os.environ.get("CAIRN_LEASE_SECONDS", "30"))

    with psycopg.connect(url, autocommit=True) as conn:
        run_until_done(
            conn,
            tenant_id=tenant,
            run_id=run,
            worker=worker,
            registry=demo_registry(),
            lease_seconds=lease,
            chaos=_Chaos(),
        )


if __name__ == "__main__":
    sys.exit(main())
