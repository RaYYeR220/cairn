"""Poison rollback.

When a fact turns out to be a lie, the question is not just "quarantine it" but "what did we
already do because we believed it?". Because memory is a transactional ledger with provenance,
that is a query: walk from the revoked fact to the decisions that cited it and on to the steps
those decisions drove, and separate what was merely planned from what actually ran and now needs
undoing.
"""

from cairn.memory import Tier


def _incident_with_one_executed_step(store, ledger):
    """A believed fact -> a decision -> a planned step that has already executed."""
    fact = store.remember(
        content="checkout-api revision 42 is the healthy baseline.",
        kind="finding",
        source_uri="system://metrics/checkout-api",
        source_class="system",
        tier=Tier.CORROBORATED,
    )
    run = ledger.open_run(title="checkout-api incident")
    decision = store.decide_citing(
        [fact.mem_id],
        summary="scale checkout-api to 8 tasks to absorb load",
        required_tiers={Tier.CORROBORATED, Tier.OPERATOR},
        run_id=run,
    )
    intent = ledger.record_intent(
        run,
        step_no=1,
        effector="ecs.update_service",
        params={"service": "checkout-api", "desired_count": 8},
        decision_id=decision.decision_id,
    )
    claim = ledger.claim_next(run, worker="worker-a", lease_seconds=30)
    ledger.record_result(claim, outcome="succeeded", observed_state={"desired_count": 8})
    return fact, decision, intent


def test_revoking_a_fact_reports_the_executed_steps_that_need_compensating(store, ledger):
    fact, decision, intent = _incident_with_one_executed_step(store, ledger)

    report = store.revoke(fact.mem_id, reason="metric feed was spoofed")

    assert decision.decision_id in report.tainted_decisions
    assert [s["idem_key"] for s in report.executed_steps] == [intent.idem_key]
    assert report.unexecuted_steps == []


def test_a_planned_but_unexecuted_step_needs_no_compensation(store, ledger):
    fact = store.remember(
        content="checkout-api revision 42 is the healthy baseline.",
        kind="finding",
        source_uri="system://metrics/checkout-api",
        source_class="system",
        tier=Tier.CORROBORATED,
    )
    run = ledger.open_run(title="checkout-api incident")
    decision = store.decide_citing(
        [fact.mem_id],
        summary="scale checkout-api",
        required_tiers={Tier.CORROBORATED, Tier.OPERATOR},
        run_id=run,
    )
    ledger.record_intent(
        run,
        step_no=1,
        effector="ecs.update_service",
        params={"service": "checkout-api", "desired_count": 8},
        decision_id=decision.decision_id,
    )  # planned, never claimed or executed

    report = store.revoke(fact.mem_id, reason="metric feed was spoofed")

    assert report.executed_steps == []
    assert len(report.unexecuted_steps) == 1


def test_compensating_actions_invert_executed_effects(store, ledger):
    fact, decision, intent = _incident_with_one_executed_step(store, ledger)
    report = store.revoke(fact.mem_id, reason="metric feed was spoofed")

    from cairn.rollback import compensating_actions

    actions = compensating_actions(report)

    assert len(actions) == 1
    action = actions[0]
    assert action["effector"] == "ecs.update_service"
    # The forward step scaled to 8; the compensation restores the pre-incident count.
    assert action["reverts_idem_key"] == intent.idem_key
    assert action["params"]["service"] == "checkout-api"
