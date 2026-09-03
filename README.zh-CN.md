<div align="center">

# 失物智能匹配平台

[English](README.md) · **简体中文** · [日本語](README.ja.md)

面向失物招领业务的 AI 匹配引擎 —— 按 **Entity Matching** 来做，而不是按搜索来做。

</div>

---

这个系统回答的**不是**「两个描述像不像」，而是：

> 一条 **LOST** 记录和一条 **FOUND** 记录，是不是**同一件物品**？

这是 Entity Matching / Record Linkage。判错方向的代价是把别人的 iPhone 交给错的人，
所以整套架构围绕一条原则：

> **Embedding 负责「找得出来」，结构化属性负责「比得准」，Hard Constraint 负责「排错」，
> Ranking 负责「排顺序」，人工确认负责「最终归属」。**
>
> Embedding = Retrieval，永远不是 Decision。

完整技术设计：[docs/DESIGN.md](docs/DESIGN.md)

---

## 快速开始

### Docker（含 PostgreSQL + pgvector）

```bash
docker compose up -d --build
```

打开 http://localhost:8080 （API 文档在 `/docs`）。灌入演示数据：

```bash
docker compose exec api python -m scripts.bootstrap --demo
```

### 本地运行（需要带 pgvector 的 PostgreSQL）

```bash
pip install -r backend/requirements-dev.txt
export LF_DATABASE_URL="postgresql+psycopg://lostfound:lostfound@localhost:5432/lostfound"
python -m scripts.bootstrap --demo
uvicorn app.main:app --app-dir backend --reload
```

### 跑测试（不需要数据库）

```bash
cd backend && python -m pytest tests -q
```

---

## 流水线

```
用户 / 工作人员的描述
   │
   ▼  ① AI 理解层 —— 信息抽取 + 属性标准化
item_records（raw_description 永不被覆盖）
   + item_attributes（每个值都带 source / confidence）
   │
   ▼  ② Embedding —— TEXT / ATTRIBUTES / IMAGE，独立表，content_hash 去重
   ▼  ③ 混合检索 —— structured ∪ BM25/trigram ∪ pgvector，RRF 融合
   ▼  ④ 硬约束 —— IMEI / 序列号 / 型号冲突直接淘汰
   ▼  ⑤ 特征匹配 —— 八个维度，category-aware 权重
   ▼  ⑥ 评分 —— 可靠性加权 + 冲突惩罚 + 证据奖励
   ▼  ⑦ 精排 + 解释 —— LLM 分析证据，但绝不允许改分数
   ▼  ⑧ 人工确认 —— match_decisions 同时是 AI 反馈闭环的训练数据
```

### 评分公式

```
S_final = clip( Σ(wᵢ · rᵢ · sᵢ) / Σ(wᵢ · rᵢ)  −  P_conflict  +  B_evidence,  0, 100 )
```

| 符号 | 含义 | 来源 |
|---|---|---|
| `wᵢ` | 业务重要性权重（按类别配置） | `config/matching_weights.json` |
| `rᵢ` | 证据可靠性（序列号 1.0 → LLM 推断 0.7 → 模糊推测 0.5） | 同上，`reliability` |
| `sᵢ` | 实际匹配程度 0~100 | `backend/app/matching/features.py` |
| `P_conflict` | 冲突惩罚（CRITICAL / MAJOR / MINOR） | `config/conflict_rules.json` |
| `B_evidence` | 佐证奖励，硬性封顶 10 分 | `matching/scoring.py` |

分母只统计**可用证据**。任一侧缺失的维度**不参与平均**，而不是记 0 分。

> **Missing ≠ Mismatch。** `UNKNOWN` 既不是匹配，也不是冲突。

---

## 真正的难点：同一件东西，说法完全不同

报失的人和登记的人几乎不会用同一个词。日语尤其残酷 —— 同一个「包」可以是
かばん / 鞄 / カバン / バッグ / リュック / デイパック / 背嚢，
这还没算上中文、英文和罗马字。

