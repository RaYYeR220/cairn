"""Score the integrity gate against the corpus and report the result honestly.

The gate is run with its deterministic detectors only - no model in the loop - so the score is a
floor: it is the protection the system has even when the classifier is unavailable and everything
depends on the code. Misses are listed, not hidden.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from ..gate import IntegrityGate
from ..trust import Tier
from .corpus import CORPUS, FAMILIES, Case


@dataclass
class Scorecard:
    attacks: int
    benign: int
    caught: int          # attacks correctly quarantined (true positives)
    missed: int          # attacks admitted (false negatives)
    false_positives: int  # benign quarantined
    recall: float
    false_positive_rate: float
    per_family: dict[str, dict[str, int]]
    missed_ids: list[str] = field(default_factory=list)
    false_positive_ids: list[str] = field(default_factory=list)

    def as_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def as_markdown(self) -> str:
        lines = [
            "# Integrity gate scorecard",
            "",
            "_Deterministic detectors only; no model in the loop._",
            "",
            f"- Attacks: **{self.caught}/{self.attacks} caught** "
            f"(recall {self.recall:.0%}), {self.missed} missed.",
            f"- Benign lines: **{self.benign - self.false_positives}/{self.benign} admitted** "
            f"(false-positive rate {self.false_positive_rate:.0%}).",
            "",
            "## By attack family",
            "",
        ]
        for family in FAMILIES:
            stat = self.per_family[family]
            lines.append(f"- `{family}`: {stat['caught']}/{stat['total']} caught")
        if self.missed_ids:
            lines += ["", "## Missed attacks", ""]
            lines += [f"- {mid}" for mid in self.missed_ids]
        if self.false_positive_ids:
            lines += ["", "## False positives (benign lines quarantined)", ""]
            lines += [f"- {fid}" for fid in self.false_positive_ids]
        return "\n".join(lines) + "\n"


def _quarantined(gate: IntegrityGate, case: Case) -> bool:
    verdict = gate.screen(case.content, source_class="attacker_writable", requested_tier=Tier.RAW_EVIDENCE)
    return verdict.tier == Tier.QUARANTINED


def score(gate: IntegrityGate | None = None, corpus: list[Case] = CORPUS) -> Scorecard:
    gate = gate or IntegrityGate()
    attacks = [c for c in corpus if c.poison]
    benign = [c for c in corpus if not c.poison]

    per_family: dict[str, dict[str, int]] = {f: {"total": 0, "caught": 0} for f in FAMILIES}
    missed_ids: list[str] = []
    for case in attacks:
        per_family[case.family]["total"] += 1
        if _quarantined(gate, case):
            per_family[case.family]["caught"] += 1
        else:
            missed_ids.append(case.id)

    false_positive_ids = [c.id for c in benign if _quarantined(gate, c)]

    caught = len(attacks) - len(missed_ids)
    return Scorecard(
        attacks=len(attacks),
        benign=len(benign),
        caught=caught,
        missed=len(missed_ids),
        false_positives=len(false_positive_ids),
        recall=caught / len(attacks) if attacks else 1.0,
        false_positive_rate=len(false_positive_ids) / len(benign) if benign else 0.0,
        per_family=per_family,
        missed_ids=missed_ids,
        false_positive_ids=false_positive_ids,
    )


def main() -> None:
    card = score()
    print(card.as_markdown())


if __name__ == "__main__":
    main()
