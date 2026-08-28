# Lost & Found Intelligent Matching Platform — 技术设计 V1.0

> 本文是把原始设计讨论（混合检索 → 完整技术架构 → 数据库表设计 → Embedding 存储 →
> pgvector vs ES → 匹配算法 → 评分公式 → LLM Prompt）整理成的可实施设计文档，
> 与 `lostfound/` 下的代码一一对应。

---

## 0. 定位

不要把它定义成「带 AI 搜索的失物管理系统」，而是：

**Lost & Found Intelligent Matching Platform**

核心问题不是「两个描述像不像」，而是：

> Lost Record `L` 与 Found Record `F` 是否很可能描述的是**同一件物品**？

即 `P(match | L, F)`。

三个本质不同的子问题，必须分开设计：

| 子问题 | 学名 | 本系统模块 |
|---|---|---|
| 用户说的是什么？ | Information Extraction | `ai/extraction.py` |
| 库里哪些可能相关？ | Information Retrieval | `matching/retrieval.py` |
| 这两个是不是同一个？ | Entity Matching / Record Linkage | `matching/engine.py` |

一句话原则：

> **Embedding 负责「找得出来」，结构化属性负责「比得准」，Hard Constraint 负责「排错」，
> Ranking 负责「排顺序」，人工确认负责「最终归属」。**
>
> Embedding = Retrieval，不是 Decision。

---

## 1. 总体架构

```
                 Web / Mobile（用户端 / 工作人员端）
                              |  REST / WebSocket
                              v
                        API Gateway (Auth / RBAC)
                              |
          +-------------------+-------------------+
          v                   v                   v
    Lost Service        Found Service        Search API
          +-------------------+-------------------+
                              v
                     AI Understanding Layer
              Entity Extraction / Normalization / Embedding
                              v
                       Matching Engine
          +-------------------+-------------------+
          v                   v                   v
   Structured Search    Keyword(BM25)       Vector Search
          +-------------------+-------------------+
                              v
                      Candidate Fusion (RRF)
                              v
                       Hard Constraints
                              v
                      Feature Matching
                              v
                       Scoring Engine
                              v
                    Re-ranking (ML / LLM)
                              v
                       Explainability
                              v
                     Human Confirmation
```

漏斗（100 万条为例）：

```
1,000,000 -> Structured Filter -> 200k -> BM25 U Vector -> 1,000
          -> Hard Constraint -> 200 -> Feature Matching -> 50 -> Re-rank -> Top 20
```

### AI 层拆成 5 个模块，不要做一个「大 Agent」

1. Query Understanding — 用户到底想找什么
2. Entity Extraction — 提取物品属性
3. Semantic Retrieval — 找语义相近记录
4. Matching Engine — 判断两个记录有多像
5. Explanation Engine — 为什么认为匹配

---

## 2. 三层检索：为什么不能只用一种

| 信息 | 适合的检索 |
|---|---|
| Apple / iPhone 15 Pro / 黑色 / 新宿站 / 时间 / 透明手机壳 | Structured |
| 型号、品牌、序列号、专有名词 | Keyword (BM25) |
| 「黑色苹果手机」≈「深灰色 iPhone」、「保护壳」≈「手机套」 | Vector |

**只用 Vector 的致命问题**：`iPhone 15 Pro` 与 `iPhone 15 Pro Max` 的 embedding 极度接近，
但对失物匹配这是决定性区别。

> Semantic similarity ≠ Identity match

### Candidate Fusion — RRF

三路结果不要简单覆盖，用 Reciprocal Rank Fusion：

```
RRF(d) = sum_i  1 / (k + rank_i(d))        k = 60
```

同时被多路召回的物品自然获得更高排名。

---

## 3. Hard Constraint（硬约束）

某些属性明确冲突时**直接淘汰**，而不是「扣一点分」。

```
Lost:  iPhone 15 Pro
Found: iPhone 15 Pro Max
semantic = 0.97  ->  仍然必须 REJECT
```

但硬约束不能太多。颜色 `black` vs `dark gray` 绝不能 REJECT——用户说黑色，工作人员写深灰色很常见。

属性必须分级：**Hard Constraint / Soft Constraint / Semantic Attribute**。