四层来解决，**缺任何一层都会产生静默漏召**：

| 层 | 作用 | 覆盖什么 | 代码 |
|---|---|---|---|
| ① 同义词标准化 | 已知词 100% 准确 | かばん / 鞄 / バッグ / 背包 / backpack → `bag` | `config/synonyms.json`、`ai/normalize.py` |
| ② 零样本类别分类 | 词典里根本没有的词 | コインケース、マグボトル、ボストンバッグ | `ai/classify.py` |
| ③ 多语言句向量 | 描述性表达、跨语言 | 「雨の日にさすやつ」「黑色双肩包」 | `ai/embedding_provider.py`（本机 ONNX） |
| ④ 三路 RRF 融合 | 任一层失手仍能召回 | structured ∪ BM25/trigram ∪ vector | `matching/retrieval.py` |

**类别绝不能做检索门禁。** 类别是**推断**出来的，会错。
`left a bottle of sake` 里 `bottle` 比 `sake` 长，词典匹配会赢，记录被判成水壶 ——
一旦拿它当过滤条件，那瓶清酒就**永久且静默地**召回不到。
所以 `base_pool` 只按 `record_type + status` 收缩；类别只是 RRF 的一路 + 一个评分维度，
绝不是生杀大权。

### 自己跑一遍

下面每个数字都由一条命令产出，没有一个是手写的：

```bash
docker compose up -d --build
docker compose exec api python -m scripts.benchmark
```

它会清库、灌语料、跑完 53 条查询，生成
[docs/BENCHMARK.md](docs/BENCHMARK.md) 和机器可读的
[docs/benchmark.json](docs/benchmark.json)。

### 实测，不是宣称

```bash
python -m scripts.seed_corpus --count 240   # 240 条同类同色干扰项
python -m scripts.eval_synonyms             # 53 条对抗查询
```

53 条查询覆盖 日 / 中 / 英 × 汉字、平假名、片假名、罗马字、口语、古语，
在 247 条记录上测：

| 阶段 | Recall@1 | Recall@3 |
|---|---|---|
| 初版（词典残缺 + 哈希占位向量） | 77.4% | 83.0% |
| 修完词典与检索架构后 | 88.7% | 88.7% |
| 加真实多语言向量 + 零样本分类 + 证据厚度修正 | 96.2% | 96.2% |
| 分类阈值改成量纲无关 + 换更强的 paraphrase 模型 | **98.1%** | **98.1%** |

仍有 1 条失败，如实记录、不做粉饰：
`しろいみみにつけるやつをなくした` —— 整句没有任何名词，只有一个功能性描述 → 排 44。

**更大的模型反而更差。** 换成体积 10 倍的 `multilingual-e5-large` 结果不升反降 ——
e5 是为「非对称检索」训练的，而这里是对称的短文本相似度。
完整分析（含对比前必须先修掉的两个混淆变量）见
[docs/MODEL_COMPARISON.md](docs/MODEL_COMPARISON.md)。

| 模型 | 维度 | 体积 | Recall@1 |
|---|---|---|---|
| paraphrase-multilingual-MiniLM-L12-v2 | 384 | 0.22 GB | 96.2% |
| **paraphrase-multilingual-mpnet-base-v2**（默认） | 768 | 1.00 GB | **98.1%** |
| intfloat/multilingual-e5-large | 1024 | 2.24 GB | 88.7% |

### 换向量模型不要覆盖历史

```bash
python -m scripts.reembed --activate   # 新模型并存写入，验证后再把旧向量置 DEPRECATED
python -m scripts.reextract            # 词典 / 抽取规则改动后重跑 AI 理解层
```

**绝不要原地 `UPDATE` 向量。** 不同模型产出的向量不能直接比较，混用会让检索静默劣化。

---

## V3：图片与跨模态检索

