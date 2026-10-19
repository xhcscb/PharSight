import json
import re
import sqlite3
import sys
from pathlib import Path

import pdfplumber


def compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


document_hash = sys.argv[1]
db_path = Path("outputs/.graph_cache/graphrag_full_pdf_v2.sqlite3")
pdf_path = Path("assets/v2_graph") / f"{document_hash}.pdf"
connection = sqlite3.connect(db_path)
connection.row_factory = sqlite3.Row
document = dict(
    connection.execute(
        "SELECT * FROM documents WHERE document_hash=?", (document_hash,)
    ).fetchone()
)

passages_by_page: dict[int, list[dict]] = {}
for row in connection.execute(
    "SELECT * FROM passages WHERE document_id=?", (document["id"],)
):
    passages_by_page.setdefault(row["page_start"], []).append(dict(row))

record_pages = {
    row[0]
    for row in connection.execute(
        """SELECT DISTINCT p.page_start
           FROM records r
           JOIN json_each(r.evidence_ids_json) ids
           JOIN evidence_spans e ON e.id=ids.value
           JOIN passages p ON p.id=e.passage_id
           WHERE p.document_id=?""",
        (document["id"],),
    )
}

evidence_by_page: dict[int, list[dict]] = {}
for row in connection.execute(
    """SELECT e.id, e.quote, p.page_start
       FROM evidence_spans e
       JOIN passages p ON p.id=e.passage_id
       WHERE p.document_id=?""",
    (document["id"],),
):
    evidence_by_page.setdefault(row["page_start"], []).append(dict(row))

result = {
    "source_id": document["source_id"],
    "document_hash": document_hash,
    "passage_misses": [],
    "evidence_misses": [],
    "nonempty_without_passage": [],
    "nonempty_without_record": [],
}
nonempty_pages = 0
page_chars = 0
passage_total = 0
passage_matches = 0
evidence_total = 0
evidence_matches = 0

with pdfplumber.open(pdf_path) as pdf:
    result["pdf_pages"] = len(pdf.pages)
    for page_number, page in enumerate(pdf.pages, 1):
        text = page.extract_text() or ""
        compact_page = compact(text)
        page_chars += len(compact_page)
        if compact_page:
            nonempty_pages += 1
        passages = passages_by_page.get(page_number, [])
        if compact_page and not passages:
            result["nonempty_without_passage"].append(
                [page_number, len(compact_page)]
            )
        if compact_page and page_number not in record_pages:
            result["nonempty_without_record"].append(
                [page_number, len(compact_page)]
            )
        for passage in passages:
            passage_total += 1
            compact_passage = compact(passage["text"])
            matched = bool(compact_passage) and (
                compact_passage in compact_page or compact_page in compact_passage
            )
            passage_matches += int(matched)
            if not matched:
                result["passage_misses"].append(
                    [
                        page_number,
                        passage["chunk_id"],
                        len(compact_passage),
                        len(compact_page),
                    ]
                )
        for evidence in evidence_by_page.get(page_number, []):
            evidence_total += 1
            matched = compact(evidence["quote"]) in compact_page
            evidence_matches += int(matched)
            if not matched:
                result["evidence_misses"].append(
                    [page_number, evidence["id"]]
                )

result.update(
    {
        "nonempty_pages": nonempty_pages,
        "page_compact_chars": page_chars,
        "passages": passage_total,
        "passage_pdf_matches": passage_matches,
        "evidence": evidence_total,
        "evidence_pdf_matches": evidence_matches,
        "pages_with_records": len(record_pages),
    }
)
print(json.dumps(result, ensure_ascii=False, indent=2))
connection.close()