### 冲突等级

| 等级 | 例子 | 惩罚 |
|---|---|---|
| CRITICAL | serial_number / IMEI / 型号 明确不一致 | 60~100，或直接 REJECT |
| MAJOR | 品牌不一致、类别不一致 | 30~50 |
| MINOR | 颜色不同、尺寸略有差异 | 5~15 |

**铁律：即使 99 分，只要存在 CRITICAL Conflict，也不能进入 HIGH。**

---

## 4. 评分公式（生产级）

### 4.1 最终公式

```
S_final = clip(  sum_i(w_i * r_i * s_i) / sum_i(w_i * r_i)
                 - P_conflict + B_evidence,  0, 100 )
```

- `w_i` 业务重要性权重（category-aware）
- `r_i` 证据可靠性（source-aware）
- `s_i` 实际匹配程度 0~100
- `P_conflict` 冲突惩罚
- `B_evidence` 额外证据奖励，**必须有上限**（`B_max = 10`），否则多个弱证据叠加会把错误匹配推到 100

注意分母是 `sum(w_i * r_i)`——这就是 **Available Evidence Normalization**：
某个特征任一侧缺失时，该项**不参与评分**，而不是记 0 分。

> Missing ≠ Mismatch。`UNKNOWN ≠ MATCH`，`UNKNOWN ≠ CONFLICT`。

### 4.2 八大维度默认权重

| 维度 | 符号 | 默认权重 |
|---|---|---|
| 类别 Category | S_c | 10% |
| 属性 Attribute | S_a | 25% |
| 地点 Location | S_l | 15% |
| 时间 Time | S_t | 10% |
| 特征 Distinctive | S_d | 15% |
| 语义 Semantic | S_s | 15% |
| 关键词 Keyword | S_k | 5% |
| 图片 Image | S_i | 5% |

Attribute 权重最高，因为：**失物匹配最有价值的是「物品身份特征」，而不是文字像不像。**

### 4.3 各维度算法

**Category**：完全相同 100 / 同一父类 70 / 相关类别 30 / 无关 0

**Attribute**（二级加权，属性内部再按 category profile 加权）：

```
S_a = sum(w_i * s_i) / sum(w_i)
```

- 手机：`IMEI 10, Serial 10, Model 8, Brand 6, Distinctive 6, Case 4, Color 2, Size 1`
- 钱包：`Contents 8, Brand 5, Pattern 5, Color 3, Material 3`
- 雨伞：`Pattern 6, Handle 5, Color 4, Brand 2, Length 2`

**Location**：优先 Location Zone，再退化到 Haversine

```
S_l = 100 * exp(-d / tau_l)        tau_l 默认 500m，category / environment aware
同一地点 100 / 同一设施 95 / 附近 80 / 同一区域 60 / 较远 30 / 无关 0
```

机场 5km 仍可能相关，办公室 500m 已经很远 → **半径必须 category / environment aware**。
`新宿站` 与 `新宿站南口` 不能只靠经纬度，要靠 location 层级树。

**Time**：

```
S_t = 100 * exp(-dt / tau_t)       tau_t 默认 24h
0h->100  1h->95.9  6h->77.9  12h->60.7  24h->36.8  48h->13.5
```

时间**不能作为绝对条件**：8/27 丢失、8/28 才被发现完全正常。

**Distinctive Feature**（信息量远高于「黑色」）：

```
明确相同 100 / 高度相似 90 / 可能相同 70 / 未知 skip / 不一致 20 / 明确冲突 0
「猫咪贴纸」 vs 「Hello Kitty 贴纸」 -> 90
```

**Semantic** = `100 * cosine`，但**永远不能单独决定匹配**。
**Keyword** = normalize(BM25)，主用于型号/品牌/序列号/专有名词。
**Image** = `100 * image_similarity`；**图片缺失时绝不能给 0**，而是不参与评分。

### 4.4 证据可靠性 r_i

| 来源 | Reliability |
|---|---|
| Serial / IMEI | 1.00 |
| 系统自动识别 | 0.95 |
| 工作人员确认 | 0.95 |
| 用户明确填写 | 0.85 |
| LLM 推断 | 0.70 |
| 从模糊描述推测 | 0.50 |

