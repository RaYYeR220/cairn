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

from .embedding import Embedder
from .gate import IntegrityGate
from .trust import EMBEDDING_DIMENSIONS, Tier

__all__ = ["MemoryStore", "Memory", "Hit", "Tier"]


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
