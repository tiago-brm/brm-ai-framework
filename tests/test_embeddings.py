import importlib.util

import pytest

from mcp_engine.vault.embeddings import DEFAULT_DIM, EmbeddingProvider, LocalEmbeddingProvider

HAS_SENTENCE_TRANSFORMERS = importlib.util.find_spec("sentence_transformers") is not None

requires_model = pytest.mark.skipif(
    not HAS_SENTENCE_TRANSFORMERS,
    reason="sentence-transformers not installed",
)


def test_local_provider_satisfies_protocol() -> None:
    assert isinstance(LocalEmbeddingProvider(), EmbeddingProvider)


def test_dim_does_not_load_model() -> None:
    provider = LocalEmbeddingProvider()
    assert provider.dim == DEFAULT_DIM
    assert provider._model is None


def test_encode_empty_does_not_load_model() -> None:
    provider = LocalEmbeddingProvider()
    assert provider.encode([]) == []
    assert provider._model is None


@requires_model
def test_encode_returns_vectors_of_declared_dim() -> None:
    provider = LocalEmbeddingProvider()
    vectors = provider.encode(["prescrição de reparação civil", "modelo de petição"])

    assert len(vectors) == 2
    assert all(len(v) == provider.dim for v in vectors)
    assert all(isinstance(value, float) for value in vectors[0])


@requires_model
def test_encode_is_deterministic() -> None:
    provider = LocalEmbeddingProvider()
    first = provider.encode(["mesma frase"])[0]
    second = provider.encode(["mesma frase"])[0]

    assert first == pytest.approx(second)