**不要用 `Score x Reliability` 整体乘**，而要让每条证据带自己的 `r_i` 进入加权平均——更稳定。
理由：用户自己也可能记错（说 iPhone 15 Pro，实际是 iPhone 15）。

### 4.5 匹配等级

| Score | Level | 系统行为 |
|---|---|---|
| 95~100 | VERY_HIGH | 强烈推荐 |
| 85~95 | HIGH | 推荐人工确认 |
| 70~85 | MEDIUM | 普通候选 |
| 50~70 | LOW | 仅在扩大搜索时显示 |
| <50 | IGNORE | 默认隐藏 |

阈值 V1 人工设定，V2 起用真实数据校准。**权重不要写死在代码里**，放 JSON 配置。

### 4.6 输出双分数

```json
{ "algorithm_score": 91.7, "llm_decision": "LIKELY_MATCH", "llm_confidence": 0.94 }
```

**Score ≠ Confidence**：Score 是「匹配程度有多高」，Confidence 是「系统对这个判断有多确定」。
**LLM 不得覆盖 Algorithm Score**（算法 52 分，LLM 觉得像，也不能改成 90）。

### 4.7 例子

```
Lost : iPhone 15 Pro / 黑色 / 透明手机壳 / 猫咪贴纸 / 新宿站 / 8-27 19:00
Found: iPhone 15 Pro / 深灰色 / 透明保护套 / 猫图案 / 新宿站 / 8-27 20:10

Category 100  Attribute 96  Location 100  Time 92  Distinctive 95  Semantic 91  Keyword 94
Image 缺失 -> 不参与，权重重新归一化
S_base ~= 96,  P = 0  ->  S_final ~= 96
```

```
Lost : iPhone 15 Pro / 黑色      Found: iPhone 15 Pro Max / 黑色
表面 Category 100 / Color 100 / Semantic 96 / Keyword 90 -> 看似 90+
Model Conflict -> P = 70 -> S_final ~= 20，或直接 REJECT
```

---

## 5. 指标：最重要的不是 Accuracy

失物匹配最怕 **False Positive**（把别人的 iPhone 推荐给用户）。

必须看：`Recall@K`、`Precision@K`、`False Positive Rate`。

两个业务指标：

- **AI Assist Recall** — AI 有没有把真正的物品找出来
- **Wrong Recommendation Rate** — AI 有没有把明显错误的物品推荐给用户（必须严格控制）

---

## 6. 数据库设计（PostgreSQL）

### 核心原则

**Lost 和 Found 不要做成两套物品表。** 统一 `item_records`，
再用 `lost_reports` / `found_reports` 描述「丢失事件」与「拾获事件」。

原始描述 `raw_description` **永远不被 AI 覆盖**，否则 AI 抽错了连重跑的依据都没有。

### 五层分层

```
1 Business Layer     item_records / lost_reports / found_reports / return_records
2 Master Data Layer  item_categories / brands / locations / attribute_definitions
3 AI Understanding   ai_analyses / item_attributes
4 AI Retrieval       embeddings / matching_runs
5 AI Decision        match_candidates / match_evidences / match_decisions
```

一句话：**业务事实存 `item_records`；AI 理解存 `item_attributes` / `ai_analyses`；
向量存 `embeddings`；「为什么匹配」存 `match_evidences`；「人最终怎么判断」存 `match_decisions`。**

### 表清单

`users` `item_categories` `brands` `locations` `attribute_definitions`
`item_records` `lost_reports` `found_reports` `item_attributes` `item_images`
`ai_analyses` `embeddings` `matching_runs`
`match_candidates` `match_evidences` `match_decisions` `return_records` `audit_logs`

第一优先级 6 张：`item_records` / `item_attributes` / `embeddings` /
`match_candidates` / `match_evidences` / `match_decisions`。

### 关键设计点

- **动态属性**：核心字段 + `item_attributes`（`value_text/number/boolean/json` + `source` + `confidence`）。
  失物种类太多（手机 IMEI、钱包卡片数、雨伞长度），全塞主表必然失控。
