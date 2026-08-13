"""The local embedder gives genuinely semantic recall.

Skipped unless fastembed is installed (it pulls a model on first run), so the core suite stays
light. When present, this pins the property the demo relies on: memory is retrieved by meaning,
not by shared keywords.
"""

import uuid

import pytest

pytest.importorskip("fastembed")
pytestmark = pytest.mark.slow

from cairn.embedding import FastEmbedEmbedder  # noqa: E402
from cairn.gate import IntegrityGate  # noqa: E402
from cairn.memory import MemoryStore, Tier  # noqa: E402


@pytest.fixture(scope="module")
def local_embedder():
    return FastEmbedEmbedder()


def test_the_local_model_width_matches_the_schema(local_embedder):
    from cairn.trust import EMBEDDING_DIMENSIONS

    assert local_embedder.dimensions == EMBEDDING_DIMENSIONS


def test_recall_matches_on_meaning_not_shared_words(test_database, local_embedder):
    import psycopg

    with psycopg.connect(test_database, autocommit=True) as conn:
        store = MemoryStore(conn, tenant_id=uuid.uuid4(), embedder=local_embedder, gate=IntegrityGate())
        for fact in [
            "Rolling back checkout-api to the previous task definition cleared the elevated error rate.",
            "Scaling the payments worker pool up absorbed the traffic surge.",
            "A memory leak in the image resizer caused OOM restarts until the container was patched.",
        ]:
            store.remember(fact, kind="postmortem", source_uri="op://runbooks",
                           source_class="operator", tier=Tier.OPERATOR)

        # No word here appears in the stored postmortems.
        hits = store.recall(
            "the shopping cart service is throwing 500s right after we shipped a new build",
            tiers={Tier.OPERATOR}, limit=1,
        )

    assert "checkout-api" in hits[0].content
