# For reviewers — a 5-minute path

Everything here runs against a **real CockroachDB cluster**. Nothing is mocked where a real
integration exists. Pick the depth you have time for.

## 60 seconds — see the whole story

```bash
docker compose -f docker-compose.demo.yml up --build      # single-node CockroachDB + console
```

Open **http://localhost:8080** and press **Run incident**. You will watch, top to bottom:

1. A CloudWatch-style telemetry burst arrives. One line is not a signal — it's an instruction
   smuggled in through a writable log. It is flagged **writable source**.
2. **The gate quarantines it** — and shows you *which detectors fired*. The three real signals pass.
3. **The durable ledger** lays down one stone: the agent's single approved action, committed before
   it ran, with its observed result.
4. **A trusted fact turns out to be a lie.** Revoking it taints the decision downstream and proposes
   a compensating action — the rollback is a query over provenance.
5. **The graded scorecard**, misses included.

The hosted version is the same page, one click away: **https://cairn-console.onrender.com**
*(free tier — the first request after idle may take ~30s to wake)*.

## 3 minutes — run the proofs

```bash
docker compose -f lab/docker-compose.yml up -d            # 3 nodes, one per simulated region
uv venv && uv pip install -e ".[dev,mcp,embed]"

uv run pytest -q                                          # 39 tests, live cluster
uv run python lab/proofs.py                               # 3 graded tracks → PROOFS.md (gate · durability · invariant)
uv run python lab/compare_pgvector.py                     # CockroachDB vs pgvector → COMPARISON.md
uv run python lab/resilience.py                           # kill a region mid-run; watch it finish exactly once
```

- **`lab/resilience.py`** is the hero: it runs a multi-step remediation, kills the CockroachDB node
  standing in for `us-east-1` partway through, and shows the worker reconnect to a surviving region,
  read its intact ledger, and finish — every effect applied exactly once. This is the claim a
  single-node store cannot make.
- **`tests/test_chaos.py`** proves exactly-once the hard way: it kills the first worker with a real
  `os._exit` in the window between the side effect and recording it, then resumes with a second
  worker and asserts each effect landed once.

## Where to look — the three CockroachDB mechanisms

| Claim | File | What to read |
|---|---|---|
| Quarantine is unreachable, not filtered | `tests/test_retrieval_plan.py` | asserts `EXPLAIN` shows a vector-index search scoped to the allowed tiers, one prefix span each |
| Exactly-once under crash | `tests/test_chaos.py`, `src/cairn/ledger.py` | lease + epoch fencing; effect keyed by idempotency key |
| No decision on revoked evidence | `tests/test_invariant.py`, `src/cairn/memory.py` | serializable race, 25 rounds, both orderings |
| Poison rollback | `tests/test_lineage.py`, `src/cairn/rollback.py` | lineage walk → taint → compensating actions |

## The CockroachDB tools, live

- **Vector Indexing** — the mechanism above (`src/cairn/schema.py`, `src/cairn/memory.py`).
- **Managed MCP Server** — `python lab/managed_mcp_inspect.py` reads live agent memory over
  `cockroachlabs.cloud/mcp` and shows a write being rejected (read-only by default). Needs a
  service-account key; see `docs/managed-mcp.md`.
- **Agent Skills** — `skills/`, plus upstream PR
  [cockroachlabs/cockroachdb-skills#22](https://github.com/cockroachlabs/cockroachdb-skills/pull/22).

## Honesty

`README.md` has an explicit **Honest limitations** section: what is live, what the reproducible demo
simulates for zero-credential replay, and where the deterministic gate stops and the model
second-opinion begins. The eval publishes its misses. We would rather you trust the numbers.
