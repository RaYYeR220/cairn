"""Semantic memory with a trust boundary built into the index.

`recall` takes an explicit allowlist of tiers rather than a minimum tier. That is not a style
choice: CockroachDB accelerates a vector index only when every prefix column is constrained by
equality or an IN list. A range predicate such as `trust_tier >= 2` silently degrades to a full
scan with a post-filter - correct, but it walks the quarantined partitions on the way. An
allowlist keeps the traversal inside the tiers the caller named, and is fail-closed by
construction: a tier nobody enumerated is never visited.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

import psycopg.errors

from .embedding import Embedder
from .gate import IntegrityGate
from .trust import EMBEDDING_DIMENSIONS, Tier

__all__ = ["MemoryStore", "Memory", "Hit", "Decision", "TaintReport", "Tier"]


def _vector_literal(values: Iterable[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


@dataclass(frozen=True)
class Memory:
    mem_id: UUID
    trust_tier: Tier
    kind: str
    content: str
    content_hash: str
    source_uri: str
    source_class: str
    gate_verdict: dict[str, Any]


@dataclass(frozen=True)
class Hit:
    mem_id: UUID
    content: str
    trust_tier: Tier
    kind: str
    source_uri: str
    distance: float


@dataclass(frozen=True)
class Decision:
    committed: bool
    decision_id: UUID | None = None
    refused_reason: str | None = None


@dataclass(frozen=True)
class TaintReport:
    """What a revocation touched: the decisions it invalidated and the executed steps that

    therefore need compensating - the raw material for a rollback.
    """

    mem_id: UUID
    tainted_decisions: list[UUID]
    executed_steps: list[dict[str, Any]]
    unexecuted_steps: list[dict[str, Any]]


class MemoryStore:
    def __init__(
        self,
        conn: psycopg.Connection,
        tenant_id: UUID,
        embedder: Embedder,
        gate: IntegrityGate,
    ) -> None:
        self._conn = conn
        self._tenant_id = tenant_id
        self._embedder = embedder
        self._gate = gate

    @classmethod
    def like(cls, other: "MemoryStore", conn: psycopg.Connection) -> "MemoryStore":
        """A second store over the same tenant on a different connection - another worker."""
        return cls(conn, tenant_id=other._tenant_id, embedder=other._embedder, gate=other._gate)

    def remember(
        self,
        content: str,
        kind: str,
        source_uri: str,
        source_class: str,
        tier: Tier = Tier.RAW_EVIDENCE,
    ) -> Memory:
        """Screen, embed and store one piece of content. Never raises on hostile input."""
        verdict = self._gate.screen(content, source_class=source_class, requested_tier=tier)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        embedding = _vector_literal(self._embedder.embed(content))

        row = self._conn.execute(
            f"""
            INSERT INTO memory (tenant_id, trust_tier, kind, content, content_hash,
                                source_uri, source_class, gate_verdict, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::VECTOR({EMBEDDING_DIMENSIONS}))
            RETURNING mem_id
            """,
            (
                self._tenant_id,
                int(verdict.tier),
                kind,
                content,
                digest,
                source_uri,
                source_class,
                Jsonb(verdict.as_json()),
                embedding,
            ),
        ).fetchone()

        return Memory(
            mem_id=row[0],
            trust_tier=verdict.tier,
            kind=kind,
            content=content,
            content_hash=digest,
            source_uri=source_uri,
            source_class=source_class,
            gate_verdict=verdict.as_json(),
        )

    def _recall_query(self, query: str, tiers: set[Tier], limit: int) -> tuple[str, tuple]:
        if not tiers:
            raise ValueError(
                "recall requires an explicit allowlist of trust tiers; "
                "an empty allowlist would mean 'search everything', which is never intended"
            )

        embedding = _vector_literal(self._embedder.embed(query))
        placeholders = ", ".join(["%s"] * len(tiers))
        ordered_tiers = sorted(int(t) for t in tiers)

        sql = f"""
            SELECT mem_id, content, trust_tier, kind, source_uri,
                   embedding <=> %s::VECTOR({EMBEDDING_DIMENSIONS}) AS distance
              FROM memory
             WHERE tenant_id = %s
               AND trust_tier IN ({placeholders})
             ORDER BY embedding <=> %s::VECTOR({EMBEDDING_DIMENSIONS})
             LIMIT %s
        """
        params = (embedding, self._tenant_id, *ordered_tiers, embedding, limit)
        return sql, params

    def analyze(self) -> None:
        """Refresh table statistics, so the optimizer can see the shape of the data."""
        self._conn.execute("ANALYZE memory")

    def explain_recall(self, query: str, tiers: set[Tier], limit: int = 5) -> str:
        """The query plan a recall would run under.

        Exposed because the trust boundary is a property of the plan, not of the result set:
        this is how both the test suite and the console show that the traversal never enters a
        tier the caller did not allow.
        """
        sql, params = self._recall_query(query, tiers, limit)
        rows = self._conn.execute(f"EXPLAIN {sql}", params).fetchall()
        return "\n".join(row[0] for row in rows)

    def recall(self, query: str, tiers: set[Tier], limit: int = 5) -> list[Hit]:
        """Nearest neighbours, restricted to an explicit allowlist of trust tiers."""
        sql, params = self._recall_query(query, tiers, limit)
        rows = self._conn.execute(sql, params).fetchall()

        return [
            Hit(
                mem_id=r[0],
                content=r[1],
                trust_tier=Tier(r[2]),
                kind=r[3],
                source_uri=r[4],
                distance=float(r[5]),
            )
            for r in rows
        ]

    # -- decisions & revocation ------------------------------------------------------------

    def tier_of(self, mem_id: UUID) -> Tier:
        row = self._conn.execute(
            "SELECT trust_tier FROM memory WHERE tenant_id = %s AND mem_id = %s",
            (self._tenant_id, mem_id),
        ).fetchone()
        return Tier(row[0])

    def is_tainted(self, decision_id: UUID) -> bool:
        row = self._conn.execute(
            "SELECT tainted FROM decision WHERE tenant_id = %s AND decision_id = %s",
            (self._tenant_id, decision_id),
        ).fetchone()
        return bool(row[0])

    def decide_citing(
        self,
        mem_ids: list[UUID],
        summary: str,
        required_tiers: set[Tier],
        run_id: UUID | None = None,
    ) -> Decision:
        """Commit a decision citing evidence, atomically with a re-check of that evidence.

        The tier check and the decision write happen in one serializable transaction, so a
        concurrent revocation of any cited fact cannot slip between the read and the write: the
        cluster forces one of the two transactions to retry. On a serialization conflict we
        retry the whole thing, which re-reads the now-current tiers.
        """
        allowed = {int(t) for t in required_tiers}
        attempts = 0
        while True:
            attempts += 1
            try:
                with self._conn.transaction():
                    rows = self._conn.execute(
                        """
                        SELECT mem_id, trust_tier, revoked_at IS NOT NULL AS revoked
                          FROM memory
                         WHERE tenant_id = %s AND mem_id = ANY(%s)
                        """,
                        (self._tenant_id, list(mem_ids)),
                    ).fetchall()

                    for _mem_id, tier, revoked in rows:
                        if tier not in allowed:
                            reason = "evidence_revoked" if revoked else "cites_untrusted_evidence"
                            return Decision(committed=False, refused_reason=reason)

                    decision_id = self._conn.execute(
                        """
                        INSERT INTO decision (tenant_id, run_id, summary)
                        VALUES (%s, %s, %s) RETURNING decision_id
                        """,
                        (self._tenant_id, run_id, summary),
                    ).fetchone()[0]

                    self._conn.cursor().executemany(
                        """
                        INSERT INTO decision_evidence (tenant_id, decision_id, mem_id)
                        VALUES (%s, %s, %s)
                        """,
                        [(self._tenant_id, decision_id, m) for m in mem_ids],
                    )
                return Decision(committed=True, decision_id=decision_id)
            except psycopg.errors.SerializationFailure:
                if attempts >= 10:
                    raise
                continue

    def revoke(self, mem_id: UUID, reason: str) -> TaintReport:
        """Quarantine a fact and taint everything downstream of it.

        Runs in one serializable transaction: the tier change and the taint cascade are a single
        atomic act, so a decision that cites this fact is never observed as live after the fact
        is quarantined. The report lists the executed steps a caller must now compensate.
        """
        attempts = 0
        while True:
            attempts += 1
            try:
                with self._conn.transaction():
                    self._conn.execute(
                        """
                        UPDATE memory
                           SET trust_tier = %s, revoked_at = now(), revoked_reason = %s
                         WHERE tenant_id = %s AND mem_id = %s
                        """,
                        (int(Tier.QUARANTINED), reason, self._tenant_id, mem_id),
                    )

                    tainted = [
                        r[0]
                        for r in self._conn.execute(
                            """
                            SELECT decision_id FROM decision_evidence
                             WHERE tenant_id = %s AND mem_id = %s
                            """,
                            (self._tenant_id, mem_id),
                        ).fetchall()
                    ]

                    if tainted:
                        self._conn.execute(
                            """
                            UPDATE decision
                               SET tainted = true, taint_reason = %s
                             WHERE tenant_id = %s AND decision_id = ANY(%s)
                            """,
                            (f"cited revoked memory: {reason}", self._tenant_id, tainted),
                        )

                    executed, unexecuted = self._steps_under(tainted)
                return TaintReport(
                    mem_id=mem_id,
                    tainted_decisions=tainted,
                    executed_steps=executed,
                    unexecuted_steps=unexecuted,
                )
            except psycopg.errors.SerializationFailure:
                if attempts >= 10:
                    raise
                continue

    def _steps_under(
        self, decision_ids: list[UUID]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not decision_ids:
            return [], []
        rows = self._conn.execute(
            """
            SELECT i.idem_key, i.step_no, i.effector, i.params,
                   (r.idem_key IS NOT NULL) AS executed
              FROM step_intent AS i
              LEFT JOIN step_result AS r
                     ON r.tenant_id = i.tenant_id AND r.idem_key = i.idem_key
             WHERE i.tenant_id = %s AND i.decision_id = ANY(%s)
             ORDER BY i.step_no
            """,
            (self._tenant_id, decision_ids),
        ).fetchall()
        executed, unexecuted = [], []
        for idem_key, step_no, effector, params, was_executed in rows:
            entry = {
                "idem_key": idem_key,
                "step_no": step_no,
                "effector": effector,
                "params": params,
            }
            (executed if was_executed else unexecuted).append(entry)
        return executed, unexecuted
