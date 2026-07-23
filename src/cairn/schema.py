"""Schema for the Cairn memory layer.

Applied as a list of idempotent statements rather than a migration framework: the schema is
small, and every statement here is safe to re-run against an existing cluster.
"""

from __future__ import annotations

import psycopg

STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS agent_run (
        tenant_id  UUID        NOT NULL,
        run_id     UUID        NOT NULL DEFAULT gen_random_uuid(),
        title      STRING      NOT NULL,
        status     STRING      NOT NULL DEFAULT 'open',
        opened_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        closed_at  TIMESTAMPTZ,
        PRIMARY KEY (tenant_id, run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS step_intent (
        tenant_id        UUID        NOT NULL,
        idem_key         STRING      NOT NULL,
        run_id           UUID        NOT NULL,
        step_no          INT         NOT NULL,
        effector         STRING      NOT NULL,
        params           JSONB       NOT NULL,
        planned_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        lease_owner      STRING,
        lease_expires_at TIMESTAMPTZ,
        lease_epoch      INT         NOT NULL DEFAULT 0,
        attempts         INT         NOT NULL DEFAULT 0,
        PRIMARY KEY (tenant_id, idem_key),
        INDEX by_run (tenant_id, run_id, step_no)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS step_result (
        tenant_id      UUID        NOT NULL,
        idem_key       STRING      NOT NULL,
        outcome        STRING      NOT NULL,
        observed_state JSONB,
        lease_epoch    INT         NOT NULL,
        finished_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, idem_key)
    )
    """,
]


def apply_schema(conn: psycopg.Connection) -> None:
    """Create every Cairn table that does not already exist."""
    for statement in STATEMENTS:
        conn.execute(statement)
