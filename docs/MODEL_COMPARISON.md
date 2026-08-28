# 向量模型选型 —— 更大的模型反而更差

同一套语料（247 条记录）、同一套 53 条对抗查询、同一份代码，只换向量模型。

```bash
bash scripts/compare_models.sh
```

## 结果

| 模型 | 维度 | 体积 | Recall@1 | Recall@3 | MRR | 未进第 1 位 |
|---|---|---|---|---|---|---|
| `paraphrase-multilingual-MiniLM-L12-v2` | 384 | 0.22 GB | 96.2% | 96.2% | 0.965 | 2 |
| **`paraphrase-multilingual-mpnet-base-v2`** | **768** | **1.00 GB** | **98.1%** | **98.1%** | **0.982** | **1** |
| `intfloat/multilingual-e5-large` | 1024 | 2.24 GB | 88.7% | 94.3% | 0.914 | 6 |

**e5-large 体积是 MiniLM 的 10 倍，效果反而低了 7.5 个点。**

## 为什么

不是「大模型不行」，是**训练目标不匹配**。

| | e5 系列 | paraphrase 系列 |
|---|---|---|
| 训练目标 | 非对称检索：长文档 passage ↔ 短 query | 对称相似：句子 ↔ 句子（paraphrase / STS） |
| 使用方式 | 必须加 `query:` / `passage:` 前缀 | 无前缀 |
| 适合的场景 | 「用一句话去检索一段文档」 | 「判断两句话是不是一个意思」 |

失物匹配是**后者**：一句物品描述 vs 另一句物品描述，两侧长度、语域、信息密度都对等。
用非对称检索模型去做对称相似度，等于把工具用反了。

e5 的失败集中得很明显 —— 6 条里有 5 条是清酒（语料里该类别唯一的一条记录），
排名 2~4 名，说明它在近邻之间**区分度不足**：余弦压缩在 0.7~0.95 的窄带里，
正确答案和干扰项拉不开差距。

## 两个必须先修掉的混淆变量

第一版对比跑出来 e5 是 86.8%，但那个数字**不能用**，因为里面混了我自己的两个 bug。
修掉之后 e5 回到 88.7%，结论方向没变，但数字才是干净的。

### 1. query / passage 前缀

e5 靠前缀区分两侧角色，不加前缀会明显掉点。
`EmbeddingProvider.embed()` 因此加了 `kind` 参数：

```python
provider.embed(text, kind="passage")   # 入库的记录
provider.embed(text, kind="query")     # 检索用的查询
```

对 paraphrase 系列是无害的空操作，对 e5 是必需的。

### 2. 零样本分类器的阈值不是量纲无关的

原来的实现用**绝对余弦阈值**：

```python
MIN_SIMILARITY = 0.35
MIN_MARGIN = 0.02        # 与次优类别的差距
```

问题是不同模型的余弦分布根本不在一个量纲上：

| 模型 | 类别原型相似度的典型分布 |
|---|---|
| paraphrase 系列 | 散布在 0.1 ~ 0.7 |
| e5 系列 | 压缩在 0.7 ~ 0.95 |

同一套阈值换个模型就整体失灵：要么全部拒判（分类全空），要么全部通过（乱判）。
这会让「模型好坏」和「阈值有没有校准」混在一起，对比结论直接作废。

改成量纲无关的判据：

```python
z = (best - mean(all_similarities)) / stdev(all_similarities)
margin_sd = (best - runner_up) / stdev(all_similarities)

if z < 1.8 or margin_sd < 0.25 or best < 0.35:
    return None          # 判不准就留空，绝不硬猜
```

绝对相似度降级成兜底下限。**换模型不用重新调参**——这才是可以跨模型比较的前提。

## 结论

默认选 **`paraphrase-multilingual-mpnet-base-v2`**：

- 比 MiniLM 高 1.9 个点，代价是镜像从 1.06 GB 涨到约 1.8 GB
- 内存占用约 0.9 GB（MiniLM 约 0.64 GB）

想要更小的镜像随时可以换回去，一个环境变量的事：

```bash
LF_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
  docker compose up -d
docker compose exec api python -m scripts.reembed --activate
```

## 唯一一条三个模型都做不到的查询

```
しろいみみにつけるやつをなくした
（「戴在耳朵上的那个白色玩意儿」丢了）
```

| 模型 | 排名 |
|---|---|
| MiniLM | 35 |
| mpnet | 44 |
| e5-large | 46 |

整句话里**没有任何名词**，只有一个功能性描述（「戴在耳朵上的」）和一个颜色。
词典无从下手，零样本分类也判不出类别，只剩句向量在硬扛。

这不是调参能解决的，需要的是把「用途 / 功能」也建成一个可匹配的属性维度
（`用途: 耳につける`），或者用生成式模型把这类描述改写成规范表述再检索。
留在 V4 一并处理。
