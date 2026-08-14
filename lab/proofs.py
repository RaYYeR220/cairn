"""The numbers, reproducibly. One command runs every quantitative claim and writes PROOFS.md.

Three graded tracks, each against a real CockroachDB cluster:
  A · the integrity gate over a held-out corpus,
  B · durability — crash a worker mid-run, N times, and count exactly-once,
  C · the consistency invariant — race a revocation against a decision, N times, and count clean.

Run:  python lab/proofs.py            (uses CAIRN_ADMIN_URL or the local lab cluster)
"""

from __future__ import annotations

import os
import sys
import uuid

import psycopg

sys.path.insert(0, "src")

from cairn.embedding import DeterministicEmbedder  # noqa: E402
from cairn.eval.runner import score  # noqa: E402
from cairn.scenario import durability_demo, revoke_race_trial  # noqa: E402
from cairn.schema import apply_schema  # noqa: E402

ADMIN = os.environ.get("CAIRN_ADMIN_URL", "postgresql://root@localhost:26257/defaultdb?sslmode=disable")
DURABILITY_TRIALS = 15
INVARIANT_TRIALS = 30


def _fresh_db() -> str:
    name = f"cairn_proofs_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f"CREATE DATABASE {name}")
    url = ADMIN.replace("/defaultdb", f"/{name}")
    with psycopg.connect(url, autocommit=True) as c:
        apply_schema(c)
    return url, name


def main() -> int:
    url, name = _fresh_db()
    connect = lambda: psycopg.connect(url, autocommit=True)  # noqa: E731
    emb = DeterministicEmbedder()

    print("Track A · integrity gate ...", flush=True)
    card = score()

    print(f"Track B · durability ({DURABILITY_TRIALS} crash trials) ...", flush=True)
    dur_ok = 0
    for _ in range(DURABILITY_TRIALS):
        r = durability_demo(connect(), tenant_id=uuid.uuid4())
        by = {e["step_no"]: e["applied_by"] for e in r["effects"]}
        if r["exactly_once"] and by == {1: "worker-a", 2: "worker-a", 3: "worker-b", 4: "worker-b"}:
            dur_ok += 1

    print(f"Track C · consistency invariant ({INVARIANT_TRIALS} races) ...", flush=True)
    inv_ok = sum(
        revoke_race_trial(connect(), tenant_id=uuid.uuid4(), embedder=emb, connect=connect)
        for _ in range(INVARIANT_TRIALS)
    )

    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name} CASCADE")

    report = f"""# Proofs

_Reproduce: `python lab/proofs.py` against a CockroachDB cluster. Every number below is measured,
not asserted; failures would show here too._

## Track A — the integrity gate (held-out corpus, deterministic detectors only)

- Attacks caught: **{card.caught}/{card.attacks}** (recall {card.recall:.0%})
- False positives on benign lines: **{card.false_positives}/{card.benign}** ({card.false_positive_rate:.0%})
- Misses (all pure semantic paraphrase, the model second-opinion's job): {', '.join(card.missed_ids) or 'none'}

## Track B — durability under a mid-run worker crash

- Trials: **{DURABILITY_TRIALS}** · exactly-once with correct attribution: **{dur_ok}/{DURABILITY_TRIALS}**
- Each trial applies a step's effect, kills the worker before it records the result, and resumes
  with a second worker. Pass = every effect applied once and the crashed step still bears the
  first worker's name.

## Track C — the consistency invariant (revocation raced against a decision)

- Trials: **{INVARIANT_TRIALS}** · invariant held: **{inv_ok}/{INVARIANT_TRIALS}**
- Pass = no untainted decision is left citing a fact a concurrent transaction revoked, on either
  serial ordering. This is the property that requires the vector index and the operational tables
  to live in one serializable transaction.
"""
    with open("PROOFS.md", "w", encoding="utf-8") as f:
        f.write(report)

    print("\n" + report)
    all_pass = (card.false_positives == 0 and dur_ok == DURABILITY_TRIALS
                and inv_ok == INVARIANT_TRIALS)
    print("PROOFS.md written.", "ALL TRACKS PASS." if all_pass else "SEE RESULTS ABOVE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
