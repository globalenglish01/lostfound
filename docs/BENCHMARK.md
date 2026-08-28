# Benchmark — 同义表达对抗评测

> 本文件由 `python -m scripts.benchmark` 自动生成，不要手工编辑。

同一件物品，只用**一种**写法登记；再用**说法完全不同**的查询去找它。
覆盖 日 / 中 / 英 × 汉字・平假名・片假名・罗马字・口语・古语。

## 运行环境

| 项 | 值 |
|---|---|
| 算法版本 | `lf-matching-v1.0` |
| LLM provider | `rule` |
| Embedding provider | `onnx` |
| Embedding model | `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` |
| 语料规模 | 247 条 FOUND 记录（240 条同类同色干扰项） |
| 查询数 | 53 |
| 耗时 | 255.7s |

## 总体结果

| 指标 | 值 |
|---|---|
| **Recall@1** | **98.1%** |
| Recall@3 | 98.1% |
| Recall@10 | 98.1% |
| MRR | 0.982 |
| 落在 Top100 之外 | 0 |

## 按语言

| 语言 | 查询数 | Recall@1 | Recall@3 |
|---|---|---|---|
| English | 7 | 100.0% | 100.0% |
| 日本語 | 32 | 96.9% | 96.9% |
| ローマ字 | 1 | 100.0% | 100.0% |
| 中文 | 13 | 100.0% | 100.0% |

## 未进入第 1 位的查询

失败案例一律如实列出，不做粉饰。

| 物品 | 表述风格 | 查询 | 排名 | 分数 |
|---|---|---|---|---|
| earbuds | `ja-hiragana` | しろいみみにつけるやつをなくした | 44 | 38.8 |

## 全部查询明细

