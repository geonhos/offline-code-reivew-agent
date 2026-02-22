"""임베딩 생성 - Ollama nomic-embed-text 모델 사용."""

import httpx

from src.config import settings


def embed(texts: str | list[str]) -> list[list[float]]:
    """텍스트를 벡터로 변환한다.

    Args:
        texts: 단일 문자열 또는 문자열 리스트.

    Returns:
        임베딩 벡터 리스트. 단일 입력이어도 리스트로 반환.
    """
    if isinstance(texts, str):
        texts = [texts]

    resp = httpx.post(
        f"{settings.ollama_base_url}/api/embed",
        json={"model": settings.embed_model, "input": texts},
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["embeddings"]


def embed_single(text: str) -> list[float]:
    """단일 텍스트의 임베딩 벡터를 반환한다."""
    return embed(text)[0]