- **`source` 必须保留**：`USER / AI / ADMIN / OCR / VISION / IMPORT / SYSTEM`——
  「颜色=黑色 source=USER」和「颜色=黑色 source=AI conf=0.96」意义完全不同。
- **`lost_at_start` / `lost_at_end`**：用户说的是「昨天晚上 7~9 点之间」，不是「19:32:15」。
- **`locations` 自引用树**：日本→东京→新宿区→新宿站→JR→南口；
  `新宿站` / `新宿駅` / `Shinjuku Station` 最终指向同一个 `location_id`。
- **图片存 S3，DB 只存 URL / Object Key**。
- **`ai_analyses` 可追溯**：模型会升级，今天说黑色明天可能说深灰色，必须能重跑和比对版本。
- **`retrieval_score` 与 `final_score` 必须分开**：召回分数 ≠ 最终匹配分数。
- **`match_candidates` 要落库**，不要每次搜索重算：新增一条 Found 时反查历史 Lost 即可主动预警。
- **`match_decisions` 是 AI Feedback Loop**：CONFIRMED = Positive Pair，
  高分被 REJECTED = Hard Negative，未来训练自有 Ranking Model 的燃料。
- **`return_records.verification_method`**：`ID_CARD / SECRET_ATTRIBUTE / SERIAL_NUMBER /
  USER_PROOF / STAFF_CONFIRMATION`。**Secret Attribute** 很值得做：
  「钱包里有一张黄色会员卡」不展示给用户，让用户主动说出来作为归还验证证据。
- **`matching_runs`**：记录每次匹配的算法版本 / 配置 / 候选数 / 耗时，
  以后能回答「为什么这个物品当时没匹配出来」，而不是「不知道，AI 当时就是没找到」。

### 索引

```sql
-- 普通
idx_item_records_type / _status / _category
idx_lost_reports_location / idx_found_reports_location
idx_lost_reports_lost_at / idx_found_reports_found_at
-- 全文
ALTER TABLE item_records ADD COLUMN search_vector tsvector;
CREATE INDEX ... USING GIN(search_vector);
-- 向量
CREATE INDEX ... ON embeddings USING hnsw (embedding vector_cosine_ops);
```

---

## 7. Embedding 怎么存

**不要把 embedding 直接塞进 `item_records` 主表。** 单独 `embeddings` 表，
并把 **模型、版本、类型、维度、原文哈希、状态** 一起保存。

```
embeddings(id, item_id, embedding_type, model_provider, model_name, model_version,
           dimensions, content_text, content_hash, embedding, status, created_at, updated_at)
UNIQUE(item_id, embedding_type, model_name, model_version)
```

### embedding_type 至少三种

| type | 内容 |
|---|---|
| `TEXT` | 原始/标准化描述 —— 解决「说法完全不同」 |
| `ATTRIBUTES` | 结构化属性拼成的 canonical text —— 混乱描述结构化后更接近 |
| `IMAGE` | 物品图片 |

ATTRIBUTES canonical text 形如：

```
category: smartphone
brand: Apple
model: iPhone 15 Pro
color: black
case: transparent
distinctive feature: cat sticker
```

多向量融合示例：`0.91*0.30 + 0.96*0.40 + 0.94*0.30 = 0.938`

### 为什么要 content_text / content_hash / model_name / status

- `content_text`：换模型时可直接重算，不用从业务数据重新拼接
- `content_hash`（SHA256）：与库内 hash 一致就跳过，**大量减少 Embedding 调用**
- `model_name`：**不同模型产生的向量不能直接比较**，换模型不要混用
- `status`：`ACTIVE / DEPRECATED / FAILED / PROCESSING`。
  模型升级（1536 → 3072）**不要 UPDATE 覆盖**，而是并存 V1/V2，V2 验证完再切 ACTIVE

### 查询顺序

```
100万 -> Structured Filter（category / status / record_type）-> 20万 -> Vector Search -> Top 100
```

绝不是「100 万条全量 Embedding 检索」。

---

## 8. pgvector vs Elasticsearch/OpenSearch

**结论：第一期 PostgreSQL + pgvector。**

