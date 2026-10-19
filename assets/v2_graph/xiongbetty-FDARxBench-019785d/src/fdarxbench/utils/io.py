"""Shared I/O helpers for loading and writing JSONL data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any

from tqdm import tqdm


def load_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                yield json.loads(s)
            except Exception:
                continue


def append_jsonl_line(fh, obj: Dict[str, Any]) -> None:
    fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def load_labels_by_set_id(labels_jsonl: Path) -> Dict[str, str]:
    """
    For open_full mode: map set_id -> full label text from an aggregated labels JSONL.

    Expects each line to look like:
      {
        "drug_name": "...",
        "set_id": "...",
        "drug_id": "...",
        "label": "||PASSAGE_0000||\\n..."
      }
    """
    by_set_id: Dict[str, str] = {}

    if not labels_jsonl.exists():
        raise FileNotFoundError(f"labels_jsonl not found: {labels_jsonl}")

    for rec in load_jsonl(labels_jsonl):
        set_id = (rec.get("set_id") or "").strip()
        label = rec.get("label") or rec.get("label_raw") or ""
        if not set_id or not label:
            continue
        # If there are duplicates, last one wins (you likely don't have any)
        by_set_id[set_id] = label

    return by_set_id


def load_labels_with_chunks(path: Path) -> Dict[str, Dict[str, Any]]:
    """
    Load chunked labels into dict keyed by set_id.
    """
    labels_by_set_id: Dict[str, Dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in tqdm(f, desc="Loading labels_with_chunks"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            set_id = rec["set_id"]
            labels_by_set_id[set_id] = rec
    return labels_by_set_id
