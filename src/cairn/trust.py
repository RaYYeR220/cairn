"""Trust tiers.

The tier is a prefix column of the vector index, so it is not a label attached to a row -
it decides which partitions of the index a retrieval is allowed to walk at all.
"""

from __future__ import annotations

import os
from enum import IntEnum

#: Width of the embedding vector, and of the `memory.embedding` column. Defaults to the local
#: model's 384 dims (a zero-credential demo), overridable to match a hosted embedder - e.g.
#: CAIRN_EMBEDDING_DIM=1024 for Titan Text Embeddings V2.
EMBEDDING_DIMENSIONS = int(os.environ.get("CAIRN_EMBEDDING_DIM", "384"))


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
