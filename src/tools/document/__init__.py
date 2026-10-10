"""Document-parsing tools (PDF / research report readers)."""

from .document_index import (
    PDFEvidenceSearchTool,
    build_document_index,
    load_document_index,
    search_document_indexes,
)
from .paddleocr_reader import PaddleOCRDocumentReader
from .pdf_ingestion import download_and_parse_pdf, is_probable_pdf_url, remote_url_is_pdf

__all__ = [
    "PDFEvidenceSearchTool",
    "PaddleOCRDocumentReader",
    "build_document_index",
    "load_document_index",
    "download_and_parse_pdf",
    "is_probable_pdf_url",
    "remote_url_is_pdf",
    "search_document_indexes",
]
