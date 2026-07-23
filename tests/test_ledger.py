"""The write-ahead intent ledger.

Every side effect the agent takes is preceded by a committed intent. These tests pin
the properties that make replay after a crash safe.
"""


def test_the_same_intent_recorded_twice_yields_one_row_and_one_key(ledger):
    run = ledger.open_run(title="checkout-api 5xx spike")

    first = ledger.record_intent(
        run,
        step_no=1,
        effector="ecs.update_service",
        params={"service": "checkout-api", "desired_count": 4},
    )
    # Same step, same parameters, different key order: still the same intent.
    second = ledger.record_intent(
        run,
        step_no=1,
        effector="ecs.update_service",
        params={"desired_count": 4, "service": "checkout-api"},
    )

    assert first.idem_key == second.idem_key
    assert ledger.count_intents(run) == 1
