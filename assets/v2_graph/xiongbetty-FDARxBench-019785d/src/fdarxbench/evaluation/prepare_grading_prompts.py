#!/usr/bin/env python3
"""
Prepare grading prompts for inference predictions using the FDA SimpleQA template.

Reads a predictions JSONL and formats each record through the grader template,
outputting JSONL records with system_prompt and user_prompt ready to be sent to
any judge LLM.

Usage:
  python -m fdarxbench.evaluation.prepare_grading_prompts \
    --predictions preds.jsonl \
    --out grading_prompts.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tqdm import tqdm

from fdarxbench.evaluation.grader_template import FDA_GRADER_TEMPLATE
from fdarxbench.utils.io import load_jsonl


GRADER_SYSTEM_PROMPT = "You are a grading assistant."


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Prepare grading prompts from inference predictions."
    )
    ap.add_argument("--predictions", required=True,
                    help="Path to predictions JSONL (needs qid, question, gold_answer, prediction)")
    ap.add_argument("--out", required=True,
                    help="Path to write grading prompt JSONL")
    args = ap.parse_args()

    preds_path = Path(args.predictions)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_written = 0

    with out_path.open("w", encoding="utf-8") as fout:
        for rec in tqdm(load_jsonl(preds_path), desc="Preparing grading prompts"):
            question = rec.get("question", "")
            gold_answer = rec.get("gold_answer", "")
            prediction = rec.get("prediction", "")
            qid = rec.get("qid")

            grader_user_prompt = FDA_GRADER_TEMPLATE.format(
                question=question,
                target=gold_answer,
                predicted_answer=prediction,
            )

            out_rec = {
                "qid": qid,
                "question": question,
                "gold_answer": gold_answer,
                "prediction": prediction,
                "grader_system_prompt": GRADER_SYSTEM_PROMPT,
                "grader_user_prompt": grader_user_prompt,
            }
            fout.write(json.dumps(out_rec, ensure_ascii=False) + "\n")
            n_written += 1

    print(f"[prepare_grading_prompts] wrote {n_written} grading prompt records -> {out_path}")


if __name__ == "__main__":
    main()
