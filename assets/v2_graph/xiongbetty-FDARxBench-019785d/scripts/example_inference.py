#!/usr/bin/env python3
"""
Example inference script for FDARxBench.

Reads a prompt JSONL file produced by prepare_prompts.sh, sends each prompt to
an LLM, and writes predictions to an output JSONL file.

This script is a starting point — replace `call_llm` with your own model call.

Usage:
  python scripts/example_inference.py \
    --prompts prompts_full_closed.jsonl \
    --out my_predictions.jsonl
"""

from __future__ import annotations

import argparse
import json


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Replace this function with your own LLM call.

    Example using the OpenAI client::

        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content

    Example using the Anthropic client::

        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return resp.content[0].text
    """
    raise NotImplementedError(
        "Replace call_llm() with your own model call. "
        "See the docstring above for examples."
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run LLM inference on FDARxBench prompts."
    )
    ap.add_argument(
        "--prompts", required=True,
        help="Path to prompt JSONL from prepare_prompts.sh",
    )
    ap.add_argument(
        "--out", required=True,
        help="Path to write predictions JSONL",
    )
    args = ap.parse_args()

    n = 0
    with open(args.prompts, encoding="utf-8") as fin, \
         open(args.out, "w", encoding="utf-8") as fout:
        for line in fin:
            rec = json.loads(line)
            prediction = call_llm(rec["system_prompt"], rec["user_prompt"])
            fout.write(json.dumps({
                "qid": rec["qid"],
                "question": rec["user_prompt"],
                "gold_answer": rec["gold_answer"],
                "prediction": prediction,
            }, ensure_ascii=False) + "\n")
            n += 1

    print(f"Wrote {n} predictions -> {args.out}")


if __name__ == "__main__":
    main()
