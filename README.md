<div align="center">

# Lost & Found Intelligent Matching Platform

**English** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

An AI matching engine for lost-and-found operations — built as **entity matching**, not as search.

</div>

---

The question this system answers is **not** "do these two descriptions look alike?"
It is:

> Do a **LOST** record and a **FOUND** record refer to **the same physical object**?

That is entity matching / record linkage. Getting it wrong in the confident direction means
handing someone else's iPhone to the wrong person, so the architecture is built around
one principle:

> **Embeddings decide what gets *retrieved*. Structured attributes decide what *matches*.
> Hard constraints decide what gets *rejected*. Ranking decides the *order*.
> A human decides who *gets the item back*.**
>
> Embedding = Retrieval, never Decision.

Full technical design: [docs/DESIGN.md](docs/DESIGN.md) (Chinese).

---

## Quick start

### Docker (includes PostgreSQL + pgvector)

```bash
docker compose up -d --build
```

Open http://localhost:8080 (API docs at `/docs`). Seed demo data:

```bash
docker compose exec api python -m scripts.bootstrap --demo
```

### Local (requires a PostgreSQL with pgvector)

```bash
pip install -r backend/requirements-dev.txt
export LF_DATABASE_URL="postgresql+psycopg://lostfound:lostfound@localhost:5432/lostfound"
python -m scripts.bootstrap --demo
uvicorn app.main:app --app-dir backend --reload
```

### Tests (no database required)

```bash
cd backend && python -m pytest tests -q
```

---

## Pipeline

```
User / staff description
   │
   ▼  1. AI Understanding — extraction + attribute normalization
item_records (raw_description is never overwritten)
   + item_attributes (every value carries source / confidence)
   │
   ▼  2. Embedding — TEXT / ATTRIBUTES / IMAGE, separate table, content_hash dedup
   ▼  3. Hybrid Retrieval — structured ∪ BM25/trigram ∪ pgvector, fused with RRF
   ▼  4. Hard Constraints — IMEI / serial / model conflicts eliminate outright
   ▼  5. Feature Matching — eight dimensions, category-aware weights
   ▼  6. Scoring — reliability weighting + conflict penalty + evidence bonus
   ▼  7. Re-ranking + Explanation — the LLM analyses evidence but never edits the score
   ▼  8. Human Confirmation — match_decisions doubles as AI feedback-loop training data
```

### The scoring formula

```
S_final = clip( Σ(wᵢ · rᵢ · sᵢ) / Σ(wᵢ · rᵢ)  −  P_conflict  +  B_evidence,  0, 100 )
```

| Symbol | Meaning | Source |
|---|---|---|
| `wᵢ` | Business importance, category-aware | `config/matching_weights.json` |
| `rᵢ` | Evidence reliability (serial 1.0 → LLM-inferred 0.7 → vague guess 0.5) | same file, `reliability` |
| `sᵢ` | Actual match degree, 0–100 | `backend/app/matching/features.py` |
| `P_conflict` | Conflict penalty (CRITICAL / MAJOR / MINOR) | `config/conflict_rules.json` |
| `B_evidence` | Corroborating-evidence bonus, hard-capped at 10 | `matching/scoring.py` |

The denominator counts **available evidence only**. A dimension missing on either side is
excluded from the average rather than scored as zero.

> **Missing ≠ Mismatch.** `UNKNOWN` is neither a match nor a conflict.

---

## The hard part: the same object, described completely differently

The person who files the report and the person who logs the item almost never pick the
same word. Japanese makes this brutal — one bag can be
かばん / 鞄 / カバン / バッグ / リュック / デイパック / 背嚢, before you even add
Chinese, English, or rōmaji.

Four layers handle it. Drop any one and you get **silent** recall failures:

| Layer | Job | Covers | Code |
|---|---|---|---|
| 1. Synonym normalization | 100% precision on known words | かばん / 鞄 / バッグ / 背包 / backpack → `bag` | `config/synonyms.json`, `ai/normalize.py` |
| 2. Zero-shot classification | Words the dictionary has never seen | コインケース, マグボトル, ボストンバッグ | `ai/classify.py` |
| 3. Multilingual sentence vectors | Paraphrase and cross-language | 「雨の日にさすやつ」, 「黑色双肩包」 | `ai/embedding_provider.py` (local ONNX) |
| 4. Three-channel RRF fusion | Survives any single layer failing | structured ∪ BM25/trigram ∪ vector | `matching/retrieval.py` |

**Category must never gate retrieval.** Category is *inferred*, so it is sometimes wrong.
In `left a bottle of sake`, "bottle" is longer than "sake" and wins the dictionary match —
the record becomes a water bottle. Use that as a filter and the sake is unreachable
forever, silently. So `base_pool` narrows only by `record_type + status`; category is one
RRF channel and one scoring dimension, never a gate.

### Reproduce it yourself

Every number below is produced by one command — nothing is hand-written:

```bash
docker compose up -d --build
docker compose exec api python -m scripts.benchmark
```

It wipes the database, seeds the corpus, runs all 53 queries, and writes
[docs/BENCHMARK.md](docs/BENCHMARK.md) plus a machine-readable
[docs/benchmark.json](docs/benchmark.json).

