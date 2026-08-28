from __future__ import annotations

from typing import Protocol, runtime_checkable

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_DIM = 384


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def dim(self) -> int: ...

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbeddingProvider:
    def __init__(self, model_name: str = DEFAULT_MODEL, dim: int = DEFAULT_DIM) -> None:
        self._model_name = model_name
        self._dim = dim
        self._model = None

    @property
    def dim(self) -> int:
        return self._dim

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
        return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._load().encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return [[float(value) for value in vector] for vector in vectors]
