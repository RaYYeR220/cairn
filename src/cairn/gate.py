"""The integrity gate.

Runs on the write path, before anything is embedded. Its job is narrow: decide the tier a
piece of content is allowed to occupy. It never decides what the agent should do with the
content, and it never deletes anything - quarantined rows are kept, with the reason attached,
because an audit trail of what was rejected is worth as much as the rejection.

Three rules, in order of authority:

1. A source ceiling. Content from somewhere an outside party can write to is capped at raw
   evidence no matter what the content says about itself.
2. Deterministic detectors. These alone are sufficient to quarantine; the gate does not need a
   model to be safe.
3. A model second opinion, which may only *lower* trust. If it errors, times out, or is not
   configured to answer, the content is quarantined rather than admitted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .trust import SOURCE_CEILING, Tier

#: Score at or above which the classifier's opinion alone quarantines content.
CLASSIFIER_THRESHOLD = 0.5


class GateUnavailable(Exception):
    """Internal signal that the second-opinion classifier could not be consulted."""


#: Each detector is a name and a pattern whose match means "this text is shaped like an
#: instruction to an agent, not like a record of something that happened".
DETECTORS: list[tuple[str, re.Pattern[str]]] = [
    (
        "override_prior_context",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}"
            r"\b(previous|prior|preceding|earlier|above|all)\b",
            re.I,
        ),
    ),
    (
        "role_marker",
        re.compile(
            r"(^|\n)\s*(system|assistant|user|developer)\s*:|<\|im_(start|end)\|>|\[/?INST\]",
            re.I,
        ),
    ),
    (
        "authority_claim",
        re.compile(
            r"\b(this|the following)\b[^.\n]{0,30}\bis (a message |an instruction )?from "
            r"(the )?(operator|administrator|admin|security team|sre)\b",
            re.I,
        ),
    ),
    (
        "privilege_request",
        re.compile(
            r"\b(grant|give|assign|escalate|elevate)\b[^.\n]{0,40}"
            r"\b(role|access|permission|privilege|admin|sudo|credential|key)\b",
            re.I,
        ),
    ),
    (
        "state_assertion_override",
        re.compile(
            r"\b(mark|set|declare|treat|consider|report)\b[^.\n]{0,40}"
            r"\b(as )?(healthy|resolved|closed|safe|passing|ok)\b",
            re.I,
        ),
    ),
    (
        "tool_invocation_syntax",
        re.compile(
            r"<tool_call|</?function_calls|\"tool_name\"\s*:|```(json|tool|python)\s*\n\s*\{",
            re.I,
        ),
    ),
    ("encoded_payload", re.compile(r"[A-Za-z0-9+/]{120,}={0,2}|(?:%[0-9A-Fa-f]{2}){12,}")),
    ("unicode_smuggling", re.compile(r"[\U000E0000-\U000E007F​-‏⁠-⁤]")),
    (
        "exfiltration_link",
        re.compile(r"!?\[[^\]]*\]\(\s*https?://[^)\s]*[?&][^)\s]*=[^)\s]*\)", re.I),
    ),
]


@dataclass(frozen=True)
class Verdict:
    tier: Tier
    reason: str
    detectors: list[str] = field(default_factory=list)
    classifier_score: float | None = None

    def as_json(self) -> dict:
        return {
            "tier": int(self.tier),
            "reason": self.reason,
            "detectors": self.detectors,
            "classifier_score": self.classifier_score,
        }


class IntegrityGate:
    def __init__(self, classifier: Callable[[str], float] | None = None) -> None:
        """`classifier` returns a 0..1 likelihood that the content is an injection attempt."""
        self._classifier = classifier

    def screen(self, content: str, source_class: str, requested_tier: Tier) -> Verdict:
        ceiling = SOURCE_CEILING.get(source_class, Tier.QUARANTINED)
        fired = [name for name, pattern in DETECTORS if pattern.search(content)]

        score: float | None = None
        classifier_failed = False
        if self._classifier is not None:
            try:
                score = float(self._classifier(content))
            except Exception:
                classifier_failed = True

        if fired:
            return Verdict(Tier.QUARANTINED, "instruction_shaped", fired, score)
        if classifier_failed:
            return Verdict(Tier.QUARANTINED, "classifier_unavailable", [], None)
        if score is not None and score >= CLASSIFIER_THRESHOLD:
            return Verdict(Tier.QUARANTINED, "classifier_flagged", [], score)

        return Verdict(Tier(min(requested_tier, ceiling)), "admitted", [], score)