### Measured, not asserted

```bash
python -m scripts.seed_corpus --count 240   # 240 same-category, same-colour distractors
python -m scripts.eval_synonyms             # 53 adversarial queries
```

53 queries across ja / zh / en × kanji, hiragana, katakana, rōmaji, colloquial, archaic —
against 247 records:

| Stage | Recall@1 | Recall@3 |
|---|---|---|
| First cut (incomplete dictionary + placeholder hash vectors) | 77.4% | 83.0% |
| After fixing the dictionary and the retrieval architecture | 88.7% | 88.7% |
| With real multilingual vectors + zero-shot + evidence-thickness correction | 96.2% | 96.2% |
| After scale-free classifier thresholds + a bigger paraphrase model | **98.1%** | **98.1%** |

One query still fails, reported as-is rather than tuned away:
`しろいみみにつけるやつをなくした` ("lost the white thing you put on your ears") — the
sentence contains no noun at all, only a functional description → rank 44.

**Bigger is not better.** Swapping in `multilingual-e5-large` (10× the size) makes results
*worse*, because e5 is trained for asymmetric retrieval while this task is symmetric
short-text similarity. Full analysis, including the two confounds I had to fix before the
comparison meant anything: [docs/MODEL_COMPARISON.md](docs/MODEL_COMPARISON.md).

| Model | Dim | Size | Recall@1 |
|---|---|---|---|
| paraphrase-multilingual-MiniLM-L12-v2 | 384 | 0.22 GB | 96.2% |
| **paraphrase-multilingual-mpnet-base-v2** (default) | 768 | 1.00 GB | **98.1%** |
| intfloat/multilingual-e5-large | 1024 | 2.24 GB | 88.7% |

### Changing embedding models without destroying history

```bash
python -m scripts.reembed --activate   # write the new model alongside, then DEPRECATE the old
python -m scripts.reextract            # re-run the AI understanding layer after dictionary changes
```

Never `UPDATE` an embedding in place. Vectors from different models are not comparable, and
mixing them degrades retrieval silently.

---

## V3: images and cross-modal search

`item_images`, the `IMAGE` embedding type and the `image` scoring dimension were reserved
in V1; V3 connects them with a local ONNX CLIP model. CLIP's vision and text towers share
one embedding space, so the system supports both directions:

| Direction | Use case | Top-1 |
|---|---|---|
| image → image | The user has an old photo of their own item | **7/7 = 100%** |
| text → image | The user has no photo, only a sentence | **6/7 = 85.7%** |

The test that actually matters is the third one: **items of the same category but
different identity must not be confused**. A black backpack vs a red backpack, a brown
wallet vs a black wallet — if CLIP only recognises "this is a bag", the image channel is
worthless. Both pairs stay separated.

### CLIP only understands English — and how to work around it for free

CLIP ViT-B-32 was trained on English. Fed Japanese directly it scores 2/5; fed English,
5/5. For a system deployed in Japan that is a hard blocker.

The fix is not a bigger model. It is **reusing the synonym normalization layer that
already exists**: extraction already maps 「黒い」「リュック」 to canonical English
(`black` / `bag`), so a proper English CLIP prompt can be assembled from the structured
attributes:

```
「紺色の傘をなくした」
   ↓ Query Understanding (layer 1, the dictionary)
color=blue, category=umbrella
   ↓ build_clip_prompt()
"a photo of a blue umbrella"
   ↓ CLIP text tower
```

Text → image Top-1 went from 57.1% to 85.7%, cosine from 0.21–0.25 to 0.29–0.33, at zero
cost and with no new model. Full report:
[docs/BENCHMARK_IMAGE.md](docs/BENCHMARK_IMAGE.md).

```bash
docker compose exec api python -m scripts.gen_test_images
docker compose exec api python -m scripts.eval_images
```

---

## Invariants locked down by tests

| Invariant | Test |
|---|---|
| A 0.97 semantic score cannot beat a model conflict (iPhone 15 Pro vs Pro Max) | `test_model_conflict_beats_high_semantic` |
| Black vs dark grey is not a conflict; missing data is not a conflict | `test_black_vs_dark_gray_is_not_a_conflict`, `test_unknown_is_not_conflict` |
| Semantic similarity alone never reaches the auto-recommend tier | `test_semantic_never_alone_decides` |
| A single-character CJK alias must not match inside a compound (包 in 包装) | `test_single_kanji_alias_not_matched_inside_compound` |
| …but must still match a real one-character noun (鞄 in 黒い鞄) | `test_single_kanji_alias_matched_between_kana` |
| One matching colour is not full-confidence evidence | `test_single_attribute_match_is_not_full_confidence` |

`cd backend && python -m pytest tests -q` → **53 passed**, 25 of them regressions for bugs
found during the adversarial evaluation above.

