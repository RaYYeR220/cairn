# Proofs

_Reproduce: `python lab/proofs.py` against a CockroachDB cluster. Every number below is measured,
not asserted; failures would show here too._

## Track A — the integrity gate (held-out corpus, deterministic detectors only)

- Attacks caught: **27/33** (recall 82%)
- False positives on benign lines: **0/16** (0%)
- Misses (all pure semantic paraphrase, the model second-opinion's job): evade-1, evade-2, evade-3, evade-4, evade-7, evade-8

## Track B — durability under a mid-run worker crash

- Trials: **15** · exactly-once with correct attribution: **15/15**
- Each trial applies a step's effect, kills the worker before it records the result, and resumes
  with a second worker. Pass = every effect applied once and the crashed step still bears the
  first worker's name.

## Track C — the consistency invariant (revocation raced against a decision)

- Trials: **30** · invariant held: **30/30**
- Pass = no untainted decision is left citing a fact a concurrent transaction revoked, on either
  serial ordering. This is the property that requires the vector index and the operational tables
  to live in one serializable transaction.
