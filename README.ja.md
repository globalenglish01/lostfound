<div align="center">

# 遺失物インテリジェント・マッチング基盤

[English](README.md) · [简体中文](README.zh-CN.md) · **日本語**

遺失物管理業務のための AI マッチングエンジン。検索ではなく **Entity Matching** として設計。

</div>

---

このシステムが答えるのは「二つの説明文が似ているか」**ではありません**。

> **LOST** レコードと **FOUND** レコードは、**同一の物品**を指しているか？

これは Entity Matching / Record Linkage です。誤って「一致」と判定すれば、
他人の iPhone を別の人に引き渡すことになります。だからアーキテクチャは
次の一点を軸に組み立てられています。

> **Embedding は「見つけ出す」ため、構造化属性は「正しく突き合わせる」ため、
> Hard Constraint は「明らかな誤りを弾く」ため、Ranking は「順序を決める」ため、
> そして最終的な帰属は人間が決める。**
>
> Embedding = Retrieval であって、Decision ではない。

技術設計の全文：[docs/DESIGN.md](docs/DESIGN.md)（中国語）

---

## クイックスタート

### Docker（PostgreSQL + pgvector 同梱）

```bash
docker compose up -d --build
```

http://localhost:8080 を開きます（API ドキュメントは `/docs`）。デモデータ投入：

```bash
docker compose exec api python -m scripts.bootstrap --demo
```

### ローカル実行（pgvector 入りの PostgreSQL が必要）

```bash
pip install -r backend/requirements-dev.txt
export LF_DATABASE_URL="postgresql+psycopg://lostfound:lostfound@localhost:5432/lostfound"
python -m scripts.bootstrap --demo
uvicorn app.main:app --app-dir backend --reload
```

### テスト（データベース不要）

```bash
cd backend && python -m pytest tests -q
```

---

## パイプライン

```
利用者・職員の説明文
   │
   ▼  ① AI 理解層 —— 情報抽出 + 属性正規化
item_records（raw_description は決して上書きしない）
   + item_attributes（すべての値が source / confidence を持つ）
   │
   ▼  ② Embedding —— TEXT / ATTRIBUTES / IMAGE、独立テーブル、content_hash で重複排除
   ▼  ③ ハイブリッド検索 —— structured ∪ BM25/trigram ∪ pgvector を RRF で融合
   ▼  ④ ハード制約 —— IMEI / シリアル / 型番の矛盾は即座に除外
   ▼  ⑤ 特徴マッチング —— 8 次元、カテゴリごとの重み
   ▼  ⑥ スコアリング —— 信頼度加重 + 矛盾ペナルティ + 証拠ボーナス
   ▼  ⑦ 再ランキング + 説明生成 —— LLM は証拠を分析するがスコアは変更できない
   ▼  ⑧ 人による確認 —— match_decisions は AI フィードバックループの学習データにもなる
```

### スコアリング式

```
S_final = clip( Σ(wᵢ · rᵢ · sᵢ) / Σ(wᵢ · rᵢ)  −  P_conflict  +  B_evidence,  0, 100 )
```

| 記号 | 意味 | 定義場所 |
|---|---|---|
| `wᵢ` | 業務上の重要度（カテゴリ別に設定） | `config/matching_weights.json` |
| `rᵢ` | 証拠の信頼度（シリアル 1.0 → LLM 推論 0.7 → 曖昧な推測 0.5） | 同上 `reliability` |
| `sᵢ` | 実際の一致度 0〜100 | `backend/app/matching/features.py` |
| `P_conflict` | 矛盾ペナルティ（CRITICAL / MAJOR / MINOR） | `config/conflict_rules.json` |
| `B_evidence` | 補強証拠ボーナス、上限 10 点 | `matching/scoring.py` |

分母は**利用可能な証拠のみ**を数えます。どちらか一方に欠けている次元は
0 点として扱うのではなく、**平均から除外**します。

> **欠損 ≠ 不一致。** `UNKNOWN` は一致でも矛盾でもありません。

---

## 本当に難しいところ：同じ物なのに言い方がまったく違う

届け出る人と登録する職員が同じ語を選ぶことは、まずありません。
日本語ではこれが特に厳しく、ひとつの「かばん」が
かばん / 鞄 / カバン / バッグ / リュック / デイパック / 背嚢 になり、
さらに中国語・英語・ローマ字が加わります。

