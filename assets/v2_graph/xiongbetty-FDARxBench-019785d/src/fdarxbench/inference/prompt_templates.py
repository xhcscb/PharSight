# prompt_templates.py

from __future__ import annotations
from typing import List, Dict

BASE_INSTRUCTIONS = """
You are assisting FDA reviewers by answering questions about a single drug label.

General rules:
- Answer as concisely as possible (1–3 sentences).
- Do NOT invent facts that are not supported by the label.
""".strip()


def build_closed_book_prompt(
    drug_name: str,
    question: str,
) -> str:
    prompt = f"""You do NOT have access to the drug label text. Use only your existing knowledge.
    
QUESTION:
{question}

ANSWER:"""
    return prompt.strip()


def build_open_book_full_label_prompt(
    drug_name: str,
    question: str,
    label_text: str,
    max_chars: int = 16000,
) -> str:
    """
    Open-book: model sees the entire label (or a truncated version),
    with explicit PASSAGE markers like: ||PASSAGE_0005||.

    REQUIRED OUTPUT FORMAT:
      First line: a natural-language answer (or NOT_ANSWERABLE).
      Second line: CITED_PASSAGES: [PASSAGE_0005, PASSAGE_0012, ...]
    """
    if len(label_text) > max_chars:
        label_text = label_text[:max_chars] + "\n[TRUNCATED]"

    instructions = f"""The label text below is divided into passages. Each passage is preceded by a marker
of the form: ||PASSAGE_XXXX|| where XXXX is a zero-padded integer (the passage id).

When answering:
- Use ONLY information from the label text.
- After your answer, you MUST list the passage ids that best support your answer.
- If multiple passages are relevant, include all of them.
- If the answer truly cannot be determined from the label, reply exactly with: NOT_ANSWERABLE
- If the answer is NOT_ANSWERABLE, use CITED_PASSAGES: [].

Output format (exactly):
1. First line: the answer in natural language (or NOT_ANSWERABLE).
2. Second line: CITED_PASSAGES: [PASSAGE_XXXX, PASSAGE_YYYY, ...]
"""

    prompt = f"""{instructions}

LABEL TEXT:
{label_text}

QUESTION:
{question}

ANSWER (follow the required format):"""
    return prompt.strip()


def build_open_book_passages_prompt(
    drug_name: str,
    question: str,
    passages: List[Dict],
    max_passages: int = 10,
) -> str:
    passages = sorted(
        passages,
        key=lambda p: int(p.get("doc_chunk_index", 0))
    )[:max_passages]

    parts = []
    for i, p in enumerate(passages):
        sec_title = (p.get("section_title") or "").strip()
        text = (p.get("text") or "").strip()
        idx = p.get("doc_chunk_index")
        label = f"PASSAGE {i+1}"
        if idx is not None:
            label += f" (doc_chunk_index={idx})"
        if sec_title:
            parts.append(f"{label} — {sec_title}\n{text}")
        else:
            parts.append(f"{label}\n{text}")

    context_block = "\n\n".join(parts) if parts else "[NO PASSAGES FOUND]"

    prompt = f"""You are given a set of passages extracted from the FDA-approved label
for this drug. Use ONLY these passages to answer.

PASSAGES:
{context_block}

QUESTION:
{question}

ANSWER:"""
    return prompt.strip()
