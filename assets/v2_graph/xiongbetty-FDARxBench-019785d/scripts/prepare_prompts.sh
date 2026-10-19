#!/usr/bin/env bash
set -euo pipefail

# Usage: bash scripts/prepare_prompts.sh --mode closed [--dataset toy|full]
#
# --mode     (required): closed | open_full | open_passages
# --dataset  (optional): toy (default) or full

MODE=""
DATASET="toy"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)    MODE="$2"; shift 2 ;;
    --dataset) DATASET="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$MODE" ]]; then
  echo "Error: --mode is required (closed | open_full | open_passages)" >&2
  exit 1
fi

case "$DATASET" in
  toy)  QA_PATH="data/qa/qa_toy.jsonl" ;;
  full) QA_PATH="data/qa/qa.jsonl" ;;
  *)    echo "Error: --dataset must be 'toy' or 'full'" >&2; exit 1 ;;
esac

OUT="prompts_${DATASET}_${MODE}.jsonl"

LABELS_ARG=""
if [[ "$MODE" == "open_full" ]]; then
  LABELS_ARG="--labels data/labels/labels.jsonl"
fi

python -m fdarxbench.inference.prepare_prompts \
  --mode "$MODE" \
  --qa "$QA_PATH" \
  $LABELS_ARG \
  --out "$OUT"
