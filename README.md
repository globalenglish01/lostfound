# Lost & Found Intelligent Matching Platform

失物智能匹配平台。核心问题不是「两个描述像不像」，而是
**「Lost Record 和 Found Record 是否描述的是同一件物品」**——这是 Entity Matching /
Record Linkage，不是搜索。

> Embedding 负责「找得出来」，结构化属性负责「比得准」，Hard Constraint 负责「排错」，
> Ranking 负责「排顺序」，人工确认负责「最终归属」。

完整设计见 [docs/DESIGN.md](docs/DESIGN.md)。

---

## 快速开始

### 用 Docker（含 PostgreSQL + pgvector）

```bash
docker compose up -d --build
```

打开 http://localhost:8080 （API 文档 http://localhost:8080/docs ）。

灌入演示数据：

```bash
docker compose exec api python -m scripts.bootstrap --demo
```

### 本地跑（需要一个带 pgvector 的 PostgreSQL）

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

## 系统构成

```
用户/工作人员描述
   │
   ▼  ① AI Understanding（LLM Extraction + 属性标准化）
item_records（raw_description 永不被覆盖）+ item_attributes（带 source / confidence）
   │
   ▼  ② Embedding（TEXT / ATTRIBUTES / IMAGE，独立 embeddings 表 + content_hash 去重）
   ▼  ③ Hybrid Retrieval（Structured 过滤 → BM25 ∪ pgvector → RRF 融合）
   ▼  ④ Hard Constraint（IMEI/序列号/型号冲突直接淘汰）
   ▼  ⑤ Feature Matching（八维打分，category-aware 权重）
   ▼  ⑥ Scoring（可靠性加权 + 冲突惩罚 + 证据奖励）
   ▼  ⑦ Re-ranking + Explanation（LLM 分析证据，但不得改分数）
   ▼  ⑧ Human Confirmation（match_decisions = AI Feedback Loop 训练数据）
```

### 评分公式

```
S_final = clip( sum(w_i * r_i * s_i) / sum(w_i * r_i) - P_conflict + B_evidence, 0, 100 )
```

| 符号 | 含义 | 来源 |
|---|---|---|
| `w_i` | 业务重要性权重（category-aware） | `config/matching_weights.json` |
| `r_i` | 证据可靠性（Serial 1.0 → LLM 推断 0.7 → 模糊推测 0.5） | 同上 `reliability` |
| `s_i` | 实际匹配程度 0~100 | `backend/app/matching/features.py` |
| `P_conflict` | 冲突惩罚（CRITICAL / MAJOR / MINOR） | `config/conflict_rules.json` |
| `B_evidence` | 额外证据奖励，封顶 10 分 | `matching/scoring.py` |

分母只统计**可用证据**——任一侧缺失的维度不参与评分，而不是记 0 分。

---

## 三条被测试锁死的铁律

| 铁律 | 测试 |
|---|---|
| 语义 0.97 也压不过型号冲突（iPhone 15 Pro vs Pro Max） | `test_model_conflict_beats_high_semantic` |
| 黑色 vs 深灰色不是冲突；缺失不是冲突 | `test_black_vs_dark_gray_is_not_a_conflict` / `test_unknown_is_not_conflict` |
| Semantic 单独存在时不得进入自动推荐档 | `test_semantic_never_alone_decides` |

`cd backend && python -m pytest tests -q` → 28 passed。

---

## 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/lost` | 用户报失 → 抽取 → Embedding → 自动搜 FOUND |
| POST | `/api/found` | 工作人员登记拾获 → 反查历史 LOST 并预警 |
| POST | `/api/search` | 自然语言检索（Query Understanding + Hybrid Retrieval） |
| POST | `/api/ai/extract` | 单独调用抽取，前端可让用户确认 AI 理解是否正确 |
| GET | `/api/items/{id}/matches` | 已落库的候选 + 完整证据链（不重算） |
| POST | `/api/items/{id}/rematch` | 调完权重后手动重跑 |
| GET | `/api/matches/{id}/explanation` | 把证据转成人话 |
| POST | `/api/matches/{id}/decision` | 人工 CONFIRMED / REJECTED |
| GET | `/api/items/{id}/secret-questions` | 只返回该问什么，绝不返回答案 |
| POST | `/api/items/{id}/verify-secret` | 核对 Secret Attribute |
| POST | `/api/items/{id}/return` | 正式归还（AI 不参与授权） |
| GET | `/api/admin/metrics` | AI Assist Recall / Wrong Recommendation Rate |
| GET | `/api/admin/training-pairs` | 导出 Positive / Hard Negative 供 LTR 训练 |
| POST | `/api/admin/config/reload` | 权重热更新，无需重启 |

---

## Provider 配置

系统默认**零外部依赖**（`rule` + `hashing`），可离线、可在 CI 跑。
生产切到企业自有网关即可，Matching Engine 不会被任何一家模型绑死。

```bash
# LLM
LF_LLM_PROVIDER=openai_compatible
LF_LLM_BASE_URL=https://your-gateway.internal
LF_LLM_MODEL=your-model
LF_LLM_API_KEY=...

# Embedding
LF_EMBEDDING_PROVIDER=openai_compatible   # 或 local（本机 sentence-transformers）
LF_EMBEDDING_BASE_URL=https://your-gateway.internal
LF_EMBEDDING_MODEL=your-embedding-model
LF_EMBEDDING_DIM=1536
```

换 Embedding 模型时**不要 UPDATE 覆盖**：新模型以新的 `model_name/model_version`
并存写入，验证完再把旧向量置为 `DEPRECATED`（`GET /api/admin/embedding-status` 可查）。

---

## 目录

```
lostfound/
├── docs/DESIGN.md          完整技术设计（架构/表/Embedding/pgvector vs ES/算法/公式/Prompt）
├── db/schema.sql           五层表结构 + FTS 触发器 + HNSW 索引
├── config/                 权重、冲突规则、同义词（绝不写死在代码里）
├── scripts/bootstrap.py    建表 + 主数据 + 演示数据
└── backend/app/
    ├── ai/                 抽取 / 标准化 / 三个 Prompt / LLM & Embedding Provider
    ├── matching/           retrieval → conflicts → features → scoring → engine
    ├── api/                items / search / matches / admin
    └── static/index.html   演示 UI（不只显示 94%，同时展示证据）
```

---

## 版本路线

| 版本 | 内容 | 状态 |
|---|---|---|
| V1 | 登记 + 结构化属性 + 关键词 + Vector Search | 已实现 |
| V2 | Hybrid Retrieval + Matching Score + 自动推荐 + 匹配解释 | 已实现 |
| V3 | 图片 / OCR / Image Embedding | 表结构与打分维度已预留，接入 Vision 模型即可 |
| V4 | 反馈闭环 → Learning-to-Rank | `training-pairs` 已可导出，待数据量到 10k+ |
