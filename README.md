# Cairn

**Transactional, provenance-stamped memory for agents that take actions.**

A cairn is a stack of stones marking a trail — each one placed so you can find your way back.
That is what this library does for an agent: every step it takes becomes a durable marker you can
walk back along, and every fact it learns carries a record of where it came from.

> Status: in development.

## Why

An agent that only talks can forget safely. An agent that *acts* cannot. Two things go wrong in
production, and both are memory problems:

1. **The memory dies with the incident.** An agent remediating a failing service keeps its plan and
   its progress somewhere inside the same blast radius. When the worker dies mid-run, the run is
   either abandoned half-applied or replayed into duplicate side effects.
2. **The memory is attacker-writable.** An agent that ingests logs, alerts and tickets into
   long-term memory has handed every party who can write to those sources a write path into its own
   future context. Poison once, influence every run afterwards.

Cairn treats both as first-class: a write-ahead intent ledger under serializable isolation, and a
retrieval path where untrusted content is not filtered out — it is unreachable.

## Documentation

See [`docs/`](docs/) once published. Local development notes live in [`lab/`](lab/).

## License

MIT — see [LICENSE](LICENSE).