4 層で対処します。**どれか一つでも欠けると、静かに取りこぼしが発生**します。

| 層 | 役割 | カバー範囲 | コード |
|---|---|---|---|
| ① 同義語正規化 | 既知語を 100% 正確に | かばん / 鞄 / バッグ / 背包 / backpack → `bag` | `config/synonyms.json`、`ai/normalize.py` |
| ② ゼロショット分類 | 辞書に存在しない語 | コインケース、マグボトル、ボストンバッグ | `ai/classify.py` |
| ③ 多言語文ベクトル | 説明的表現・言語横断 | 「雨の日にさすやつ」「黑色双肩包」 | `ai/embedding_provider.py`（ローカル ONNX） |
| ④ 3 経路 RRF 融合 | どれか一層が外しても再現率を維持 | structured ∪ BM25/trigram ∪ vector | `matching/retrieval.py` |

**カテゴリを検索のゲートにしてはいけません。** カテゴリは**推論結果**であり、外れます。
`left a bottle of sake` では "bottle" のほうが "sake" より長いため辞書照合で勝ち、
そのレコードは水筒と判定されます。これをフィルタに使えば、その日本酒は
**恒久的に、しかも無言で**検索不能になります。
そのため `base_pool` は `record_type + status` だけで絞り、
カテゴリは RRF の 1 経路かつスコアの 1 次元にとどめ、生殺与奪の権は与えません。

### 自分で再現する

以下の数値はすべて 1 コマンドで生成されます。手書きの数字はありません。

```bash
docker compose up -d --build
docker compose exec api python -m scripts.benchmark
```

DB を初期化し、コーパスを投入し、53 クエリを実行して
[docs/BENCHMARK.md](docs/BENCHMARK.md) と機械可読の
[docs/benchmark.json](docs/benchmark.json) を出力します。

### 主張ではなく実測

```bash
python -m scripts.seed_corpus --count 240   # 同カテゴリ・同色のノイズ 240 件
python -m scripts.eval_synonyms             # 敵対的クエリ 53 件
```

日 / 中 / 英 × 漢字・ひらがな・カタカナ・ローマ字・口語・古語の
53 クエリを、247 件のレコードに対して実行：

| 段階 | Recall@1 | Recall@3 |
|---|---|---|
| 初版（辞書が不完全 + ハッシュの仮ベクトル） | 77.4% | 83.0% |
| 辞書と検索アーキテクチャを修正後 | 88.7% | 88.7% |
| 実用的な多言語ベクトル + ゼロショット + 証拠量補正 | 96.2% | 96.2% |
| 閾値を無次元化 + より強い paraphrase モデルへ | **98.1%** | **98.1%** |

失敗は 1 件残っています。取り繕わずそのまま記載します。
`しろいみみにつけるやつをなくした` —— 名詞が一つも無く、機能的な説明だけ → 44 位。

**大きいモデルほど良い、とは限りません。** 10 倍のサイズの `multilingual-e5-large` に
差し替えると結果はむしろ悪化します。e5 は非対称検索向けに学習されており、
この課題は対称な短文類似だからです。
比較を成立させるために先に潰した 2 つの交絡要因も含め、詳細は
[docs/MODEL_COMPARISON.md](docs/MODEL_COMPARISON.md) を参照してください。

| モデル | 次元 | サイズ | Recall@1 |
|---|---|---|---|
| paraphrase-multilingual-MiniLM-L12-v2 | 384 | 0.22 GB | 96.2% |
| **paraphrase-multilingual-mpnet-base-v2**（既定） | 768 | 1.00 GB | **98.1%** |
| intfloat/multilingual-e5-large | 1024 | 2.24 GB | 88.7% |

### ベクトルモデルの入れ替えで履歴を壊さない

```bash
python -m scripts.reembed --activate   # 新モデルを併存させ、検証後に旧ベクトルを DEPRECATED へ
python -m scripts.reextract            # 辞書・抽出ルール変更後に AI 理解層を再実行
```

**ベクトルをその場で `UPDATE` してはいけません。** 異なるモデルのベクトルは比較不能で、
混在させると検索品質が無言で劣化します。

---

## V3：画像とクロスモーダル検索

`item_images`、`IMAGE` ベクトル種別、`image` スコア次元は V1 の時点で確保済みでした。
V3 ではローカル ONNX CLIP でそれらを接続します。
CLIP は視覚側とテキスト側が同一のベクトル空間を共有するため、両方向に対応できます。

