#!/usr/bin/env bash
set -euo pipefail

# Usage: bash scripts/prepare_grading.sh --predictions <predictions.jsonl>

PREDICTIONS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --predictions) PREDICTIONS="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$PREDICTIONS" ]]; then
  echo "Error: --predictions is required" >&2
  exit 1
fi

OUT="$(basename "$PREDICTIONS" .jsonl)_grading_prompts.jsonl"

python -m fdarxbench.evaluation.prepare_grading_prompts \
  --predictions "$PREDICTIONS" \
  --out "$OUT"