| | 物品 | 表述风格 | 查询 | 排名 | 分数 | 召回通道 |
|---|---|---|---|---|---|---|
| ✅ | bag | `ja-katakana` | 黒いバッグをなくしました | 1 | 73.9 | structured, keyword, vec:text, vec:attr |
| ✅ | bag | `ja-katakana2` | ブラックのバックパックを落としました | 1 | 74.2 | structured, keyword, vec:text, vec:attr |
| ✅ | bag | `ja-hiragana` | くろいかばんをおとしました | 1 | 66.3 | structured, keyword, vec:text, vec:attr |
| ✅ | bag | `ja-kanji` | 黒い鞄を紛失しました | 1 | 73.5 | structured, keyword, vec:text, vec:attr |
| ✅ | bag | `ja-colloquial` | 黒いリュック落としちゃった | 1 | 74.6 | structured, keyword, vec:text, vec:attr |
| ✅ | bag | `ja-old` | 黒色の背嚢を失くしました | 1 | 74.7 | structured, keyword, vec:text, vec:attr |
| ✅ | bag | `ja-alt` | 黒いデイパックが見つかりません | 1 | 77.6 | structured, keyword, vec:text, vec:attr |
| ✅ | bag | `zh` | 丢了一个黑色双肩包 | 1 | 74.2 | structured, keyword, vec:text, vec:attr |
| ✅ | bag | `zh2` | 黑色背包不见了 | 1 | 72.4 | structured, keyword, vec:text, vec:attr |
| ✅ | bag | `en` | lost a black backpack | 1 | 68.4 | structured, keyword, vec:text, vec:attr |
| ✅ | bag | `romaji` | kuroi kaban wo nakushimashita | 1 | 60.0 | structured, keyword, vec:text, vec:attr |
| ✅ | bottle | `ja-katakana` | 青いタンブラーをなくしました | 1 | 74.0 | structured, keyword, vec:text, vec:attr |
| ✅ | bottle | `ja-katakana2` | ブルーのマイボトルを落としました | 1 | 79.2 | structured, keyword, vec:text, vec:attr |
| ✅ | bottle | `ja-hiragana` | あおいすいとうをおとしました | 1 | 61.8 | structured, keyword, vec:text, vec:attr |
| ✅ | bottle | `ja-kanji` | 青色の魔法瓶を紛失 | 1 | 75.3 | structured, keyword, vec:text, vec:attr |
| ✅ | bottle | `ja-colloquial` | 青いボトル忘れちゃった | 1 | 72.3 | structured, keyword, vec:text, vec:attr |
| ✅ | bottle | `zh` | 丢了一个蓝色保温杯 | 1 | 77.0 | structured, keyword, vec:text, vec:attr |
| ✅ | bottle | `zh2` | 蓝色不锈钢水壶不见了 | 1 | 75.1 | structured, keyword, vec:text, vec:attr |
| ✅ | bottle | `en` | lost a blue thermos flask | 1 | 75.9 | structured, keyword, vec:text, vec:attr |
| ✅ | umbrella | `ja-hiragana` | こんいろのかさをなくしました | 1 | 75.4 | structured, keyword, vec:text, vec:attr |
| ✅ | umbrella | `ja-katakana` | ネイビーのアンブレラを落としました | 1 | 77.2 | structured, keyword, vec:text, vec:attr |
| ✅ | umbrella | `ja-kanji` | 紺色の雨傘を紛失しました | 1 | 90.9 | structured, keyword, vec:text, vec:attr |
| ✅ | umbrella | `ja-colloquial` | 紺の折りたたみ忘れた | 1 | 85.2 | structured, keyword, vec:text, vec:attr |
| ✅ | umbrella | `zh` | 丢了一把深蓝色折叠伞 | 1 | 90.2 | structured, keyword, vec:text, vec:attr |
| ✅ | umbrella | `zh2` | 藏青色带花纹的伞不见了 | 1 | 91.5 | structured, keyword, vec:text, vec:attr |
| ✅ | umbrella | `en` | lost a navy folding umbrella with flower pattern | 1 | 67.9 | structured, keyword, vec:text, vec:attr |
| ✅ | wallet | `ja-hiragana` | ちゃいろのさいふをなくしました | 1 | 65.8 | structured, keyword, vec:text, vec:attr |
| ✅ | wallet | `ja-katakana` | ブラウンのウォレットを落としました | 1 | 76.1 | structured, keyword, vec:text, vec:attr |
| ✅ | wallet | `ja-kanji` | 茶色の札入れを紛失 | 1 | 80.7 | structured, keyword, vec:text, vec:attr |
| ✅ | wallet | `ja-alt` | 茶色いがま口を失くしました | 1 | 75.3 | structured, keyword, vec:text, vec:attr |
| ✅ | wallet | `ja-colloquial` | 茶色の財布どっかいった | 1 | 83.1 | structured, keyword, vec:text, vec:attr |
| ✅ | wallet | `zh` | 丢了一个棕色皮夹 | 1 | 80.5 | structured, keyword, vec:text, vec:attr |
| ✅ | wallet | `zh2` | 咖啡色真皮长款钱包不见了 | 1 | 87.2 | structured, keyword, vec:text, vec:attr |
| ✅ | wallet | `en` | lost a brown leather long wallet | 1 | 85.1 | structured, keyword, vec:text, vec:attr |
| ✅ | earbuds | `ja-katakana` | 白いワイヤレスイヤフォンをなくしました | 1 | 83.8 | structured, keyword, vec:text, vec:attr |
| ✅ | earbuds | `ja-brand` | 白いエアポッズを落としました | 1 | 69.1 | structured, keyword, vec:text, vec:attr |
| ❌ | earbuds | `ja-hiragana` | しろいみみにつけるやつをなくした | 44 | 38.8 | keyword, vec:text |
| ✅ | earbuds | `ja-alt` | 白い無線ヘッドホンを紛失 | 1 | 78.3 | structured, keyword, vec:text, vec:attr |
| ✅ | earbuds | `zh` | 丢了白色无线耳机 | 1 | 78.9 | structured, keyword, vec:text, vec:attr |
| ✅ | earbuds | `zh2` | 白色蓝牙耳机连充电盒不见了 | 1 | 83.6 | structured, keyword, vec:text, vec:attr |
| ✅ | earbuds | `en` | lost white wireless earbuds with charging case | 1 | 68.5 | structured, keyword, vec:text, vec:attr |
| ✅ | sake | `ja-polite` | お酒の瓶を忘れました | 1 | 72.8 | structured, keyword, vec:text |
| ✅ | sake | `ja-alt` | 地酒の一升瓶を置き忘れました | 1 | 74.4 | structured, keyword, vec:text |
| ✅ | sake | `ja-colloquial` | 日本酒の瓶忘れてきた | 1 | 81.5 | structured, keyword, vec:text |
| ✅ | sake | `ja-kanji` | 清酒の大瓶を紛失 | 1 | 70.3 | structured, keyword, vec:text |
| ✅ | sake | `zh` | 落了一瓶日本清酒 | 1 | 73.9 | structured, keyword, vec:text |
| ✅ | sake | `en` | left a bottle of sake | 1 | 67.9 | keyword, vec:text |
| ✅ | laptop | `ja-katakana` | シルバーのノートPCをなくしました | 1 | 77.9 | structured, keyword, vec:text, vec:attr |
| ✅ | laptop | `ja-alt` | 銀色のラップトップを落としました | 1 | 82.1 | structured, keyword, vec:text, vec:attr |
| ✅ | laptop | `ja-hiragana` | ぎんいろのぱそこんをなくした | 1 | 59.4 | structured, keyword, vec:text, vec:attr |
| ✅ | laptop | `zh` | 丢了一台银色笔记本电脑 | 1 | 80.9 | structured, keyword, vec:text, vec:attr |
| ✅ | laptop | `zh2` | 13寸银色手提电脑不见了 | 1 | 82.5 | structured, keyword, vec:text, vec:attr |
| ✅ | laptop | `en` | lost a silver 13 inch laptop | 1 | 83.1 | structured, keyword, vec:text, vec:attr |

## 复现方法

```bash
docker compose up -d --build
docker compose exec api python -m scripts.benchmark
```
