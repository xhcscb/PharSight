#!/usr/bin/env python3
"""
Prepare inference prompts for QA examples in three modes:

- closed       : Question only (no label text)  [factual/multihop ONLY]
- open_full    : Full label text with passage markers  [all tasks incl. refusal]
- open_passages: Gold passages from context  [factual/multihop ONLY]

Outputs one JSONL record per question containing system_prompt and user_prompt
ready to be sent to any LLM.

Usage:
  python -m fdarxbench.inference.prepare_prompts \
    --mode closed \
    --qa data/qa/qa_toy.jsonl \
    --out prompts_closed.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Any

from tqdm import tqdm

from fdarxbench.inference.prompt_templates import (
    BASE_INSTRUCTIONS,
    build_closed_book_prompt,
    build_open_book_full_label_prompt,
    build_open_book_passages_prompt,
)
from fdarxbench.utils.io import load_labels_by_set_id


def extract_gold_doc_idxs(rec: Dict[str, Any]):
    """
    From a QA record, extract a sorted list of unique doc_chunk_index values.
    """
    ctx = rec.get("context")

    if not isinstance(ctx, list):
        return []

    doc_idxs = set()
    for c in ctx:
        if not isinstance(c, dict):
            continue
        idx = c.get("doc_chunk_index")
        if idx is None:
            continue
        try:
            idx_int = int(idx)
        except Exception:
            continue
        doc_idxs.add(idx_int)

    return sorted(doc_idxs) if doc_idxs else []


def build_prompt_record(
    rec: Dict[str, Any],
    mode: str,
    label_by_set_id: Dict[str, str],
    max_passages: int = 10,
) -> Dict[str, Any]:
    """
    Build a prompt record for one QA example.

    Returns a dict with system_prompt, user_prompt, and metadata fields.
    """
    set_id = (rec.get("set_id") or "").strip()
    drug_name = (rec.get("drug_name") or "").strip()
    question = rec.get("question") or ""
    source = rec.get("source") or ""
    task = (rec.get("task") or rec.get("question_type") or "").lower()
    qid = rec.get("qid") or None

    # Build user prompt for the given mode
    if mode == "closed":
        user_prompt = build_closed_book_prompt(drug_name, question)

    elif mode == "open_full":
        label_text = label_by_set_id.get(set_id, "")
        user_prompt = build_open_book_full_label_prompt(drug_name, question, label_text)

    elif mode == "open_passages":
        ctx = rec.get("context") or []
        user_prompt = build_open_book_passages_prompt(
            drug_name, question, ctx, max_passages=max_passages
        )
    else:
        raise ValueError(f"Unknown mode: {mode}")

    gold_doc_idxs = extract_gold_doc_idxs(rec)

    return {
        "qid": qid,
        "set_id": set_id,
        "drug_name": drug_name,
        "task": task,
        "source": source,
        "mode": mode,
        "system_prompt": BASE_INSTRUCTIONS,
        "user_prompt": user_prompt,
        "gold_answer": rec.get("answer"),
        "gold_doc_chunk_index": gold_doc_idxs,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Prepare inference prompts for FDA drug label QA."
    )
    ap.add_argument("--mode", required=True, choices=["closed", "open_full", "open_passages"],
                    help="Inference mode")
    ap.add_argument("--qa", required=True, help="Path to QA JSONL")
    ap.add_argument("--labels", required=False, default=None,
                    help="Labels JSONL with set_id + label text (required for open_full)")
    ap.add_argument("--out", required=True, help="Path to write prompt JSONL")
    ap.add_argument("--max_passages", type=int, default=10,
                    help="Max passages to include in open_passages mode")
    args = ap.parse_args()

    mode = args.mode
    qa_path = Path(args.qa)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "open_full" and args.labels is None:
        ap.error("--labels is required for open_full mode")

    # Only needed for open_full; load once up front
    label_by_set_id: Dict[str, str] = {}
    if mode == "open_full":
        labels_path = Path(args.labels)
        print("[prepare_prompts] Loading label texts by set_id ...")
        label_by_set_id = load_labels_by_set_id(labels_path)

    n_total = 0
    n_written = 0
    n_skipped_refusal = 0

    with qa_path.open("r", encoding="utf-8") as fin, \
         out_path.open("w", encoding="utf-8") as fout:

        for line in tqdm(fin, desc=f"Preparing prompts ({mode})"):
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except Exception:
                continue

            n_total += 1
            task = (rec.get("task") or rec.get("question_type") or "").lower()

            # Skip refusal questions for modes where they don't make sense
            if task == "refusal" and mode in ("closed", "open_passages"):
                n_skipped_refusal += 1
                continue

            prompt_rec = build_prompt_record(
                rec,
                mode=mode,
                label_by_set_id=label_by_set_id,
                max_passages=args.max_passages,
            )
            fout.write(json.dumps(prompt_rec, ensure_ascii=False) + "\n")
            n_written += 1

    print(f"[prepare_prompts] processed {n_total} records")
    print(f"[prepare_prompts] wrote {n_written} prompt records -> {out_path}")
    print(f"[prepare_prompts] skipped refusal records (this mode): {n_skipped_refusal}")


if __name__ == "__main__":
    main()