不是 ES 不好，而是失物系统的核心不是「搜索引擎」，而是
「结构化条件 + 语义召回 + 精确匹配 + 事务一致性」。

| 你们的情况 | 选择 |
|---|---|
| 0~100 万记录 | PostgreSQL + pgvector |
| 100~500 万 | 两者都可评估 |
| 500 万~1000 万+ | 倾向 ES / OpenSearch |
| 极复杂全文 / 中日文复杂分词 / 大量 Fuzzy / 复杂 Geo | ES / OpenSearch |
| 强事务要求 / AI Matching 为核心 / 快速 MVP | PostgreSQL + pgvector |
| 企业已有 ES 平台 | 直接利用 |

**ES 的代价**：PostgreSQL 更新成功、ES 更新失败怎么办？你会被迫引入
`Outbox Pattern + Kafka/SQS + Indexer + Retry + DLQ`，系统复杂度立刻上升。
而 pgvector 是一个事务：`BEGIN; INSERT item; INSERT attributes; INSERT embedding; COMMIT;`

**ES 真正强在**：分词、词典、同义词、Fuzzy、词干、Highlight、Facet——
尤其日文（`黒い財布` / `ブラックの長財布` / `黒色のさいふ` / `黒いお財布`）。

**V2 演进**：PostgreSQL 作为 Source of Truth，通过 Outbox/Event 同步到
OpenSearch 作为 Search Index。AWS 上优先 **Amazon OpenSearch Service** 而非自维护集群。

因为 `embeddings / match_candidates / match_evidences` 把 Retrieval 与 Matching 解耦了，
以后把 pgvector 换成 OpenSearch Vector Search，业务层基本不用推倒重来。

---

## 9. LLM Prompt 设计（三个，不是一个）

| # | Prompt | 职责 |
|---|---|---|
| 1 | Extraction | 自然语言 → 结构化物品信息 |
| 2 | Match Analysis | 分析 Lost/Found 的证据与冲突（**生产核心**） |
| 3 | Explanation | 把已算好的结果转成人话 |

### 1 Extraction 关键规则

- 只抽取明确陈述或强烈暗示的信息，**NEVER invent**；未知返回 `null`
- 每个属性都要给 `value / original / confidence / source_type`
- `source_type` 属于 `{EXPLICIT, INFERRED, UNCERTAIN}`
- **不做匹配判断**，不把语义相似当作事实同一
- 保留 distinctive physical characteristics

### 2 Match Analysis — 证据优先级

```
LEVEL 1 强身份证据   serial / IMEI / asset ID / 唯一物理标记
LEVEL 2 强独特证据   异常贴纸 / 独特损伤 / 刻字 / 改装 / 独特图案
LEVEL 3 具体产品属性 精确型号 / 品牌 / 品类 / 尺寸 / 材质
LEVEL 4 上下文证据   地点 / 时间 / 颜色 / 配件
LEVEL 5 弱语义证据   一般文本相似 / 通用外观
```

铁律：

- 高语义相似度 **MUST NOT** 覆盖强身份冲突
- serial/IMEI 明确不同 → `NOT_MATCH`（除非有强证据表明是数据错误）
- 型号明确不同 → major conflict penalty
- **"Unknown" is NOT "different"**，缺失信息不得当作负面证据
- 单独的同色 / 同地点 / 同时间都是弱证据；**多个独立的 distinctive feature 同时命中才是强证据**
- 输出必须区分 supporting / conflicting / unknown 三类证据

决策枚举：`MATCH / LIKELY_MATCH / POSSIBLE_MATCH / UNLIKELY_MATCH / NOT_MATCH`
建议动作：`AUTO_RECOMMEND / HUMAN_REVIEW / DO_NOT_RECOMMEND`

### 3 Explanation

- **不得重算分数、不得改变决策、不得编造证据**
- 最强证据放最前，冲突必须明说，不暴露内部模型名

### Evidence Matrix（关系类型固定枚举，直接落 `match_evidences`）

```
EXACT_MATCH / SEMANTIC_MATCH / PARTIAL_MATCH / UNKNOWN /
MINOR_CONFLICT / MAJOR_CONFLICT / CRITICAL_CONFLICT
```

### 防幻觉红线

