"""Embedding Provider（可插拔）。

设计要点（DESIGN.md §7）：
- Embedding 不进主表，`embeddings` 表连同 model/version/dimensions/content_hash/status 一起存
- content_hash 相同则跳过重算，大量减少调用
- 不同模型产生的向量不能直接比较，换模型并存而非覆盖
"""
from __future__ import annotations

import hashlib
import math
import struct
from typing import Protocol

from ..config import settings


def content_hash(text: str) -> str:
    """SHA256(content_text)：命中即跳过重新生成。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


class EmbeddingProvider(Protocol):
    name: str
    model: str
    version: str
    dim: int

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedding:
    """确定性哈希投影 Embedding。

    零外部依赖、可离线、可在 CI 中稳定复现。它对**字符 n-gram 共现**敏感，
    因此「透明手机壳」与「透明保护套」会有一定相似度，但当然不具备真正的
    跨语言语义能力——生产请切到 local / 企业网关 provider。
    """

    name = "hashing"

    def __init__(self, dim: int | None = None, model: str | None = None,
                 version: str | None = None) -> None:
        self.dim = dim or settings.embedding_dim
        self.model = model or settings.embedding_model
        self.version = version or settings.embedding_model_version

    @staticmethod
    def _tokens(text: str) -> list[str]:
        from .normalize import norm_text

        s = norm_text(text)
        if not s:
            return []
        words = [w for w in s.split(" ") if w]
        grams: list[str] = list(words)
        compact = s.replace(" ", "")
        for n in (2, 3):
            grams.extend(compact[i:i + n] for i in range(max(0, len(compact) - n + 1)))
        return grams

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in self._tokens(text):
            digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            idx = struct.unpack("<Q", digest)[0] % self.dim
            sign = 1.0 if digest[0] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]


class LocalSentenceTransformer:
    """本机 sentence-transformers（无外部 API 调用）。"""

    name = "local"

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer  # 延迟导入

        self.model = settings.embedding_model
        self.version = settings.embedding_model_version
        self._m = SentenceTransformer(self.model)
        self.dim = settings.embedding_dim

    def embed(self, text: str) -> list[float]:
        raw = self._m.encode(text, normalize_embeddings=True).tolist()
        return _fit_dim(raw, self.dim)


class OpenAICompatibleEmbedding:
    """企业自有网关 / 自托管模型（OpenAI 兼容 /v1/embeddings）。"""

    name = "openai_compatible"

    def __init__(self) -> None:
        self.model = settings.embedding_model
        self.version = settings.embedding_model_version
        self.dim = settings.embedding_dim
        self.base_url = settings.embedding_base_url.rstrip("/")
        self.api_key = settings.embedding_api_key
        if not self.base_url:
            raise RuntimeError("LF_EMBEDDING_BASE_URL 未配置")

    def embed(self, text: str) -> list[float]:
        import httpx

        resp = httpx.post(
            f"{self.base_url}/v1/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            json={"model": self.model, "input": text},
            timeout=settings.llm_timeout,
        )
        resp.raise_for_status()
        return _fit_dim(resp.json()["data"][0]["embedding"], self.dim)


def _fit_dim(vec: list[float], dim: int) -> list[float]:
    """向量维度对齐 schema 里的 VECTOR(dim)：不足补零，超出截断。"""
    if len(vec) == dim:
        return vec
    if len(vec) < dim:
        return list(vec) + [0.0] * (dim - len(vec))
    return list(vec[:dim])


_PROVIDERS = {
    "hashing": HashingEmbedding,
    "local": LocalSentenceTransformer,
    "openai_compatible": OpenAICompatibleEmbedding,
}

_cache: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _cache
    if _cache is None:
        cls = _PROVIDERS.get(settings.embedding_provider)
        if cls is None:
            raise RuntimeError(f"未知 embedding provider: {settings.embedding_provider}")
        _cache = cls()
    return _cache


def reset_embedding_provider() -> None:
    global _cache
    _cache = None
