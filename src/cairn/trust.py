"""Trust tiers.

The tier is a prefix column of the vector index, so it is not a label attached to a row -
it decides which partitions of the index a retrieval is allowed to walk at all.
"""

from __future__ import annotations

from enum import IntEnum

#: Titan Text Embeddings V2 default width; the `memory.embedding` column is declared to match.
EMBEDDING_DIMENSIONS = 1024


class Tier(IntEnum):
    QUARANTINED = 0
    RAW_EVIDENCE = 1
    CORROBORATED = 2
    OPERATOR = 3


#: The highest tier a given kind of source is ever allowed to reach, whatever it claims about
#: itself. Anything an outside party can write to is capped below the planner's floor.
SOURCE_CEILING: dict[str, Tier] = {
    "attacker_writable": Tier.RAW_EVIDENCE,
    "system": Tier.CORROBORATED,
    "operator": Tier.OPERATOR,
}
