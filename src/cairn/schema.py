"""Schema for the Cairn memory layer.

Applied as a list of idempotent statements rather than a migration framework: the schema is
small, and every statement here is safe to re-run against an existing cluster.
"""

from __future__ import annotations

import psycopg

from .trust import EMBEDDING_DIMENSIONS

STATEMENTS: list[str] = [
    f"""
    CREATE TABLE IF NOT EXISTS memory (
        tenant_id    UUID        NOT NULL,
        mem_id       UUID        NOT NULL DEFAULT gen_random_uuid(),
        trust_tier   INT2        NOT NULL,
        kind         STRING      NOT NULL,
        content      STRING      NOT NULL,
        content_hash STRING      NOT NULL,
        source_uri   STRING      NOT NULL,
        source_class STRING      NOT NULL,
        ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        gate_verdict JSONB       NOT NULL,
        revoked_at   TIMESTAMPTZ,
        revoked_reason STRING,
        embedding    VECTOR({EMBEDDING_DIMENSIONS}),
        PRIMARY KEY (tenant_id, mem_id)
    )
    """,
    # trust_tier sits between tenant_id and the embedding on purpose: as a prefix column it
    # scopes the approximate-nearest-neighbour traversal to the tiers a query names, so
    # quarantined memory is not filtered out of a result set - it is never visited.
    """
    CREATE VECTOR INDEX IF NOT EXISTS mem_vec
        ON memory (tenant_id, trust_tier, embedding vector_cosine_ops)
    """,
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
        decision_id      UUID,
        PRIMARY KEY (tenant_id, idem_key),
        INDEX by_run (tenant_id, run_id, step_no),
        INDEX by_decision (tenant_id, decision_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decision (
        tenant_id    UUID        NOT NULL,
        decision_id  UUID        NOT NULL DEFAULT gen_random_uuid(),
        run_id       UUID,
        summary      STRING      NOT NULL,
        tainted      BOOL        NOT NULL DEFAULT false,
        taint_reason STRING,
        decided_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, decision_id),
        INDEX by_run (tenant_id, run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decision_evidence (
        tenant_id   UUID NOT NULL,
        decision_id UUID NOT NULL,
        mem_id      UUID NOT NULL,
        PRIMARY KEY (tenant_id, decision_id, mem_id),
        INDEX by_mem (tenant_id, mem_id)
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


def enable_vector_indexes(conn: psycopg.Connection) -> bool:
    """Turn on vector indexing if this cluster lets us.

    Self-hosted clusters need the setting flipped once; managed clusters may reserve it, in
    which case it is already on or the operator has to enable it. Either way the caller only
    needs to know whether to expect an accelerated index.
    """
    if not conn.autocommit:
        # SET CLUSTER SETTING cannot run inside a multi-statement transaction, and attempting
        # it would poison the caller's transaction rather than fail on its own.
        return False
    try:
        conn.execute("SET CLUSTER SETTING feature.vector_index.enabled = true")
        return True
    except psycopg.Error:
        return False


def apply_schema(conn: psycopg.Connection) -> None:
    """Create every Cairn table and index that does not already exist.

    Pass an autocommit connection: enabling vector indexing is a cluster setting, which cannot
    be issued inside a transaction.
    """
    enable_vector_indexes(conn)
    for statement in STATEMENTS:
        conn.execute(statement)
