#!/usr/bin/env bash
# 同一套 53 条对抗查询，逐个换向量模型跑完整评测。
#
# /models 是持久卷，换模型不需要重建镜像；每轮：
#   重启容器（换 LF_EMBEDDING_MODEL）→ 等就绪 → benchmark（内部会清库重灌）
#
#   bash scripts/compare_models.sh
set -u
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1

MODELS=(
  "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2:minilm"
  "sentence-transformers/paraphrase-multilingual-mpnet-base-v2:mpnet"
  "intfloat/multilingual-e5-large:e5large"
)

for entry in "${MODELS[@]}"; do
  model="${entry%:*}"
  tag="${entry##*:}"
  echo "=============================================================="
  echo "  $tag  <-  $model"
  echo "=============================================================="

  LF_EMBEDDING_MODEL="$model" docker compose up -d >/dev/null 2>&1

  # 等模型加载完成（大模型首次加载要几十秒）
  for _ in $(seq 1 60); do
    actual=$(curl -s http://127.0.0.1:8080/api/admin/config 2>/dev/null \
             | python -c "import sys,json;print(json.load(sys.stdin)['embedding_model'])" 2>/dev/null)
    [ "$actual" = "$model" ] && break
    sleep 3
  done
  echo "  server model = ${actual:-<未就绪>}"

  docker compose exec -T -e PYTHONIOENCODING=utf-8 api \
    python -m scripts.benchmark \
      --out "/app/docs/_cmp_${tag}.md" \
      --json "/app/docs/_cmp_${tag}.json" 2>&1 \
    | grep -vE "UserWarning|self\._m|it/s\]" | tail -8
  echo
done

echo "全部完成。汇总："
PYTHONIOENCODING=utf-8 python - <<'PY'
import json, pathlib
rows = []
for tag in ("minilm", "mpnet", "e5large"):
    p = pathlib.Path("docs") / f"_cmp_{tag}.json"
    if not p.exists():
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    s = d["summary"]
    rows.append((tag, d["environment"].get("embedding_model", "?"),
                 s["recall@1"], s["recall@3"], s["mrr"],
                 sum(1 for r in d["results"] if r["rank"] != 1)))
print(f"{'tag':<9}{'Recall@1':>10}{'Recall@3':>10}{'MRR':>8}{'失败':>6}")
for tag, model, r1, r3, mrr, fails in rows:
    print(f"{tag:<9}{r1:>9.1%}{r3:>10.1%}{mrr:>8.3f}{fails:>6}")
PY
