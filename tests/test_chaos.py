"""The durability proof: a worker dies in the worst possible window and the run still applies

each side effect exactly once.

The dangerous window is the gap between applying a side effect and recording that it happened.
A worker killed there has changed the outside world but left no record of it. Naive replay
double-applies; abandoning the run leaves it half-done. Cairn survives it because the effect is
idempotent under the step's key and the ledger lets a fresh worker resume.

The kill here is real: the first worker runs in a subprocess and calls `os._exit`, skipping every
cleanup path, exactly as a `kill -9` or a lost Lambda would. A second worker then resumes.
"""

import os
import subprocess
import sys
import time
from uuid import UUID

import psycopg
import pytest

from cairn.agent.worker import run_until_done
from cairn.effectors import demo_registry, seed_service
from cairn.ledger import Ledger

pytestmark = pytest.mark.slow

WORKER = [sys.executable, "-m", "cairn.agent.worker"]


def _plan_three_scaling_steps(ledger: Ledger, run: UUID) -> None:
    for step_no, count in enumerate([4, 6, 8], start=1):
        ledger.record_intent(
            run,
            step_no=step_no,
            effector="ecs.update_service",
            params={"service": "checkout-api", "desired_count": count},
        )


def test_a_worker_killed_between_effect_and_result_still_applies_each_step_once(
    test_database, connect, tenant
):
    admin = connect()
    ledger = Ledger(admin, tenant_id=tenant)
    seed_service(admin, tenant, service="checkout-api", desired_count=2)
    run = ledger.open_run(title="scale checkout-api up in three steps")
    _plan_three_scaling_steps(ledger, run)

    # Worker A hard-dies right after applying the effect for step 2, before recording its result.
    env = {
        **os.environ,
        "CAIRN_DATABASE_URL": test_database,
        "CAIRN_TENANT": str(tenant),
        "CAIRN_RUN": str(run),
        "CAIRN_WORKER": "worker-a",
        "CAIRN_LEASE_SECONDS": "2",
        "CAIRN_CHAOS_DIE_AFTER_EFFECT_ON_STEP": "2",
    }
    dead = subprocess.run(WORKER, env=env, capture_output=True, text=True, timeout=60)
    assert dead.returncode == 137, dead.stderr

    # Worker B resumes once the dead worker's lease lapses.
    time.sleep(2.5)
    run_until_done(
        connect(),
        tenant_id=tenant,
        run_id=run,
        worker="worker-b",
        registry=demo_registry(),
        lease_seconds=30,
    )

    # Each step's effect landed exactly once. Step 2, applied by the worker that then died, was
    # re-attempted by worker-b - and that re-attempt was a no-op, so the record still names A.
    rows = admin.execute(
        """
        SELECT i.step_no, e.applied_by
          FROM effect_log AS e
          JOIN step_intent AS i ON i.tenant_id = e.tenant_id AND i.idem_key = e.idem_key
         WHERE e.tenant_id = %s
         ORDER BY i.step_no
        """,
        (tenant,),
    ).fetchall()
    assert rows == [(1, "worker-a"), (2, "worker-a"), (3, "worker-b")]

    # The external world reflects the final intended state, and every step has a result.
    final = admin.execute(
        "SELECT desired_count FROM service_state WHERE tenant_id = %s AND service = %s",
        (tenant, "checkout-api"),
    ).fetchone()
    assert final[0] == 8

    incomplete = admin.execute(
        """
        SELECT count(*) FROM step_intent i
        LEFT JOIN step_result r ON r.tenant_id = i.tenant_id AND r.idem_key = i.idem_key
        WHERE i.tenant_id = %s AND r.idem_key IS NULL
        """,
        (tenant,),
    ).fetchone()
    assert incomplete[0] == 0
