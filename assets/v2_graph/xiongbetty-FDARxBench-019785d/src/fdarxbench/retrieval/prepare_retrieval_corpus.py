#!/usr/bin/env python3
"""
Prepare retrieval query/corpus pairs with gold labels from QA + chunked labels.

For each QA example, outputs a record containing the question (query), the full
chunk corpus for that drug label, and the gold doc_chunk_indices/texts for
evaluation.

Usage:
  python -m fdarxbench.retrieval.prepare_retrieval_corpus \
    --labels data/labels/labels.jsonl \
    --qa data/qa/qa_toy.jsonl \
    --out retrieval_corpus.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

from tqdm import tqdm

from fdarxbench.utils.io import append_jsonl_line, load_labels_with_chunks


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Prepare retrieval query/corpus pairs with gold labels."
    )
    ap.add_argument("--labels", type=str, required=True,
                    help="Path to labels JSONL (with chunks).")
    ap.add_argument("--qa", type=str, required=True,
                    help="Path to QA JSONL file.")
    ap.add_argument("--out", type=str, required=True,
                    help="Output JSONL path for retrieval corpus records.")
    ap.add_argument("--tasks", type=str, default=None,
                    help="Comma-separated list of task types to include, e.g. factual,multihop.")
    args = ap.parse_args()

    labels_path = Path(args.labels)
    qa_path = Path(args.qa)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    allowed_tasks: Optional[Set[str]] = None
    if args.tasks:
        allowed_tasks = {
            t.strip() for t in args.tasks.split(",") if t.strip()
        }

    print("[prepare_retrieval_corpus] Loading chunked labels ...")
    labels_by_set_id = load_labels_with_chunks(labels_path)

    n_written = 0
    n_skipped = 0

    with qa_path.open("r", encoding="utf-8") as fin, \
         out_path.open("w", encoding="utf-8") as fout:

        for line in tqdm(fin, desc="Preparing retrieval corpus"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)

            task = rec.get("task")
            if allowed_tasks is not None and task not in allowed_tasks:
                n_skipped += 1
                continue

            qid = rec["qid"]
            set_id = rec["set_id"]
            question = rec["question"]
            drug_name = rec.get("drug_name")
            source = rec.get("source")

            label_rec = labels_by_set_id.get(set_id)
            if label_rec is None:
                n_skipped += 1
                continue

            corpus: List[str] = label_rec.get("chunks", [])

            # Collect gold indices/texts from QA context
            gold_indices: List[int] = []
            gold_texts: List[str] = []
            for ctx in rec.get("context", []):
                idx = ctx.get("doc_chunk_index")
                if idx is None:
                    continue
                if not isinstance(idx, int):
                    try:
                        idx = int(idx)
                    except Exception:
                        continue
                gold_indices.append(idx)
                if 0 <= idx < len(corpus):
                    gold_texts.append(corpus[idx])
                else:
                    gold_texts.append(ctx.get("text", ""))

            out_rec = {
                "qid": qid,
                "set_id": set_id,
                "drug_name": drug_name,
                "task": task,
                "source": source,
                "question": question,
                "corpus": corpus,
                "gold_doc_chunk_indices": gold_indices,
                "gold_chunk_texts": gold_texts,
            }
            append_jsonl_line(fout, out_rec)
            n_written += 1

    print(f"[prepare_retrieval_corpus] wrote {n_written} records -> {out_path}")
    if n_skipped:
        print(f"[prepare_retrieval_corpus] skipped {n_skipped} records (task filter or missing label)")


if __name__ == "__main__":
    main()
