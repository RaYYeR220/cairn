# Cairn Agent Skills

Portable [Agent Skills](https://agentskills.io/specification) that encode the two disciplines at
the core of Cairn as standalone, product-agnostic capabilities. They teach the *pattern* on
CockroachDB primitives, so any agent — not just Cairn — can adopt them. They work with any agent
framework or LLM and follow the same structure as the official
[cockroachdb-skills](https://github.com/cockroachlabs/cockroachdb-skills) collection.

| Skill | Domain | What it teaches |
|---|---|---|
| [`designing-durable-agent-execution`](cockroachdb-application-development/designing-durable-agent-execution/SKILL.md) | application development | A write-ahead intent ledger with deterministic idempotency keys and lease-with-epoch fencing under SERIALIZABLE, so a worker that crashes mid-step is resumed by another without double-applying effects. |
| [`isolating-untrusted-agent-memory`](cockroachdb-security-and-governance/isolating-untrusted-agent-memory/SKILL.md) | security & governance | Treating ingested content as untrusted evidence, with the trust tier stored as a prefix column of a vector index so quarantined memory is unreachable from retrieval rather than filtered out. |

## Install

```bash
npx skills add <this-repo>
```

Compatible with Claude Code, Cursor, Windsurf, and other agents that support the Agent Skills
specification.

## Validation

Both skills pass the official `cockroachdb-skills` spec validator (`scripts/validate-spec.py`),
frontmatter and naming conventions included.

These are proposed for upstream contribution to `cockroachlabs/cockroachdb-skills`.
