"""题库查重领域的回归测试。

核心断言只有一条，但它是整个工具成立的前提：

    同服务、同场景、语义高度相似  ≠  同一道题

这与失物领域「iPhone 15 Pro vs Pro Max」是同一条铁律的两次应用。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.matching.scoring import compute_score                      # noqa: E402
from domain.questionbank import features as F                       # noqa: E402
from domain.questionbank.loader import build_question               # noqa: E402
from domain.questionbank.normalize import (                         # noqa: E402
    canonical_kp,
    extract_constraints,
    extract_services,
    split_question,
    strip_boilerplate,
)


def score(a, b, *, stem=0.9, ans=0.9, kw=0.5) -> float:
    feats = F.build_features(a, b, stem_cosine=stem, answer_cosine=ans, keyword_sim=kw)
    report = F.detect_conflicts(a, b)
    return compute_score(feats, report, weights=F.WEIGHTS,
                         dimensions=F.DIMENSIONS,
                         level_config=F.LEVEL_CONFIG).final_score


def q(qid, stem, options, answer, kps=()):
    raw = stem + "\n" + "\n".join(f"{k}. {v}" for k, v in sorted(options.items()))
    return build_question(qid=qid, raw_q=raw,
                          raw_a=f"✅ 正确答案: {', '.join(answer)}",
                          kps=list(kps))


# ---------------------------------------------------------------------------
# 术语归一：绝不做包含式合并
# ---------------------------------------------------------------------------

def test_vendor_prefix_is_stripped():
    assert canonical_kp("AWS Lambda") == canonical_kp("Lambda") == "lambda"
    assert canonical_kp("Amazon S3") == canonical_kp("S3") == "s3"


def test_specific_concept_is_not_merged_into_the_service():
    """「Amazon S3 跨区域复制」并进「Amazon S3」会抹平最有区分度的信号。"""
    assert canonical_kp("Amazon S3跨区域复制") != canonical_kp("Amazon S3")
    assert canonical_kp("存储成本优化") != canonical_kp("成本优化")


def test_manual_synonyms_are_merged():
    assert canonical_kp("成本效益") == canonical_kp("成本优化")


def test_short_ascii_service_alias_needs_word_boundary():
    """"dx" 不能命中 "index"，否则几百道题上会积累成大量假阳性。"""
    assert "DirectConnect" not in extract_services("使用 index 加速查询")
    assert "DirectConnect" in extract_services("通过 DX 专线连接本地数据中心")


# ---------------------------------------------------------------------------
# 题干拆分与套话剥离
# ---------------------------------------------------------------------------

def test_split_question():
    stem, opts = split_question(
        "问题 #12 一家公司需要迁移数据库。\nA. 使用 DMS\nB. 使用快照\nC. 手工导出")
    assert stem.startswith("一家公司")
    assert set(opts) == {"A", "B", "C"}
    assert "DMS" in opts["A"]


def test_generic_ask_is_stripped():
    """几乎每道题都以「哪个解决方案能满足这些要求？」结尾，留着会整体抬高余弦。"""
    s = strip_boilerplate("公司要迁移数据库。哪个解决方案将满足这些要求？")
    assert "哪个解决方案" not in s
    assert "迁移数据库" in s


def test_strip_never_empties():
    assert strip_boilerplate("哪个解决方案将满足这些要求？").strip()


# ---------------------------------------------------------------------------
# 约束抽取：判别器
# ---------------------------------------------------------------------------

def test_constraints_extracted():
    assert "cost" in extract_constraints("需要最具成本效益的方案")
    assert "downtime" in extract_constraints("要求最小停机时间完成切换")
    assert "ops_overhead" in extract_constraints("以最少的运营开销实现")


# ---------------------------------------------------------------------------
# 核心铁律
# ---------------------------------------------------------------------------

def _same_scenario_pair(a_ask, b_ask, a_ans, b_ans):
    stem = "一家公司在 AWS 上运行 EC2 和 RDS，计划跨多个账户和区域部署。"
    opts = {"A": a_ans, "B": "使用 CloudFormation StackSets 跨账户部署",
            "C": "手工在每个账户创建资源", "D": "使用第三方工具"}
    a = q("qa", stem + a_ask, opts, ["A"])
    optsb = dict(opts, A=b_ans)
    b = q("qb", stem + b_ask, optsb, ["A"])
    return a, b


def test_same_scenario_different_goal_is_not_duplicate():
    """场景一样、服务一样，但一个求成本最优、一个求最小停机 —— 两道不同的题。"""
    a, b = _same_scenario_pair(
        "需要最具成本效益的方案。", "需要最小停机时间完成迁移。",
        "使用 Spot 实例降低成本", "使用 DMS 持续复制实现近乎零停机")
    assert score(a, b, stem=0.95, ans=0.6) < 82


def test_identical_question_is_duplicate():
    stem = "一家公司需要将本地 Kubernetes 集群迁移到 AWS，以最少的迁移工作量完成。"
    opts = {"A": "迁移到 Amazon EKS 并使用 ECR 存储镜像",
            "B": "重写为 Lambda", "C": "迁移到 EC2 自建", "D": "使用 Fargate 重构"}
    a = q("qa", stem, opts, ["A"], ["Amazon EKS", "迁移工作量"])
    b = q("qb", "某公司希望把自管理的 Kubernetes 集群搬到 AWS，迁移工作量要最小。",
          opts, ["A"], ["EKS", "迁移工作量"])
    assert score(a, b, stem=0.93, ans=0.99) >= 82


def test_single_vs_multi_select_can_never_merge():
    """单选和多选是结构不同的题，语义再像也不能合并。"""
    stem = "一家公司要提升 S3 数据的持久性。"
    opts = {"A": "启用跨区域复制", "B": "启用版本控制",
            "C": "使用 Glacier", "D": "开启 MFA Delete"}
    a = q("qa", stem, opts, ["A"])
    b = q("qb", stem, opts, ["A", "B"])
    s = score(a, b, stem=1.0, ans=0.95)
    assert s < 82, s


def test_disjoint_services_is_major_conflict():
    a = q("qa", "公司需要用 Amazon Athena 分析 S3 上的日志。",
          {"A": "使用 Athena 查询", "B": "导入 Redshift"}, ["A"])
    b = q("qb", "公司需要用 AWS Direct Connect 连接本地数据中心。",
          {"A": "建立 DX 专线", "B": "使用 VPN"}, ["A"])
    report = F.detect_conflicts(a, b)
    assert any(c["field_name"] == "services" for c in report.as_dicts())


def test_missing_answer_text_does_not_zero_the_score():
    """有一侧没抽到正确选项文本时，该维度不参与评分，而不是记 0。"""
    stem = "一家公司需要将本地 Kubernetes 集群迁移到 AWS。"
    opts = {"A": "迁移到 Amazon EKS", "B": "重写为 Lambda"}
    a = q("qa", stem, opts, ["A"], ["EKS"])
    b = q("qb", stem, opts, [], ["EKS"])          # 没有答案
    feats = F.build_features(a, b, stem_cosine=0.95, answer_cosine=None, keyword_sim=0.6)
    res = compute_score(feats, F.detect_conflicts(a, b), weights=F.WEIGHTS,
                        dimensions=F.DIMENSIONS, level_config=F.LEVEL_CONFIG)
    assert res.dimension_scores["answer"] is None
    assert "answer" not in res.used_weights
    assert res.final_score > 55


@pytest.mark.parametrize("level,expect_keep", [
    ("DUPLICATE", False), ("LIKELY_DUPLICATE", False),
    ("SAME_TOPIC", True), ("DISTINCT", True),
])
def test_level_semantics(level, expect_keep):
    """SAME_TOPIC 及以下必须保留——那是「同一片知识的不同考点」，不是重复。"""
    order = [b["level"] for b in F.LEVEL_CONFIG["match_levels"]]
    assert level in order
    keep = order.index(level) >= order.index("SAME_TOPIC")
    assert keep is expect_keep


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

def test_report_renders_without_crashing(tmp_path):
    """报告生成器崩掉的代价是前面十几分钟的计算全白跑，必须有烟雾测试。

    （真踩过：写复习清单时用 `a, b = qs[p.a], qs[p.b]` 把行列表 `a` 遮蔽了，
      跑完 16104 对打分之后才在最后一行炸掉。）
    """
    from domain.questionbank.dedup import Cluster, DedupResult, Pair
    from domain.questionbank.report import write_json, write_markdown

    a = q("qa", "公司需要加密所有新建的 EBS 卷。", {"A": "启用 EBS 默认加密", "B": "手工加密"}, ["A"])
    b = q("qb", "公司需要加密所有已有的 EBS 卷。", {"A": "快照复制后替换", "B": "启用默认加密"}, ["A"])
    c = q("qc", "公司需要加密所有新建的 EBS 卷。", {"A": "启用 EBS 默认加密", "B": "手工加密"}, ["A"])
    for x, no in ((a, "1"), (b, "2"), (c, "3")):
        x.source_no = no

    pair_dup = Pair("qa", "qc", 99.0, "DUPLICATE", {"stem": 100.0}, [], 0.0)
    pair_topic = Pair("qa", "qb", 80.0, "SAME_TOPIC", {"stem": 88.0}, [], 0.0)
    res = DedupResult(
        questions={x.qid: x for x in (a, b, c)},
        pairs=[pair_dup, pair_topic],
        clusters=[Cluster("qa", ["qa", "qc"], 2, "最高相似 99.0", [pair_dup]),
                  Cluster("qb", ["qb"], 1)],
        kept=["qa", "qb"], topic_pairs=[pair_topic],
        embedding_model="test", threshold=95.0)

    md = tmp_path / "R.md"
    write_markdown(res, md)
    write_json(res, tmp_path / "kept.json", tmp_path / "detail.json")

    text = md.read_text(encoding="utf-8")
    assert "可合并的重复" in text
    assert "同一考点的不同问法" in text
    assert "#1" in text and "#2" in text          # 复习清单里两道题都在
    assert (tmp_path / "kept.json").exists()