---

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/lost` | File a loss → extract → embed → auto-search FOUND |
| POST | `/api/found` | Log an item → back-search historical LOST and alert staff |
| POST | `/api/search` | Natural-language search (query understanding + hybrid retrieval) |
| POST | `/api/ai/extract` | Extraction only, so the UI can ask "did we understand you correctly?" |
| GET | `/api/items/{id}/matches` | Persisted candidates with the full evidence chain (no recompute) |
| POST | `/api/items/{id}/rematch` | Re-run after changing weights |
| GET | `/api/matches/{id}/explanation` | Turn the evidence into plain language |
| POST | `/api/matches/{id}/decision` | Human CONFIRMED / REJECTED |
| GET | `/api/items/{id}/secret-questions` | Returns what to ask — never the answer |
| POST | `/api/items/{id}/verify-secret` | Check a secret attribute during handover |
| POST | `/api/items/{id}/return` | Record the return (AI never authorises) |
| GET | `/api/admin/metrics` | AI Assist Recall / Wrong Recommendation Rate |
| GET | `/api/admin/training-pairs` | Export positives / hard negatives for learning-to-rank |
| POST | `/api/admin/config/reload` | Hot-reload weights without a restart |

Accuracy is deliberately **not** the headline metric. The failure that matters is the
**false positive** — recommending the wrong person's property.

---

## Cost: zero. No API keys, no paid services.

| Component | License | Cost |
|---|---|---|
| PostgreSQL + pgvector | PostgreSQL License / MIT | Free |
| FastAPI / SQLAlchemy / psycopg / uvicorn | MIT / BSD | Free |
| fastembed + onnxruntime | Apache-2.0 / MIT | Free |
| `paraphrase-multilingual-mpnet-base-v2` | Apache-2.0 | Free |
| LLM | **Default provider is `rule` — no model is called at all** | — |

The 1 GB embedding model is **baked into the image at build time**, so the running
container never reaches the network and never needs a HuggingFace token. `LF_LLM_API_KEY`
and `LF_EMBEDDING_API_KEY` are empty by default and unused.

Two honest caveats:

1. The **first build** needs internet (base images + the model). After that it runs offline.
2. **Docker Desktop** requires a paid subscription for large organisations — that is Docker's
   own licensing, unrelated to this project. Docker Engine and Podman on Linux are free.

The `openai_compatible` providers exist so enterprises can point at their own gateway.
They are opt-in; if you do not configure them, nothing is ever called.

## Providers

Everything runs with **no external API** by default: a rule-based extractor and a local
ONNX multilingual embedding model baked into the image at build time.

```bash
# LLM — swap in your own gateway or self-hosted model
LF_LLM_PROVIDER=openai_compatible
LF_LLM_BASE_URL=https://your-gateway.internal
LF_LLM_MODEL=your-model
LF_LLM_API_KEY=...

# Embeddings — onnx (default) | local | openai_compatible | hashing (CI stub)
LF_EMBEDDING_PROVIDER=onnx
LF_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-mpnet-base-v2
LF_EMBEDDING_DIM=1536
```

The matching engine is not bound to any vendor. The LLM is an *evidence analyst*: it
classifies and explains, and it is structurally forbidden from overriding the algorithmic
score.

---

## PostgreSQL + pgvector, not Elasticsearch (for phase one)

Not because Elasticsearch is worse, but because this workload is
"structured filters + semantic recall + exact identity matching + transactional
consistency", not "search engine". One transaction writes the item, its attributes, and its
vectors. No outbox pattern, no Kafka, no indexer, no dead-letter queue.

Elasticsearch/OpenSearch earns its place later — at millions of records, or when Japanese
and Chinese tokenization, synonym dictionaries and fuzzy search become the bottleneck.
Because `embeddings` / `match_candidates` / `match_evidences` already decouple retrieval
from matching, swapping pgvector for OpenSearch vector search leaves the business layer
untouched. Reasoning in [docs/DESIGN.md](docs/DESIGN.md) §8.

---

## Layout

```
lostfound/
├── docs/DESIGN.md          Full technical design
├── db/schema.sql           Five-layer schema + FTS trigger + HNSW index
├── config/                 Weights, conflict rules, synonyms — never hard-coded
├── scripts/
│   ├── bootstrap.py        Schema + master data + demo data
│   ├── seed_corpus.py      Generate distractor corpus for evaluation
│   ├── eval_synonyms.py    Adversarial synonym evaluation
│   ├── reembed.py          Embedding model migration
│   └── reextract.py        Re-run the AI understanding layer
└── backend/app/
    ├── ai/                 Extraction, normalization, three prompts, providers
    ├── matching/           retrieval → conflicts → features → scoring → engine
    ├── api/                items / search / matches / admin
    └── static/index.html   Demo UI — shows the evidence, not just "94%"
```

## Roadmap

| Version | Scope | Status |
|---|---|---|
| V1 | Registration, structured attributes, keyword + vector search | Done |
| V1.5 | Zero-shot classification, multilingual vectors, adversarial evaluation | Done |
| V2 | Hybrid retrieval, match scoring, auto-recommendation, explanations | Done |
| V3 | Images / cross-modal search (image→image 100%, text→image 85.7%) | Done |
| V4 | Feedback loop → learning-to-rank | `training-pairs` exports today; needs ~10k confirmations |

## License

MIT
