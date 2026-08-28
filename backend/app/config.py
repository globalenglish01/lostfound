"""全局配置与 JSON 配置加载。

权重、冲突规则、同义词一律来自 `config/*.json`，绝不写死在代码里
（设计文档 4.5：V1 人工设定，V2 起用真实数据校准）。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

# backend/app/config.py -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.getenv("LF_CONFIG_DIR", PROJECT_ROOT / "config"))


class Settings:
    """环境配置。"""

    # --- 数据库 ---
    database_url: str = os.getenv(
        "LF_DATABASE_URL",
        "postgresql+psycopg://lostfound:lostfound@localhost:5432/lostfound",
    )

    # --- Embedding ---
    # onnx   : fastembed + onnxruntime 多语言句向量（本机推理，无外部调用，推荐）
    # local  : sentence-transformers（本机模型，无外部调用）
    # hashing: 确定性哈希投影，零依赖，用于开发/CI/离线
    # openai_compatible: 自建或企业网关（LF_EMBEDDING_BASE_URL）
    embedding_provider: str = os.getenv("LF_EMBEDDING_PROVIDER", "hashing")
    embedding_model: str = os.getenv("LF_EMBEDDING_MODEL", "hashing-1536-v1")
    embedding_model_version: str = os.getenv("LF_EMBEDDING_MODEL_VERSION", "v1")
    embedding_dim: int = int(os.getenv("LF_EMBEDDING_DIM", "1536"))
    model_cache_dir: str = os.getenv("LF_MODEL_CACHE", "")
    embedding_base_url: str = os.getenv("LF_EMBEDDING_BASE_URL", "")
    embedding_api_key: str = os.getenv("LF_EMBEDDING_API_KEY", "")

    # --- LLM ---
    # rule             : 纯规则实现，无任何外部 API（默认，CI 可跑）
    # openai_compatible: 走企业自有网关 / 自托管模型
    llm_provider: str = os.getenv("LF_LLM_PROVIDER", "rule")
    llm_model: str = os.getenv("LF_LLM_MODEL", "rule-engine-v1")
    llm_base_url: str = os.getenv("LF_LLM_BASE_URL", "")
    llm_api_key: str = os.getenv("LF_LLM_API_KEY", "")
    llm_timeout: float = float(os.getenv("LF_LLM_TIMEOUT", "60"))

    # --- 检索漏斗 ---
    structured_limit: int = int(os.getenv("LF_STRUCTURED_LIMIT", "5000"))
    keyword_limit: int = int(os.getenv("LF_KEYWORD_LIMIT", "500"))
    vector_limit: int = int(os.getenv("LF_VECTOR_LIMIT", "500"))
    fusion_limit: int = int(os.getenv("LF_FUSION_LIMIT", "1000"))
    rrf_k: int = int(os.getenv("LF_RRF_K", "60"))
    scoring_limit: int = int(os.getenv("LF_SCORING_LIMIT", "200"))
    rerank_limit: int = int(os.getenv("LF_RERANK_LIMIT", "20"))

    algorithm_version: str = os.getenv("LF_ALGORITHM_VERSION", "lf-matching-v1.0")


settings = Settings()


def _load(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def matching_weights() -> dict[str, Any]:
    return _load("matching_weights.json")


@lru_cache(maxsize=1)
def attribute_weights() -> dict[str, Any]:
    return _load("attribute_weights.json")


@lru_cache(maxsize=1)
def conflict_rules() -> dict[str, Any]:
    return _load("conflict_rules.json")


@lru_cache(maxsize=1)
def synonyms() -> dict[str, Any]:
    return _load("synonyms.json")


def reload_configs() -> None:
    """热更新权重配置（管理端调参后调用，无需重启）。"""
    for fn in (matching_weights, attribute_weights, conflict_rules, synonyms):
        fn.cache_clear()


def dimension_weights(category_code: str | None) -> dict[str, float]:
    """取某个类别的八维权重；未配置的类别退回 default。"""
    cfg = matching_weights()
    base = dict(cfg["default"])
    if category_code:
        base.update(cfg.get("categories", {}).get(category_code, {}))
    return base


def attribute_profile(category_code: str | None) -> dict[str, float]:
    """取某个类别的属性级权重 profile。"""
    cfg = attribute_weights()
    profile = dict(cfg["default"])
    if category_code:
        profile.update(
            {k: v for k, v in cfg.get(category_code, {}).items() if not k.startswith("_")}
        )
    return profile


def reliability_of(source: str | None) -> float:
    """来源 -> 可靠性 r_i。未知来源保守取 0.7。"""
    table = matching_weights()["reliability"]
    if not source:
        return 0.7
    return float(table.get(source.upper(), 0.7))