`item_images`、`IMAGE` 向量类型、`image` 评分维度在 V1 就已预留，V3 用本机 ONNX CLIP 接通。
CLIP 的视觉侧与文本侧共享同一个向量空间，因此两个方向都支持：

| 方向 | 场景 | Top1 |
|---|---|---|
| 图 → 图 | 用户有自己物品的旧照片 | **7/7 = 100%** |
| 文 → 图 | 用户没有照片，只有一句描述 | **6/7 = 85.7%** |

真正关键的是第三条：**同类不同件绝不能被错认**。
黑色背包 vs 红色背包、棕色钱包 vs 黑色钱包 ——
如果 CLIP 只认得「这是个包」，图像通道就毫无价值。两组都没有混淆。

### CLIP 只懂英文 —— 以及怎么零成本绕过

CLIP ViT-B-32 只在英文语料上训练过。日文 query 直接问它命中 2/5，英文 5/5。
对一个部署在日本的系统，这是硬伤。

解法不是换更大的模型，而是**复用已经存在的同义词标准化层**：
抽取层本来就把「黒い」「リュック」归一成了英文 canonical（`black` / `bag`），
于是可以从结构化属性拼出一句规范的英文 CLIP prompt：

```
「紺色の傘をなくした」
   ↓ Query Understanding（第 ① 层，词典）
color=blue, category=umbrella
   ↓ build_clip_prompt()
"a photo of a blue umbrella"
   ↓ CLIP 文本侧
```

文 → 图 Top1 从 57.1% 提到 85.7%，余弦从 0.21~0.25 提到 0.29~0.33。
零成本，没有引入任何新模型。完整报告见
[docs/BENCHMARK_IMAGE.md](docs/BENCHMARK_IMAGE.md)。

```bash
docker compose exec api python -m scripts.gen_test_images
docker compose exec api python -m scripts.eval_images
```

---

## 同一个引擎，第二个领域：题库查重

打分内核与领域无关。`compute_score()` 接受显式的 `weights` / `dimensions` /
`level_config`，换个领域只需要换维度和硬约束，引擎本身一行不用改。

| | 失物 | 题库 |
|---|---|---|
| 判定 | 是不是同一件物品 | 是不是同一个考点 |
| 维度 | 类别/属性/地点/时间/特征/语义/关键词/图像 | 题干/选项/服务/知识点/**优化目标**/关键词 |
| 硬约束 | IMEI、序列号、型号冲突 | **要求选几项**、**优化目标互斥**、服务不相交 |
| 铁律 | iPhone 15 Pro ≠ Pro Max | 同场景同服务 ≠ 同一道题 |

```bash
python -m scripts.dedup_questions --sapc02 SAPC02_Chinese.txt
python -m scripts.align_bilingual  --zh SAPC02_Chinese.txt --en SAPC02_English.txt
```

### 524 道 AWS SAP-C02 真题上的结果

| | |
|---|---|
| 去重后独立考题 | **523** |
| 真重复 | **1 组**（#84/#85，题干与选项逐字相同） |
| 同考点不同问法 | **9 对 —— 保留，不合并** |

那 9 对才是真正有用的产出。**去重不是目的，把「同一个考点被换了几种问法」摆出来才是。**
只记住其中一种说法，考场上换个问法就不认得了。

### 跨语言对齐，523 对，全程不看题号

| 阶段 | Top1 |
|---|---|
| 仅向量 | 78.4% |
| + 结构化重排 | 88.0% |
| + 全局一一指派 | **97.1%** |

最后一步比看上去重要：同一场考试的两个译本对齐是**二分图指派**问题，
不是 523 次独立检索。每道题各自取 argmax，等于扔掉「每道题只对应一道」这个强约束
—— 光这一步就值 9.1 个点。

### 阈值是核验出来的，不是拍的

