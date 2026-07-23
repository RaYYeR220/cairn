"""Embedding providers.

The store depends on this narrow interface so that tests can run against a deterministic
embedder while production uses Bedrock.
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import Protocol

from .trust import EMBEDDING_DIMENSIONS


class Embedder(Protocol):
    dimensions: int

    def embed(self, text: str) -> list[float]:
        ...


class DeterministicEmbedder:
    """Maps text to a stable unit vector by seeding a PRNG with the text's digest.

    It carries no semantics, which is exactly what the trust-boundary tests need: identical
    text is its own nearest neighbour, and unrelated text is far away.
    """

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
        rng = random.Random(seed)
        values = [rng.gauss(0.0, 1.0) for _ in range(self.dimensions)]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]
