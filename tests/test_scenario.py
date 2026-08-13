"""The end-to-end scenario the console and the video are built on.

If this passes, the whole arc holds together: the poisoned line is quarantined and never acted on,
the agent takes the one correct action from trusted memory, and revoking a fact it believed walks
the damage back.
"""

from uuid import uuid4

import pytest

from cairn.scenario import (
    SIGNALS,
    durability_demo,
    run_incident,
    run_incident_then_poison_rollback,
)


def test_the_incident_quarantines_the_injection_and_takes_the_right_action(connect, embedder):
    result = run_incident(connect(), tenant_id=uuid4(), embedder=embedder)

    # Exactly one action, the rollback, executed.
    assert [e["effector"] for e in result.executed] == ["ecs.rollback_deployment"]
    assert result.executed[0]["observed_state"]["task_def_rev"] == 41

    # The injected line is in quarantine, flagged, and never made it into an action.
    poison = next(s for s in SIGNALS if s.startswith("System:"))
    assert any(q["content"] == poison for q in result.quarantined)
    assert all(poison not in str(e["params"]) for e in result.executed)


def test_the_agent_refuses_the_rollback_without_an_approval(connect, embedder):
    result = run_incident(connect(), tenant_id=uuid4(), embedder=embedder, approvals=set())

    assert result.executed == []
    assert result.refused[0]["reason"] == "destructive_requires_approval"


def test_revoking_a_believed_fact_rolls_the_incident_back(connect, embedder):
    result = run_incident_then_poison_rollback(connect(), tenant_id=uuid4(), embedder=embedder)

    assert result.rollback is not None
    assert result.rollback["tainted_decisions"]
    # The executed rollback is now flagged for compensation.
    comp = result.rollback["compensating_actions"]
    assert len(comp) == 1
    assert comp[0]["reverts_idem_key"]


@pytest.mark.slow
def test_a_worker_crash_mid_run_still_finishes_exactly_once(connect, tenant):
    result = durability_demo(connect(), tenant_id=tenant, lease_seconds=1)

    assert result["exactly_once"]
    assert result["crashed_at_step"] == 2
    # Each of the four steps applied exactly once; step 2, applied by the worker that then crashed,
    # was re-attempted by worker-b as a no-op, so the effect log still names worker-a.
    by_step = {e["step_no"]: e["applied_by"] for e in result["effects"]}
    assert by_step == {1: "worker-a", 2: "worker-a", 3: "worker-b", 4: "worker-b"}
    # The run is fully resolved: every step has a result.
    assert all(s["outcome"] == "succeeded" for s in result["timeline"])