初版阈值 82 跑出 9 组，我逐组人工核对：**只有 1 组是真重复，4 组是误判。**
最危险的是 #320（加密所有**新建**的 EBS 卷）vs #370（加密所有**已有**的卷）——
解法完全不同，是 AWS 考试的经典陷阱对，合并掉等于删掉一个考点。

于是加了「新建 vs 存量」互斥硬约束，并把合并门槛提到 95：
唯一的真重复得 100 分，第二名只有 91.6，中间有明显断层。**宁可漏合，也不能合错。**

完整分析：[docs/QUESTION_DEDUP.md](docs/QUESTION_DEDUP.md)、
[docs/BILINGUAL_ALIGN.md](docs/BILINGUAL_ALIGN.md)。

---

## 被测试锁死的不变量

| 不变量 | 测试 |
|---|---|
| 语义 0.97 也压不过型号冲突（iPhone 15 Pro vs Pro Max） | `test_model_conflict_beats_high_semantic` |
| 黑色 vs 深灰色不是冲突；缺失不是冲突 | `test_black_vs_dark_gray_is_not_a_conflict`、`test_unknown_is_not_conflict` |
| 只有语义证据时不得进入自动推荐档 | `test_semantic_never_alone_decides` |
| 单字汉字别名不得命中复合词（「包装」里的「包」） | `test_single_kanji_alias_not_matched_inside_compound` |
| ……但必须仍能命中真正的单字物品名（「黒い鞄」里的「鞄」） | `test_single_kanji_alias_matched_between_kana` |
| 只对上一个颜色不算充分证据 | `test_single_attribute_match_is_not_full_confidence` |

`cd backend && python -m pytest tests -q` → **53 passed**，
其中 25 条是上面对抗评测中踩到的坑固化成的回归用例。

---

## API

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/lost` | 用户报失 → 抽取 → 建向量 → 自动搜 FOUND |
| POST | `/api/found` | 工作人员登记拾获 → 反查历史 LOST 并预警 |
| POST | `/api/search` | 自然语言检索（查询理解 + 混合检索） |
| POST | `/api/ai/extract` | 单独调用抽取，让前端问用户「我们理解得对吗」 |
| GET | `/api/items/{id}/matches` | 已落库的候选 + 完整证据链（不重算） |
| POST | `/api/items/{id}/rematch` | 调完权重后手动重跑 |
| GET | `/api/matches/{id}/explanation` | 把证据转成人话 |
| POST | `/api/matches/{id}/decision` | 人工 CONFIRMED / REJECTED |
| GET | `/api/items/{id}/secret-questions` | 只返回「该问什么」，绝不返回答案 |
| POST | `/api/items/{id}/verify-secret` | 归还时核对 Secret Attribute |
| POST | `/api/items/{id}/return` | 记录归还（AI 不参与授权） |
| GET | `/api/admin/metrics` | AI Assist Recall / Wrong Recommendation Rate |
| GET | `/api/admin/training-pairs` | 导出 Positive / Hard Negative 供 LTR 训练 |
| POST | `/api/admin/config/reload` | 权重热更新，无需重启 |

Accuracy **刻意**不是核心指标。真正要盯的失败是 **False Positive** —— 把别人的东西推荐给用户。

---

## 成本：零。不需要 API key，不依赖任何付费服务

| 组件 | 许可证 | 费用 |
|---|---|---|
| PostgreSQL + pgvector | PostgreSQL License / MIT | 免费 |
| FastAPI / SQLAlchemy / psycopg / uvicorn | MIT / BSD | 免费 |
| fastembed + onnxruntime | Apache-2.0 / MIT | 免费 |
| `paraphrase-multilingual-mpnet-base-v2` | Apache-2.0 | 免费 |
| LLM | **默认 provider 是 `rule`，根本不调用任何模型** | — |

约 1GB 的向量模型在 **构建时就打进镜像**，运行中的容器不联网，也不需要 HuggingFace token。
`LF_LLM_API_KEY` 和 `LF_EMBEDDING_API_KEY` 默认为空且不会被使用。

两点如实说明：

1. **首次构建**需要联网（拉基础镜像 + 下模型），之后完全离线可用。
2. **Docker Desktop 对大型企业需要商业订阅** —— 这是 Docker 自己的授权政策，与本项目无关。
   Linux 上的 Docker Engine 和 Podman 完全免费。

`openai_compatible` 那些 provider 是留给企业接自己网关用的，属于可选项；
不配置就永远不会被调用。

## Provider

默认**零外部 API**：规则抽取器 + 构建期打进镜像的本机 ONNX 多语言向量模型。

```bash
# LLM —— 换成你自己的网关或自托管模型
LF_LLM_PROVIDER=openai_compatible
LF_LLM_BASE_URL=https://your-gateway.internal
LF_LLM_MODEL=your-model
LF_LLM_API_KEY=...

