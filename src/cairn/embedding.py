"""Embedding providers.

The store depends on this narrow interface so that tests can run against a deterministic
embedder while production uses Bedrock.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
from typing import Protocol

from .trust import EMBEDDING_DIMENSIONS

#: Local model whose width matches the default EMBEDDING_DIMENSIONS (384).
DEFAULT_LOCAL_MODEL = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    dimensions: int

    def embed(self, text: str) -> list[float]:
        ...


class FastEmbedEmbedder:
    """A local, CPU-only semantic embedder (fastembed / ONNX).

    Real semantic vectors with no credentials and no network at query time - the model downloads
    once on first use, then runs offline. This is the default for the reproducible demo; a judge
    can run the whole system without an API key.
    """

    def __init__(self, model_name: str = DEFAULT_LOCAL_MODEL) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model_name)
        # Probe the width once so the schema and the column agree.
        self.dimensions = len(next(iter(self._model.embed(["dimension probe"]))))

    def embed(self, text: str) -> list[float]:
        return [float(x) for x in next(iter(self._model.embed([text])))]


def make_embedder():
    """Pick an embedder from the environment.

    CAIRN_EMBEDDER=bedrock uses Titan (hosted); anything else uses the local model, falling back to
    the deterministic test embedder only if fastembed is not installed. The chosen embedder's width
    must match CAIRN_EMBEDDING_DIM (the schema is built from it).
    """
    choice = os.environ.get("CAIRN_EMBEDDER", "local").lower()
    if choice == "bedrock":
        from .bedrock import BedrockEmbedder

        return BedrockEmbedder()
    try:
        return FastEmbedEmbedder()
    except ImportError:
        return DeterministicEmbedder()


class DeterministicEmbedder:
    """Maps text to a stable unit vector by seeding a PRNG with the text's digest.

    It carries no semantics, which is exactly what the trust-boundary tests need: identical
    text is its own nearest neighbour, and unrelated text is far away.
    """

    def __init__(self, dimensions: int | None = None) -> None:
        self.dimensions = dimensions or EMBEDDING_DIMENSIONS

    def embed(self, text: str) -> list[float]:
        seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
        rng = random.Random(seed)
        values = [rng.gauss(0.0, 1.0) for _ in range(self.dimensions)]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]
