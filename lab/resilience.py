"""The hero demonstration: an agent's memory survives the region hosting it going down.

The lab cluster runs three nodes, one per simulated region, replicated so any single region can
be lost without losing quorum. A worker starts a multi-step remediation while talking to the
node in us-east-1. Midway through, we kill that node - the region the agent's memory lives in
goes dark. The worker reconnects to a surviving region, reads its ledger, which is intact, and
finishes the run. Every side effect still lands exactly once.

This is the claim the brief makes concrete: an agent whose memory is a single-node store stops
when that node dies; an agent whose memory is a distributed serializable ledger does not.

Run it against the lab cluster (see lab/docker-compose.yml):

    python lab/resilience.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from uuid import UUID, uuid4

import psycopg

sys.path.insert(0, "src")

from cairn.agent.worker import run_until_done  # noqa: E402
from cairn.effectors import demo_registry, seed_service  # noqa: E402
from cairn.ledger import Ledger  # noqa: E402
from cairn.schema import apply_schema  # noqa: E402

# libpq tries these in order, so the worker starts on us-east-1 and fails over to a survivor.
MULTI_HOST = (
    "postgresql://root@localhost:26257,localhost:26258,localhost:26259"
    "/cairn?sslmode=disable&connect_timeout=5"
)
MULTI_HOST_SYS = MULTI_HOST.replace("/cairn?", "/defaultdb?")
EAST_NODE = "cairn-crdb-east"
PLAN = [("checkout-api", n) for n in (3, 4, 5, 6, 7)]


def say(msg: str) -> None:
    print(f"  {msg}", flush=True)


def docker(*args: str) -> None:
    subprocess.run(["docker", *args], check=True, capture_output=True, text=True)


def live_nodes() -> list[int]:
    with psycopg.connect(MULTI_HOST_SYS, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT node_id FROM crdb_internal.gossip_nodes WHERE is_live ORDER BY node_id"
        ).fetchall()
    return [r[0] for r in rows]


def bootstrap() -> tuple[UUID, UUID]:
    with psycopg.connect(MULTI_HOST_SYS, autocommit=True) as conn:
        conn.execute("CREATE DATABASE IF NOT EXISTS cairn")
    tenant = uuid4()
    with psycopg.connect(MULTI_HOST, autocommit=True) as conn:
        apply_schema(conn)
        seed_service(conn, tenant, service="checkout-api", desired_count=2)
        ledger = Ledger(conn, tenant_id=tenant)
        run = ledger.open_run(title="scale checkout-api through a region failure")
        for step_no, (service, count) in enumerate(PLAN, start=1):
            ledger.record_intent(
                run, step_no=step_no, effector="ecs.update_service",
                params={"service": service, "desired_count": count},
            )
    return tenant, run


def finish_with_failover(tenant: UUID, run: UUID) -> None:
    registry = demo_registry()
    while True:
        try:
            with psycopg.connect(MULTI_HOST, autocommit=True) as conn:
                run_until_done(conn, tenant_id=tenant, run_id=run, worker="worker-1",
                               registry=registry, lease_seconds=30, poll_idle=0.5)
            return
        except psycopg.OperationalError as exc:
            say(f"lost the connection ({exc.__class__.__name__}); reconnecting to a survivor...")
            time.sleep(1.0)


def report(tenant: UUID) -> None:
    with psycopg.connect(MULTI_HOST, autocommit=True) as conn:
        effects = conn.execute(
            """
            SELECT i.step_no, e.applied_by, e.observed_state->>'desired_count'
              FROM effect_log e JOIN step_intent i
                ON i.tenant_id = e.tenant_id AND i.idem_key = e.idem_key
             WHERE e.tenant_id = %s ORDER BY i.step_no
            """,
            (tenant,),
        ).fetchall()
        final = conn.execute(
            "SELECT desired_count FROM service_state WHERE tenant_id = %s AND service = %s",
            (tenant, "checkout-api"),
        ).fetchone()[0]
    say(f"effects applied ({len(effects)}, one per step, none duplicated):")
    for step_no, by, count in effects:
        say(f"    step {step_no}: desired_count -> {count}  (applied by {by})")
    say(f"final desired_count = {final}  (planned end state = {PLAN[-1][1]})")
    assert len(effects) == len(PLAN) and final == PLAN[-1][1]


def main() -> int:
    print("\n=== Cairn resilience lab: memory survives a region failure ===\n")
    say(f"cluster is live on nodes {live_nodes()} (us-east-1, us-west-2, eu-west-1)")

    tenant, run = bootstrap()
    say(f"planned a 5-step remediation for run {str(run)[:8]}")

    say("worker starts on us-east-1 and applies the first step...")
    with psycopg.connect(MULTI_HOST, autocommit=True) as conn:
        run_until_done(conn, tenant_id=tenant, run_id=run, worker="worker-1",
                       registry=demo_registry(), lease_seconds=30, max_steps=1)

    say(f"KILLING us-east-1 ({EAST_NODE}) mid-remediation - the region the memory lives in")
    docker("kill", EAST_NODE)
    time.sleep(2)
    say(f"surviving nodes: {live_nodes()} - quorum holds")

    say("worker reconnects to a survivor and finishes from the ledger...")
    finish_with_failover(tenant, run)

    report(tenant)

    say(f"restarting {EAST_NODE} so it rejoins the cluster")
    docker("start", EAST_NODE)
    print("\n=== the run completed through the failure, exactly once ===\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