# 向量 —— onnx（默认）| local | openai_compatible | hashing（CI 占位）
LF_EMBEDDING_PROVIDER=onnx
LF_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-mpnet-base-v2
LF_EMBEDDING_DIM=1536
```

匹配引擎不绑定任何厂商。LLM 的定位是**证据分析师**：负责分类和解释，
在结构上被禁止覆盖算法分数。

---

## 第一期选 PostgreSQL + pgvector，而不是 Elasticsearch

不是因为 ES 不好，而是这个场景的核心是
「结构化条件 + 语义召回 + 精确匹配 + 事务一致性」，不是「搜索引擎」。
一个事务就能写完物品、属性和向量，不需要 Outbox、Kafka、Indexer、死信队列。

ES / OpenSearch 的价值在后面才体现 —— 数据量到千万级，
或者中日文分词、同义词词典、Fuzzy 搜索成为瓶颈时。
因为 `embeddings` / `match_candidates` / `match_evidences` 已经把检索和匹配解耦，
以后把 pgvector 换成 OpenSearch 向量检索，业务层基本不用动。
推理过程见 [docs/DESIGN.md](docs/DESIGN.md) §8。

---

## 目录

```
lostfound/
├── docs/DESIGN.md          完整技术设计
├── db/schema.sql           五层表结构 + FTS 触发器 + HNSW 索引
├── config/                 权重、冲突规则、同义词 —— 绝不写死在代码里
├── scripts/
│   ├── bootstrap.py        建表 + 主数据 + 演示数据
│   ├── seed_corpus.py      生成评测用的干扰语料
│   ├── eval_synonyms.py    同义表达对抗评测
│   ├── reembed.py          向量模型迁移
│   ├── reextract.py        重跑 AI 理解层
│   ├── dedup_questions.py  题库查重
│   └── align_bilingual.py  中英文跨语言对齐
├── domain/questionbank/    第二个领域 —— 同一引擎，不同维度
│   ├── sapc02.py           从 PDF 原文解析题库
│   ├── features.py         题目等价维度 + 硬约束
│   └── dedup.py            分块召回 → 打分 → 聚类
└── backend/app/
    ├── ai/                 抽取、标准化、三个 Prompt、Provider
    ├── matching/           retrieval → conflicts → features → scoring → engine
    ├── api/                items / search / matches / admin
    └── static/index.html   演示 UI —— 展示证据，而不只是「94%」
```

## 版本路线

| 版本 | 内容 | 状态 |
|---|---|---|
| V1 | 登记、结构化属性、关键词 + 向量检索 | 已完成 |
| V1.5 | 零样本分类、多语言向量、对抗评测 | 已完成 |
| V2 | 混合检索、匹配评分、自动推荐、匹配解释 | 已完成 |
| V3 | 图片 / 跨模态检索（图→图 100%，文→图 85.7%） | 已完成 |
| V4 | 第二个领域：题库查重 + 跨语言对齐 | 已完成 |
| V5 | 反馈闭环 → Learning-to-Rank | `training-pairs` 已可导出，待积累约 1 万条确认数据 |

## 许可证

MIT
