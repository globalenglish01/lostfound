"""图像向量 Provider（可插拔）。

用 CLIP：视觉侧和文本侧落在**同一个向量空间**，因此同时支持两种检索

    图 → 图   用户拍的照片   ↔ 工作人员拍的照片
    文 → 图   「黒いリュック」 ↔ 工作人员拍的照片（用户没照片时的救命通道）

注意这里的文本向量与主检索用的 mpnet **不在同一空间**，绝不能互相比较。
`embeddings` 表按 (embedding_type, model_name, model_version) 分区存放，
天然把两个空间隔开了——这正是 DESIGN.md §7 坚持「模型信息必须落库」的原因。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..config import settings
from .embedding_provider import _fit_dim


class ImageProvider(Protocol):
    name: str
    model: str
    version: str
    dim: int

    def embed_image(self, path: str | Path) -> list[float]: ...

    def embed_text(self, text: str) -> list[float]: ...


class ClipOnnxImageProvider:
    """本机 ONNX CLIP（fastembed），无外部 API。"""

    name = "clip-onnx"

    def __init__(self) -> None:
        from fastembed import ImageEmbedding, TextEmbedding

        self.model = settings.image_model
        self.version = settings.image_model_version
        self.dim = settings.embedding_dim          # 与文本向量共用 VECTOR(1536)，不足补零
        cache = settings.model_cache_dir or None
        self._vision = ImageEmbedding(model_name=self.model, cache_dir=cache)
        self._text = TextEmbedding(model_name=settings.image_text_model, cache_dir=cache)

    def embed_image(self, path: str | Path) -> list[float]:
        vec = next(iter(self._vision.embed([str(path)]))).tolist()
        return _fit_dim(vec, self.dim)

    def embed_text(self, text: str) -> list[float]:
        """把文字投到 CLIP 空间，用来做「文 → 图」检索。"""
        vec = next(iter(self._text.embed([text or ""]))).tolist()
        return _fit_dim(vec, self.dim)


class DisabledImageProvider:
    """未启用图像匹配时的占位实现。

    绝不能在这里返回零向量——零向量和任何东西的余弦都是 0，
    会被当成「图片完全不像」，而正确语义是「没有图片，该维度不参与评分」。
    所以直接抛异常，让调用方走缺失分支。
    """

    name = "disabled"
    model = "-"
    version = "-"
    dim = 0

    def embed_image(self, path: str | Path) -> list[float]:
        raise RuntimeError("图像匹配未启用：设置 LF_IMAGE_PROVIDER=clip_onnx")

    def embed_text(self, text: str) -> list[float]:
        raise RuntimeError("图像匹配未启用：设置 LF_IMAGE_PROVIDER=clip_onnx")


_PROVIDERS = {"clip_onnx": ClipOnnxImageProvider, "disabled": DisabledImageProvider}
_cache: ImageProvider | None = None


def get_image_provider() -> ImageProvider:
    global _cache
    if _cache is None:
        cls = _PROVIDERS.get(settings.image_provider)
        if cls is None:
            raise RuntimeError(f"未知 image provider: {settings.image_provider}")
        _cache = cls()
    return _cache


def image_enabled() -> bool:
    return settings.image_provider != "disabled"


def reset_image_provider() -> None:
    global _cache
    _cache = None
