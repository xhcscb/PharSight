"""Safely download and parse public PDF evidence."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx
import pdfplumber

from .paddleocr_reader import PaddleOCRDocumentReader, credentials_configured

MAX_PDF_BYTES = 50 * 1024 * 1024


def classify_pdf_document(content: str, title: str = "") -> dict[str, str | None]:
    """Classify common Chinese disclosure PDFs using conservative text rules."""
    sample = f"{title}\n{content[:12000]}"
    compact = re.sub(r"\s+", "", sample)
    year_match = re.search(r"(20\d{2})年(?:年度)?报告", compact)
    is_summary = "年度报告摘要" in compact
    presentation_markers = ("业绩推介", "业绩说明会", "业绩演示", "全年业绩", "业绩发布")

    if year_match and not is_summary and not any(marker in compact for marker in presentation_markers):
        document_type = "annual_report"
    elif any(marker in compact for marker in presentation_markers):
        document_type = "earnings_presentation"
    elif is_summary:
        document_type = "annual_report_summary"
    else:
        document_type = "unknown"

    detected_title = next(
        (
            line.strip("# *\t")
            for line in sample.splitlines()
            if 4 <= len(line.strip("# *\t")) <= 100
        ),
        title.strip() or None,
    )
    return {
        "document_type": document_type,
        "reporting_period": year_match.group(1) if year_match else None,
        "detected_title": detected_title,
    }


def is_probable_pdf_url(url: str, metadata: dict | None = None) -> bool:
    """Return whether a search result looks like a PDF before it is opened."""
    parsed = urlparse(url)
    path_and_query = unquote(f"{parsed.path}?{parsed.query}").lower()
    if ".pdf" in path_and_query:
        return True
    metadata = metadata or {}
    hint = " ".join(str(metadata.get(key, "")) for key in ("title", "description", "mime_type"))
    return "pdf" in hint.lower() or "application/pdf" in hint.lower()


def _validate_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("PDF URL must use public HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("PDF URL must not contain embedded credentials")
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".local"):
        raise ValueError("Private PDF hosts are not allowed")
    try:
        default_port = 443 if parsed.scheme == "https" else 80
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or default_port)}
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve PDF host: {host}") from exc
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise ValueError("Private or non-global PDF addresses are not allowed")


def _extract_text_pdf(content: bytes, max_pages: int | None = None) -> tuple[str, int]:
    pages: list[str] = []
    with pdfplumber.open(BytesIO(content)) as pdf:
        selected = pdf.pages[:max_pages] if max_pages else pdf.pages
        for page_number, page in enumerate(selected, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(f"[Page {page_number}]\n{text}")
        return "\n\n".join(pages), len(selected)


async def remote_url_is_pdf(url: str) -> bool:
    """Probe a public URL for a PDF content type without downloading its body."""
    current_url = url
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        for _ in range(6):
            _validate_public_http_url(current_url)
            response = await client.head(current_url, headers={"User-Agent": "Pharsight/0.3 PDF probe"})
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return False
                current_url = urljoin(current_url, location)
                continue
            return "application/pdf" in response.headers.get("content-type", "").lower()
    return False


async def download_and_parse_pdf(
    url: str,
    *,
    max_bytes: int = MAX_PDF_BYTES,
    max_pages: int | None = None,
) -> dict:
    """Download one public PDF and return text plus provenance metadata."""
    headers = {"User-Agent": "Pharsight/0.3 PDF evidence collector"}
    current_url = url
    content = b""
    content_type = ""
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        for _ in range(6):
            _validate_public_http_url(current_url)
            async with client.stream("GET", current_url, headers=headers) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("PDF redirect did not include a location")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                declared_size = int(response.headers.get("content-length", "0") or 0)
                if declared_size > max_bytes:
                    raise ValueError(f"PDF exceeds the {max_bytes // (1024 * 1024)} MB download limit")
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > max_bytes:
                        raise ValueError(f"PDF exceeds the {max_bytes // (1024 * 1024)} MB download limit")
                    chunks.append(chunk)
                content = b"".join(chunks)
                break
        else:
            raise ValueError("PDF download exceeded the redirect limit")

    final_url = current_url
    if not content.startswith(b"%PDF-"):
        raise ValueError(f"Downloaded resource is not a PDF (content-type: {content_type or 'unknown'})")

    document_hash = hashlib.sha256(content).hexdigest()
    parser = ""
    markdown = ""
    page_count = 0

    ocr_configured = credentials_configured()
    if ocr_configured:
        cache_dir = Path(__file__).resolve().parents[3] / "outputs" / ".document_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        temp_path = cache_dir / f"{document_hash}.pdf"
        temp_path.write_bytes(content)
        try:
            result = await PaddleOCRDocumentReader().api_function(file_path=str(temp_path), max_pages=max_pages)
            if result and isinstance(result[0].data, dict) and result[0].data.get("markdown"):
                payload = result[0].data
                markdown = payload["markdown"]
                page_count = int(payload.get("page_count") or page_count)
                parser = f"paddleocr:{payload.get('backend', 'hosted')}:{payload.get('model', 'unknown')}"
        finally:
            temp_path.unlink(missing_ok=True)
    if not markdown.strip():
        markdown, page_count = _extract_text_pdf(content, max_pages=max_pages)
        parser = "pdfplumber"
    if not markdown.strip():
        if ocr_configured:
            raise RuntimeError("Neither PaddleOCR nor pdfplumber returned extractable PDF text.")
        raise RuntimeError("This PDF has no extractable text. Configure PaddleOCR credentials to parse scanned PDFs.")

    identity = classify_pdf_document(markdown)

    return {
        "content": markdown,
        "content_type": "application/pdf",
        "parser": parser,
        "page_count": page_count,
        "document_hash": document_hash,
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "final_url": final_url,
        "byte_size": len(content),
        **identity,
    }
