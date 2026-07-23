"""cairn-mcp: a read-only MCP window into an agent's memory.

Any MCP-capable client - Claude Code, Cursor, another agent - can point at this server to see what
an agent remembers, at what trust level, what was quarantined and why, and how a run unfolded. It
is read-only by design: it exposes the reasoning surface without a path to mutate it, mirroring the
"safe by default" posture of a managed database MCP endpoint.

The retrieval tool shows the trust boundary in action: it returns not just the hits but the query
plan, so a caller can confirm the search was scoped to the allowed tiers and never entered
quarantine.

Run it (stdio transport):

    CAIRN_DATABASE_URL=postgresql://... CAIRN_TENANT=<uuid> cairn-mcp
"""

from __future__ import annotations

import os
from uuid import UUID

import psycopg

from .embedding import DeterministicEmbedder
from .gate import IntegrityGate
from .memory import MemoryStore, Tier
from .reader import MemoryReader


def build_server(conn_factory, tenant_id: UUID, embedder=None):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("cairn-memory")
    embedder = embedder or DeterministicEmbedder()

    def _store(conn):
        return MemoryStore(conn, tenant_id=tenant_id, embedder=embedder, gate=IntegrityGate())

    @mcp.tool()
    def recall(query: str, tiers: list[int], limit: int = 5) -> dict:
        """Search agent memory, restricted to an explicit allowlist of trust tiers.

        tiers is an allowlist (e.g. [2, 3] for corroborated and operator memory). Quarantined
        memory (tier 0) is only ever returned if 0 is named explicitly. Returns the hits and the
        query plan, which shows the search was served by the vector index scoped to those tiers.
        """
        allow = {Tier(t) for t in tiers}
        with conn_factory() as conn:
            store = _store(conn)
            hits = store.recall(query, tiers=allow, limit=limit)
            plan = store.explain_recall(query, tiers=allow, limit=limit)
        return {
            "hits": [
                {"mem_id": str(h.mem_id), "content": h.content, "trust_tier": int(h.trust_tier),
                 "kind": h.kind, "source_uri": h.source_uri, "distance": round(h.distance, 4)}
                for h in hits
            ],
            "plan": plan,
        }

    @mcp.tool()
    def list_quarantine(limit: int = 50) -> list[dict]:
        """List memory that was quarantined on ingest, with the detector that caught it."""
        with conn_factory() as conn:
            return MemoryReader(conn, tenant_id).quarantine(limit=limit)

    @mcp.tool()
    def inspect_memory(mem_id: str) -> dict | None:
        """Show one memory row in full: provenance, trust tier, hash, and gate verdict."""
        with conn_factory() as conn:
            return MemoryReader(conn, tenant_id).memory_row(UUID(mem_id))

    @mcp.tool()
    def run_timeline(run_id: str) -> list[dict]:
        """Show a run as a sequence of intents and their results - the execution ledger."""
        with conn_factory() as conn:
            return MemoryReader(conn, tenant_id).run_timeline(UUID(run_id))

    @mcp.tool()
    def memory_overview() -> dict:
        """Count memory rows by trust tier."""
        with conn_factory() as conn:
            counts = MemoryReader(conn, tenant_id).tier_counts()
        labels = {0: "quarantined", 1: "raw_evidence", 2: "corroborated", 3: "operator"}
        return {labels.get(t, str(t)): c for t, c in sorted(counts.items())}

    return mcp


def main() -> None:
    url = os.environ["CAIRN_DATABASE_URL"]
    tenant = UUID(os.environ["CAIRN_TENANT"])

    def conn_factory():
        return psycopg.connect(url, autocommit=True)

    build_server(conn_factory, tenant).run()


if __name__ == "__main__":
    main()