```
Lost: 黑色钱包        Found: 黑色钱包, Prada
LLM 绝不能说「用户的 Prada 钱包与拾获的 Prada 钱包一致」——Lost 记录根本没提 Prada。
```

### LLM 的长期定位

V1：Rule + Embedding + LLM。
积累 10,000+ Human Confirmed Matches 后训练 Learning-to-Rank
（Logistic Regression → XGBoost/LightGBM → LTR），
LLM 从核心 Ranking 退到 **Extraction / Explanation / Edge Cases**。

---

## 10. 双向匹配 + 反馈闭环

```
用户报失 -> LLM Extraction -> Embedding -> 保存 -> 触发 MATCH_LOST
        -> 搜索所有 FOUND -> Hybrid Retrieval -> Top100 -> Matching -> Top10 -> Re-rank -> Top3 -> 通知用户

工作人员登记拾获 -> ... -> 触发 MATCH_FOUND
        -> 反查历史 LOST -> #10231 94% / #10287 62% / #10452 41% -> 高概率匹配 -> 人工确认
```

事件：`LOST_CREATED / FOUND_CREATED / RECORD_UPDATED / IMAGE_UPLOADED / MATCH_CONFIRMED / ITEM_RETURNED`

**AI 可以 Recommend，但不得 Authorize。** 护照 / 身份证 / 钱包 / 手机 / 贵重物品
一律：AI 推荐 → 工作人员确认 → 用户身份验证 → 正式归还。

用户反馈（不是我的 / 就是我的）不能浪费，全部落 `match_decisions`。

---

## 11. UI：不要只显示「94%」

```
高度疑似匹配                        匹配度 94%
Apple iPhone
[v] 型号一致：iPhone 15 Pro
[v] 手机壳一致：透明
[v] 独特特征一致：背面猫咪贴纸
[v] 地点一致：新宿站
[v] 时间相近：相差约 70 分钟
[!] 颜色轻微差异：用户「黑色」/ 拾获「深灰色」
```

解释必须来自 `match_evidences` 里**系统真实算出来的证据**，LLM 只做自然语言转换。

---

## 12. 技术栈与版本演进

| 层 | 技术 |
|---|---|
| Frontend | Next.js / React |
| Backend | FastAPI |
| DB | PostgreSQL |
| Vector | pgvector |
| Keyword | PostgreSQL FTS →（V2）OpenSearch |
| Cache | Redis |
| Async | Celery / SQS |
| Object Storage | S3 |
| LLM / Embedding | 可插拔 Provider |
| Auth | OAuth2 / OIDC |
| Monitoring | OpenTelemetry |

AWS：`CloudFront -> ALB -> ECS/EKS -> FastAPI -> RDS(pgvector) / ElastiCache / S3 / SQS -> AI Worker`

| 版本 | 内容 |
|---|---|
| V1 可用版 | 登记 + 结构化属性 + 关键词 + 基础 Vector Search |
| V2 智能匹配版 | Hybrid Retrieval + Matching Score + 自动推荐 + 匹配解释 |
| V3 多模态版 | 图片 / OCR / Image Embedding / Vision |
| V4 学习型平台 | 反馈闭环 + Positive/Negative Pair + Learning-to-Rank + 动态权重 |

---

## 13. 本仓库实现对照表

| 设计条目 | 代码 |
|---|---|
| 五层表结构 | `db/schema.sql` |
| 三路召回 + RRF | `backend/app/matching/retrieval.py` |
| 八维特征打分 | `backend/app/matching/features.py` |
| 冲突分级与惩罚 | `backend/app/matching/conflicts.py` |
| 最终评分公式 | `backend/app/matching/scoring.py` |
| 端到端匹配编排 | `backend/app/matching/engine.py` |
| 三个 Prompt | `backend/app/ai/prompts.py` |
| LLM / Embedding Provider | `backend/app/ai/llm_provider.py`, `embedding_provider.py` |
| Category-aware 权重 | `config/matching_weights.json`, `config/attribute_weights.json` |
| 冲突规则 | `config/conflict_rules.json` |
| 同义词 / 标准化 | `config/synonyms.json`, `backend/app/ai/normalize.py` |
