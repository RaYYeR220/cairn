"""The retrieval path must be enforced by the index, not by a filter above it.

Excluding quarantined memory with a WHERE clause would be correct and useless: the traversal
would still walk it. This test reads the query plan and pins the stronger property - the
approximate-nearest-neighbour search is scoped to the tiers the caller allowed, one prefix
span per tier, and nothing else is visited.
"""

import pytest

from cairn.memory import Tier


@pytest.fixture
def populated_store(store):
    for i in range(240):
        tier = Tier(i % 4)
        store.remember(
            content=f"checkout-api emitted diagnostic sample {i}",
            kind="evidence",
            source_uri=f"cloudwatch:///aws/ecs/checkout-api#{i}",
            source_class="operator",
            tier=tier,
        )
    store.analyze()
    return store


def test_recall_is_served_by_a_vector_search_scoped_to_the_allowed_tiers(populated_store):
    plan = populated_store.explain_recall(
        "why is checkout-api returning 5xx",
        tiers={Tier.CORROBORATED, Tier.OPERATOR},
        limit=5,
    )

    assert "vector search" in plan
    assert "memory@mem_vec" in plan
    # One prefix span per allowed tier, and no span for the quarantined or raw tiers.
    assert plan.count("prefix spans:") == 1
    spans = plan.split("prefix spans:")[1].splitlines()[0]
    assert "/2 -" in spans and "/3 -" in spans
    assert "/0 -" not in spans and "/1 -" not in spans


def test_the_quarantined_tier_is_reachable_only_by_asking_for_it_explicitly(populated_store):
    plan = populated_store.explain_recall(
        "why is checkout-api returning 5xx", tiers={Tier.QUARANTINED}, limit=5
    )

    spans = plan.split("prefix spans:")[1].splitlines()[0]
    assert "/0 -" in spans
    assert "/2 -" not in spans
