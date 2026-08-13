"""The console backend.

A small read-mostly API over the memory layer. Its one write path runs the reference incident on
a fresh tenant and returns everything needed to render it: the telemetry burst with the poisoned
line marked, the gate's quarantine decisions, the plan, the durable ledger, and the rollback. It
also serves the graded evaluation and the static single-page frontend.

Run it:

    CAIRN_DATABASE_URL=postgresql://... cairn-console      # or defaults to the local lab cluster
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from .db import connect
from .embedding import make_embedder
from .eval.runner import score
from .scenario import RUNBOOKS, SIGNALS, durability_demo, run_incident_then_poison_rollback

_STATIC = Path(__file__).resolve().parent.parent.parent / "console" / "static"


def _database_url() -> str:
    return os.environ.get(
        "CAIRN_DATABASE_URL", "postgresql://root@localhost:26257/cairn?sslmode=disable"
    )


def _ensure_schema() -> None:
    from .schema import apply_schema

    url = _database_url()
    admin = url.replace("/cairn?", "/defaultdb?")
    with connect(admin) as c:
        c.execute("CREATE DATABASE IF NOT EXISTS cairn")
    with connect(url) as c:
        apply_schema(c)


def create_app():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="Cairn console", version="0.1.0")
    embedder = make_embedder()

    @app.get("/api/health")
    def health():
        return {"ok": True, "embedder": type(embedder).__name__, "dims": embedder.dimensions}

    @app.get("/api/signals")
    def signals():
        """The scripted incident's inputs, so the UI can show what arrives before the agent acts."""
        poison = next(s for s in SIGNALS if s.startswith("System:"))
        return {
            "service": "checkout-api",
            "signals": [{"text": s, "poisoned": s == poison} for s in SIGNALS],
            "runbooks": [{"text": c, "kind": k, "tier": int(t)} for c, k, t in RUNBOOKS],
        }

    @app.post("/api/run")
    def run():
        """Run the full incident + poison-rollback for a fresh tenant; return the whole arc."""
        with connect(_database_url()) as conn:
            result = run_incident_then_poison_rollback(conn, tenant_id=uuid4(), embedder=embedder)
        return JSONResponse({
            "run_id": str(result.run_id),
            "tenant_id": str(result.tenant_id),
            "executed": result.executed,
            "refused": result.refused,
            "quarantined": result.quarantined,
            "timeline": result.timeline,
            "rollback": result.rollback,
        })

    @app.post("/api/durability")
    def durability():
        """Crash a worker mid-run and show the run finish exactly once."""
        from uuid import uuid4

        with connect(_database_url()) as conn:
            return durability_demo(conn, tenant_id=uuid4())

    @app.get("/api/eval")
    def evaluation():
        card = score()
        return {
            "attacks": card.attacks, "caught": card.caught, "missed": card.missed,
            "recall": round(card.recall, 3), "benign": card.benign,
            "false_positives": card.false_positives,
            "false_positive_rate": round(card.false_positive_rate, 3),
            "per_family": card.per_family, "missed_ids": card.missed_ids,
        }

    if _STATIC.is_dir():
        app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")

    return app


def main() -> None:
    import uvicorn

    _ensure_schema()
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
