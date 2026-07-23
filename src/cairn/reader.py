"""Read-only views over Cairn memory.

These are the queries a human operator or another agent needs to understand what an agent
remembers and why it acted: what is in memory and at what trust, what was quarantined and by which
detector, and how a run unfolded. They are deliberately separate from the write paths so they can
be exposed over a read-only surface (an MCP server, a dashboard) without any risk of mutation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg


class MemoryReader:
    def __init__(self, conn: psycopg.Connection, tenant_id: UUID) -> None:
        self._conn = conn
        self._tenant_id = tenant_id

    def quarantine(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT mem_id, kind, source_uri, source_class, content,
                   gate_verdict->>'reason' AS reason, gate_verdict->'detectors' AS detectors,
                   revoked_reason
              FROM memory
             WHERE tenant_id = %s AND trust_tier = 0
             ORDER BY ingested_at DESC
             LIMIT %s
            """,
            (self._tenant_id, limit),
        ).fetchall()
        return [
            {
                "mem_id": str(r[0]),
                "kind": r[1],
                "source_uri": r[2],
                "source_class": r[3],
                "content": r[4],
                "reason": r[5],
                "detectors": r[6],
                "revoked_reason": r[7],
            }
            for r in rows
        ]

    def memory_row(self, mem_id: UUID) -> dict[str, Any] | None:
        r = self._conn.execute(
            """
            SELECT mem_id, trust_tier, kind, content, content_hash, source_uri, source_class,
                   ingested_at, gate_verdict, revoked_at, revoked_reason
              FROM memory WHERE tenant_id = %s AND mem_id = %s
            """,
            (self._tenant_id, mem_id),
        ).fetchone()
        if r is None:
            return None
        return {
            "mem_id": str(r[0]), "trust_tier": r[1], "kind": r[2], "content": r[3],
            "content_hash": r[4], "source_uri": r[5], "source_class": r[6],
            "ingested_at": r[7].isoformat(), "gate_verdict": r[8],
            "revoked_at": r[9].isoformat() if r[9] else None, "revoked_reason": r[10],
        }

    def run_timeline(self, run_id: UUID) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT i.step_no, i.effector, i.params, i.decision_id,
                   i.lease_owner, i.attempts,
                   r.outcome, r.observed_state, r.finished_at
              FROM step_intent i
              LEFT JOIN step_result r
                     ON r.tenant_id = i.tenant_id AND r.idem_key = i.idem_key
             WHERE i.tenant_id = %s AND i.run_id = %s
             ORDER BY i.step_no
            """,
            (self._tenant_id, run_id),
        ).fetchall()
        return [
            {
                "step_no": r[0], "effector": r[1], "params": r[2],
                "decision_id": str(r[3]) if r[3] else None,
                "lease_owner": r[4], "attempts": r[5],
                "outcome": r[6] or "pending", "observed_state": r[7],
                "finished_at": r[8].isoformat() if r[8] else None,
            }
            for r in rows
        ]

    def tier_counts(self) -> dict[int, int]:
        rows = self._conn.execute(
            "SELECT trust_tier, count(*) FROM memory WHERE tenant_id = %s GROUP BY trust_tier",
            (self._tenant_id,),
        ).fetchall()
        return {int(t): int(c) for t, c in rows}
