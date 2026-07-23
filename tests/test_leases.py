"""Lease and fencing behaviour.

A run survives losing its worker. These tests pin what "survives" means: exactly one worker
owns a step at a time, an abandoned step becomes claimable again, and a worker that has lost
its lease can no longer write a result for it.
"""

import pytest

from cairn.ledger import LeaseLost


def _plan(ledger, title="checkout-api 5xx spike"):
    run = ledger.open_run(title=title)
    ledger.record_intent(
        run,
        step_no=1,
        effector="ecs.update_service",
        params={"service": "checkout-api", "desired_count": 4},
    )
    return run


def test_a_claimed_step_is_invisible_to_other_workers(ledger, other_worker):
    run = _plan(ledger)

    mine = ledger.claim_next(run, worker="worker-a", lease_seconds=30)
    theirs = other_worker.claim_next(run, worker="worker-b", lease_seconds=30)

    assert mine is not None
    assert theirs is None


def test_an_abandoned_step_becomes_claimable_again(ledger, other_worker):
    run = _plan(ledger)

    ledger.claim_next(run, worker="worker-a", lease_seconds=0)  # dies immediately
    resumed = other_worker.claim_next(run, worker="worker-b", lease_seconds=30)

    assert resumed is not None
    assert resumed.intent.step_no == 1


def test_a_worker_that_lost_its_lease_cannot_record_a_result(ledger, other_worker):
    run = _plan(ledger)
    zombie = ledger.claim_next(run, worker="worker-a", lease_seconds=0)
    other_worker.claim_next(run, worker="worker-b", lease_seconds=30)

    with pytest.raises(LeaseLost):
        ledger.record_result(zombie, outcome="succeeded", observed_state={"revision": 7})


def test_a_step_with_a_result_is_never_handed_out_again(ledger, other_worker):
    run = _plan(ledger)
    claim = ledger.claim_next(run, worker="worker-a", lease_seconds=0)
    ledger.record_result(claim, outcome="succeeded", observed_state={"revision": 7})

    assert other_worker.claim_next(run, worker="worker-b", lease_seconds=30) is None
