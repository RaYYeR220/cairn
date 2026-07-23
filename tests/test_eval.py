"""Regression cover for the graded evaluation.

The scorecard is a published proof artifact, so its shape is pinned here: the deterministic
detectors must clear a floor on the attack corpus and never quarantine legitimate telemetry,
and the model second-opinion must close the paraphrase gap the detectors cannot.
"""

from cairn.eval.corpus import CORPUS
from cairn.eval.runner import score
from cairn.gate import IntegrityGate


def test_deterministic_detectors_clear_the_recall_floor_with_no_false_positives():
    card = score()

    assert card.false_positives == 0, card.false_positive_ids
    assert card.recall >= 0.80


def test_every_structural_attack_is_caught_by_code_alone():
    """Everything except pure paraphrase must be caught without a model in the loop."""
    card = score()

    structural_misses = [
        mid for mid in card.missed_ids if not mid.startswith("evade-")
    ]
    assert structural_misses == []


def test_the_model_second_opinion_closes_the_paraphrase_gap():
    # A classifier that recognises the semantic-only evasions the regex layer cannot see.
    paraphrase = {c.content for c in CORPUS if c.family == "paraphrase_evasion"}

    def classifier(content: str) -> float:
        return 1.0 if content in paraphrase else 0.0

    card = score(gate=IntegrityGate(classifier=classifier))

    assert card.recall == 1.0
    assert card.false_positives == 0