| 方向 | 用途 | Top1 |
|---|---|---|
| 画像 → 画像 | 利用者が自分の持ち物の古い写真を持っている | **7/7 = 100%** |
| テキスト → 画像 | 写真がなく、説明文しかない | **6/7 = 85.7%** |

本当に重要なのは 3 つ目の観点です。**同カテゴリの別個体を取り違えてはいけない**。
黒いリュック vs 赤いリュック、茶色い財布 vs 黒い財布 ——
CLIP が「これはカバンだ」としか認識できないなら、画像チャネルには価値がありません。
どちらの組も混同されませんでした。

### CLIP は英語しか解さない —— それを無償で回避する方法

CLIP ViT-B-32 は英語コーパスのみで学習されています。日本語のクエリを直接与えると 2/5、
英語なら 5/5。日本で運用するシステムにとっては致命的です。

解決策はより大きなモデルではなく、**既にある同義語正規化層の再利用**です。
抽出層は元々「黒い」「リュック」を英語の canonical（`black` / `bag`）へ正規化しています。
そこから正しい英語の CLIP プロンプトを組み立てられます。

```
「紺色の傘をなくした」
   ↓ Query Understanding（第 1 層、辞書）
color=blue, category=umbrella
   ↓ build_clip_prompt()
"a photo of a blue umbrella"
   ↓ CLIP テキスト側
```

テキスト → 画像の Top1 は 57.1% から 85.7% に、コサインは 0.21〜0.25 から 0.29〜0.33 に。
コストゼロ、新規モデルの追加もなし。詳細は
[docs/BENCHMARK_IMAGE.md](docs/BENCHMARK_IMAGE.md)。

```bash
docker compose exec api python -m scripts.gen_test_images
docker compose exec api python -m scripts.eval_images
```

---

## 同じエンジン、二つ目のドメイン：試験問題の重複判定

スコアリングの中核はドメイン非依存です。`compute_score()` は `weights` /
`dimensions` / `level_config` を明示的に受け取るため、ドメインを変えるのは
「次元と硬制約を差し替える」だけで、エンジン本体は 1 行も書き換えません。

| | 遺失物 | 試験問題 |
|---|---|---|
| 判定 | 同一の物品か | 同一の出題ポイントか |
| 次元 | カテゴリ/属性/場所/時刻/特徴/意味/キーワード/画像 | 設問/選択肢/サービス/知識点/**最適化目標**/キーワード |
| 硬制約 | IMEI・シリアル・型番の矛盾 | **選択数**、**最適化目標の排他**、サービス不一致 |
| 不変条件 | iPhone 15 Pro ≠ Pro Max | 同シナリオ・同サービス ≠ 同じ問題 |

```bash
python -m scripts.dedup_questions --sapc02 SAPC02_Chinese.txt
python -m scripts.align_bilingual  --zh SAPC02_Chinese.txt --en SAPC02_English.txt
```

### AWS SAP-C02 実問題 524 問での結果

| | |
|---|---|
| 重複除去後の独立問題 | **523** |
| 真の重複 | **1 組**（#84/#85 — 設問も選択肢も一字一句同じ） |
| 同一出題ポイントの別の問い方 | **9 組 — 残す。統合しない** |

この 9 組こそが本当に価値ある成果です。**重複を消すことが目的ではなく、
「同じ出題ポイントが何通りの聞かれ方をしているか」を可視化することが目的**です。
片方の言い回ししか覚えていなければ、本番で言い換えられた瞬間に反応できません。

### 言語横断アライメント：523 組、問題番号は一切見ずに照合

| 段階 | Top-1 |
|---|---|
| ベクトルのみ | 78.4% |
| + 構造化リランク | 88.0% |
| + 全体一対一割当 | **97.1%** |

最後の一手は見た目以上に効きます。同一試験の二つの訳版を突き合わせるのは
**二部グラフの割当問題**であって、523 回の独立した検索ではありません。
各問で argmax を取るだけでは「1 問は 1 問にしか対応しない」という強い制約を
捨てていることになります —— この一点だけで 9.1 ポイント。

### 閾値は検証して決めたもので、勘ではない

