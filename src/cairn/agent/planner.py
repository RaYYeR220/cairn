"""The planner interface and its types.

The planner turns an incident - untrusted signals plus trusted recalled memory - into a proposed
plan. It is the one place a language model enters the loop, and it is deliberately powerless: it
proposes, it never executes. Everything it proposes is re-checked by the agent against
code-enforced rules before anything happens, so a compromised or mistaken planner cannot act.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from ..memory import Hit


@dataclass(frozen=True)
class IncidentContext:
    service: str
    signals: list[str]           # raw, untrusted telemetry for this incident
    recalled: list[Hit]          # trusted memory the agent retrieved (tier-allowlisted)


@dataclass(frozen=True)
class PlannedStep:
    effector: str
    params: dict
    rationale: str
    cites: list[UUID] = field(default_factory=list)   # memory this step is justified by


@dataclass(frozen=True)
class Plan:
    summary: str
    steps: list[PlannedStep]


class Planner(Protocol):
    def plan(self, incident: IncidentContext) -> Plan:
        ...


class StubPlanner:
    """A fixed planner for tests: returns whatever plan it was constructed with, ignoring input.

    Lets the agent loop be exercised deterministically without a model, which is also how the
    'a malicious plan is refused' cases are set up - the stub stands in for a compromised planner.
    """

    def __init__(self, plan: Plan) -> None:
        self._plan = plan

    def plan(self, incident: IncidentContext) -> Plan:
        return self._plan
