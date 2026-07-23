"""cairn-watch: the reference incident-response agent.

The loop is observe -> recall -> plan -> gate -> act -> verify -> write-back. The planner (a model)
proposes; this code disposes. Every proposed step passes a gate that is enforced in code, not in a
prompt:

  * the effector must be on a server-side allowlist;
  * a destructive effector requires a standing operator approval;
  * the step's cited evidence must clear the trust floor, checked transactionally at decision time
    so a fact revoked mid-plan cannot be acted on.

A step that fails any check is refused - a first-class, recorded outcome, not an exception. The
agent never lets the planner touch an effector directly, so a compromised or manipulated planner
degrades to "proposes things that get refused", never "acts".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import psycopg

from ..effectors import Effector
from ..ledger import Ledger
from ..memory import MemoryStore, Tier
from .planner import IncidentContext, Planner, PlannedStep

#: Tiers the planner is allowed to see and cite. Raw and quarantined evidence never reach it.
TRUSTED_TIERS = {Tier.CORROBORATED, Tier.OPERATOR}


@dataclass
class StepOutcome:
    step: PlannedStep
    status: str                       # executed | refused
    reason: str | None = None         # why it was refused
    observed_state: dict | None = None


@dataclass
class RunReport:
    run_id: UUID
    summary: str
    outcomes: list[StepOutcome] = field(default_factory=list)

    @property
    def executed(self) -> list[StepOutcome]:
        return [o for o in self.outcomes if o.status == "executed"]

    @property
    def refused(self) -> list[StepOutcome]:
        return [o for o in self.outcomes if o.status == "refused"]


class IncidentAgent:
    def __init__(
        self,
        conn: psycopg.Connection,
        tenant_id: UUID,
        store: MemoryStore,
        registry: dict[str, Effector],
        planner: Planner,
        approvals: set[str] | None = None,
        worker: str = "cairn-watch",
    ) -> None:
        self._conn = conn
        self._tenant_id = tenant_id
        self._store = store
        self._registry = registry
        self._planner = planner
        self._approvals = approvals or set()  # effector names the operator has pre-approved
        self._ledger = Ledger(conn, tenant_id=tenant_id)
        self._worker = worker

    def handle(self, service: str, signals: list[str]) -> RunReport:
        # 1. Observe: every incoming signal is untrusted evidence, screened on the way in.
        for signal in signals:
            self._store.remember(
                content=signal, kind="evidence",
                source_uri=f"cloudwatch:///aws/ecs/{service}", source_class="attacker_writable",
            )

        # 2. Recall: only trusted tiers. The planner never sees quarantined or raw evidence.
        recalled = self._store.recall(
            f"incident on {service}: " + " ".join(signals[:3]), tiers=TRUSTED_TIERS, limit=8
        )

        # 3. Plan.
        plan = self._planner.plan(IncidentContext(service=service, signals=signals, recalled=recalled))

        run = self._ledger.open_run(title=f"{service}: {plan.summary}")
        report = RunReport(run_id=run, summary=plan.summary)

        # 4-6. Gate, act, verify - step by step.
        for step_no, step in enumerate(plan.steps, start=1):
            outcome = self._run_step(run, step_no, step)
            report.outcomes.append(outcome)

        # 7. Write-back: a postmortem of what was actually done, at a trusted tier for next time.
        self._writeback(service, report)
        return report

    def _run_step(self, run: UUID, step_no: int, step: PlannedStep) -> StepOutcome:
        effector = self._registry.get(step.effector)
        if effector is None:
            return StepOutcome(step, "refused", reason="effector_not_allowlisted")
        if effector.destructive and step.effector not in self._approvals:
            return StepOutcome(step, "refused", reason="destructive_requires_approval")

        # The decision cites its evidence and is committed transactionally with a trust re-check,
        # so a plan citing revoked or sub-threshold memory is refused here, atomically.
        decision = self._store.decide_citing(
            step.cites, summary=step.rationale, required_tiers=TRUSTED_TIERS, run_id=run
        )
        if not decision.committed:
            return StepOutcome(step, "refused", reason=decision.refused_reason)

        intent = self._ledger.record_intent(
            run, step_no=step_no, effector=step.effector, params=step.params,
            decision_id=decision.decision_id,
        )
        claim = self._ledger.claim_next(run, worker=self._worker, lease_seconds=30)
        observed = effector.apply(
            self._conn, self._tenant_id, intent.idem_key, self._worker, step.params
        )
        self._ledger.record_result(claim, outcome="succeeded", observed_state=observed)
        return StepOutcome(step, "executed", observed_state=observed)

    def _writeback(self, service: str, report: RunReport) -> None:
        done = ", ".join(o.step.effector for o in report.executed) or "no actions taken"
        self._store.remember(
            content=f"Incident on {service} handled: {report.summary}. Actions: {done}.",
            kind="postmortem", source_uri=f"cairn://runs/{report.run_id}",
            source_class="system", tier=Tier.CORROBORATED,
        )