初版の閾値 82 で出た 9 組を全て人手で確認したところ、
**真の重複は 1 組、4 組は誤判定**でした。最も危険だったのは
#320（**新規**作成される EBS ボリュームを暗号化）vs #370（**既存**のものを暗号化）。
解法がまったく異なる、AWS 試験の典型的な引っかけペアです。統合すれば出題ポイントが 1 つ消えます。

そこで「新規 vs 既存」の排他的硬制約を追加し、統合の閾値を 95 に引き上げました。
唯一の真の重複は 100 点、次点は 91.6 点で、その間に明確な断絶があります。
**統合し損ねる方が、誤って統合するよりましです。**

詳細：[docs/QUESTION_DEDUP.md](docs/QUESTION_DEDUP.md)、
[docs/BILINGUAL_ALIGN.md](docs/BILINGUAL_ALIGN.md)。

---

## テストで固定した不変条件

| 不変条件 | テスト |
|---|---|
| 意味的類似度 0.97 でも型番の矛盾には勝てない（iPhone 15 Pro vs Pro Max） | `test_model_conflict_beats_high_semantic` |
| 黒 vs ダークグレーは矛盾ではない。欠損も矛盾ではない | `test_black_vs_dark_gray_is_not_a_conflict`、`test_unknown_is_not_conflict` |
| 意味的類似度だけでは自動推薦レベルに到達しない | `test_semantic_never_alone_decides` |
| 一文字の漢字エイリアスは複合語の中で一致してはならない（「包装」の「包」） | `test_single_kanji_alias_not_matched_inside_compound` |
| ……しかし本物の一文字名詞には一致すること（「黒い鞄」の「鞄」） | `test_single_kanji_alias_matched_between_kana` |
| 色が 1 つ一致しただけでは十分な証拠ではない | `test_single_attribute_match_is_not_full_confidence` |

`cd backend && python -m pytest tests -q` → **53 passed**。
うち 25 件は上記の敵対的評価で実際に踏んだ不具合の回帰テストです。

---

## API

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/api/lost` | 遺失届 → 抽出 → ベクトル生成 → FOUND を自動検索 |
| POST | `/api/found` | 拾得登録 → 過去の LOST を逆引きして職員に通知 |
| POST | `/api/search` | 自然文検索（クエリ理解 + ハイブリッド検索） |
| POST | `/api/ai/extract` | 抽出のみ。「この理解で合っていますか」を利用者に確認するため |
| GET | `/api/items/{id}/matches` | 保存済み候補と完全な証拠チェーン（再計算しない） |
| POST | `/api/items/{id}/rematch` | 重み変更後の手動再実行 |
| GET | `/api/matches/{id}/explanation` | 証拠を自然文に変換 |
| POST | `/api/matches/{id}/decision` | 人による CONFIRMED / REJECTED |
| GET | `/api/items/{id}/secret-questions` | 「何を尋ねるか」だけを返す。答えは返さない |
| POST | `/api/items/{id}/verify-secret` | 引き渡し時の Secret Attribute 照合 |
| POST | `/api/items/{id}/return` | 返却の記録（AI は承認に関与しない） |
| GET | `/api/admin/metrics` | AI Assist Recall / Wrong Recommendation Rate |
| GET | `/api/admin/training-pairs` | Learning-to-Rank 用の Positive / Hard Negative を出力 |
| POST | `/api/admin/config/reload` | 再起動なしで重みをホットリロード |

Accuracy は**意図的に**主要指標にしていません。本当に怖いのは **False Positive** ——
他人の所持品を推薦してしまうことです。

---

## コスト：ゼロ。API キー不要、有料サービス不使用

| コンポーネント | ライセンス | 費用 |
|---|---|---|
| PostgreSQL + pgvector | PostgreSQL License / MIT | 無料 |
| FastAPI / SQLAlchemy / psycopg / uvicorn | MIT / BSD | 無料 |
| fastembed + onnxruntime | Apache-2.0 / MIT | 無料 |
| `paraphrase-multilingual-mpnet-base-v2` | Apache-2.0 | 無料 |
| LLM | **既定の provider は `rule`。モデルを一切呼びません** | — |

約 1GB のベクトルモデルは**ビルド時にイメージへ焼き込まれる**ため、
稼働中のコンテナはネットワークにアクセスせず、HuggingFace のトークンも不要です。
`LF_LLM_API_KEY` と `LF_EMBEDDING_API_KEY` は既定で空、かつ使用されません。

正直に述べておく点が 2 つあります。

1. **初回ビルド時のみ**インターネットが必要です（ベースイメージとモデルの取得）。以降はオフラインで動作します。
2. **Docker Desktop は大企業では有償サブスクリプションが必要**です。これは Docker 社のライセンス方針であり、
   本プロジェクトとは無関係です。Linux の Docker Engine や Podman は無料です。

`openai_compatible` 系の provider は、企業が自社ゲートウェイを接続するための**オプション**です。
設定しなければ一度も呼ばれません。

## Provider

既定では**外部 API を一切使いません**。ルールベースの抽出器と、
ビルド時にイメージへ焼き込んだローカル ONNX 多言語ベクトルモデルで動きます。

```bash
# LLM —— 自社ゲートウェイやセルフホストモデルに差し替え
LF_LLM_PROVIDER=openai_compatible
LF_LLM_BASE_URL=https://your-gateway.internal
LF_LLM_MODEL=your-model
LF_LLM_API_KEY=...

