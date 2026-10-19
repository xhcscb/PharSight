#!/usr/bin/env bash
set -euo pipefail

# Usage: bash scripts/prepare_retrieval.sh [--dataset toy|full]
#
# --dataset (optional): toy (default) or full

DATASET="toy"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dataset) DATASET="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

case "$DATASET" in
  toy)  QA_PATH="data/qa/qa_toy.jsonl" ;;
  full) QA_PATH="data/qa/qa.jsonl" ;;
  *)    echo "Error: --dataset must be 'toy' or 'full'" >&2; exit 1 ;;
esac

OUT="retrieval_corpus_${DATASET}.jsonl"

python -m fdarxbench.retrieval.prepare_retrieval_corpus \
  --labels data/labels/labels.jsonl \
  --qa "$QA_PATH" \
  --out "$OUT"
