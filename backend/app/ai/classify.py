"""零样本类别分类（Ontology × Embedding）。

词典能覆盖你想得到的词，覆盖不了你想不到的词。
「コインケース」「マグボトル」「ボストンバッグ」——每一个都是合法说法，
每一个都可能不在词表里。一旦类别抽不出来，category 维度就整个失效，
一个同色但毫不相干的物品就能排到正确答案前面，而且**完全静默**。

所以词典未命中时必须有兜底：把每个类别的别名拼成「类别原型文本」做成向量，
再拿描述向量去比余弦，取最相近且超过阈值的类别。
这样任何没见过的说法也能落到某个类别上。

结果一律标记 source_type=INFERRED / category_uncertain=True：
推断出来的类别可以参与加分，但绝不允许据此判定冲突。
"""
from __future__ import annotations

import statistics
from functools import lru_cache

from ..config import settings, synonyms
from .embedding_provider import cosine, get_embedding_provider

# 低于该余弦就认为「说不好是什么」，宁可留空也不要瞎猜
MIN_SIMILARITY = 0.35
# 与次优类别的差距太小同样不可信（例如 bag / wallet 难分）。
# 同样不能用绝对值——改成「相差多少个标准差」。
MIN_MARGIN_SD = 0.25

# 绝对阈值只对某一个模型的余弦分布有效：
#   paraphrase 系列的余弦散布在 0.1~0.7，e5 系列压缩在 0.7~0.95。
#   同一套 MIN_SIMILARITY / MIN_MARGIN 换个模型就会全线失灵——
#   要么全部拒判（分类全空），要么全部通过（乱判）。
# 所以真正的判据用 z-score：最高分比「所有类别的平均分」高出多少个标准差。
# 这个量纲无关，换模型不用重新调参。绝对阈值降级为兜底下限。
MIN_ZSCORE = 1.8


@lru_cache(maxsize=1)
def _prototypes() -> list[tuple[str, list[float]]]:
    """每个类别的原型向量：由该类别的全部别名拼成一句话再向量化。"""
    provider = get_embedding_provider()
    out: list[tuple[str, list[float]]] = []
    for code, aliases in synonyms().get("category", {}).items():
        if code.startswith("_"):
            continue
        text = "、".join([code, *aliases][:40])
        out.append((code, provider.embed(text, kind="passage")))
    return out


def reset_prototypes() -> None:
    _prototypes.cache_clear()


def zero_shot_category(text: str) -> tuple[str | None, float]:
    """返回 (类别 code, 余弦)。判不准时返回 (None, 相似度)。"""
    if not text or not text.strip():
        return None, 0.0
    try:
        vec = get_embedding_provider().embed(text, kind="query")
    except Exception:                                   # noqa: BLE001
        return None, 0.0

    scored = sorted(((code, cosine(vec, proto)) for code, proto in _prototypes()),
                    key=lambda x: x[1], reverse=True)
    if len(scored) < 3:
        return None, 0.0

    best_code, best = scored[0]
    runner_up = scored[1][1]
    sims = [s for _, s in scored]
    mean = statistics.fmean(sims)
    sd = statistics.pstdev(sims)
    z = (best - mean) / sd if sd > 1e-9 else 0.0

    # 主判据：z-score（量纲无关，换模型不用调参）
    # 兜底：绝对相似度下限 + 与次优的最小差距（防止 bag/wallet 这类难分的硬猜）
    margin_sd = (best - runner_up) / sd if sd > 1e-9 else 0.0
    if z < MIN_ZSCORE or margin_sd < MIN_MARGIN_SD or best < MIN_SIMILARITY:
        return None, best
    return best_code, best


def is_enabled() -> bool:
    """hashing provider 没有语义能力，零样本分类会全是噪声，直接关掉。"""
    return settings.embedding_provider != "hashing"