# ベクトル —— onnx（既定）| local | openai_compatible | hashing（CI 用スタブ）
LF_EMBEDDING_PROVIDER=onnx
LF_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-mpnet-base-v2
LF_EMBEDDING_DIM=1536
```

マッチングエンジンは特定ベンダーに縛られません。LLM の役割は **証拠アナリスト**であり、
分類と説明を担当し、アルゴリズムのスコアを上書きすることは構造的に禁止されています。

---

## 第一期は Elasticsearch ではなく PostgreSQL + pgvector

ES が劣るからではありません。この業務の本質が
「構造化条件 + 意味的想起 + 厳密な同一性判定 + トランザクション整合性」であり、
「検索エンジン」ではないからです。
1 トランザクションで物品・属性・ベクトルを書き切れ、Outbox パターンも Kafka も
Indexer も Dead Letter Queue も不要です。

ES / OpenSearch が真価を発揮するのはその先 —— 数百万〜千万件規模になったとき、
あるいは日本語・中国語の形態素解析、シソーラス、あいまい検索がボトルネックになったときです。
`embeddings` / `match_candidates` / `match_evidences` が検索とマッチングを
既に分離しているため、pgvector を OpenSearch のベクトル検索に差し替えても
業務層はほぼそのままです。根拠は [docs/DESIGN.md](docs/DESIGN.md) §8。

---

## ディレクトリ構成

```
lostfound/
├── docs/DESIGN.md          技術設計の全文
├── db/schema.sql           5 層スキーマ + FTS トリガ + HNSW インデックス
├── config/                 重み・矛盾ルール・同義語（コードに直書きしない）
├── scripts/
│   ├── bootstrap.py        スキーマ + マスタデータ + デモデータ
│   ├── seed_corpus.py      評価用ノイズコーパスの生成
│   ├── eval_synonyms.py    同義表現の敵対的評価
│   ├── reembed.py          ベクトルモデルの移行
│   ├── reextract.py        AI 理解層の再実行
│   ├── dedup_questions.py  試験問題の重複判定
│   └── align_bilingual.py  日英（中英）言語横断アライメント
├── domain/questionbank/    二つ目のドメイン — 同じエンジン、別の次元
│   ├── sapc02.py           PDF 原文から問題集を解析
│   ├── features.py         問題等価性の次元 + 硬制約
│   └── dedup.py            ブロッキング → スコアリング → クラスタリング
└── backend/app/
    ├── ai/                 抽出・正規化・3 つのプロンプト・Provider
    ├── matching/           retrieval → conflicts → features → scoring → engine
    ├── api/                items / search / matches / admin
    └── static/index.html   デモ UI —— 「94%」だけでなく証拠を提示する
```

## ロードマップ

| バージョン | 内容 | 状態 |
|---|---|---|
| V1 | 登録、構造化属性、キーワード + ベクトル検索 | 完了 |
| V1.5 | ゼロショット分類、多言語ベクトル、敵対的評価 | 完了 |
| V2 | ハイブリッド検索、マッチスコア、自動推薦、説明生成 | 完了 |
| V3 | 画像 / クロスモーダル検索（画像→画像 100%、テキスト→画像 85.7%） | 完了 |
| V4 | 二つ目のドメイン：試験問題の重複判定 + 言語横断アライメント | 完了 |
| V5 | フィードバックループ → Learning-to-Rank | `training-pairs` は出力可能。確認データ約 1 万件が必要 |

## ライセンス

MIT
