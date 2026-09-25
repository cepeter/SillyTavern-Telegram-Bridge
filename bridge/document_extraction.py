"""Canonical document extraction owner."""

from __future__ import annotations

import html
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from defusedxml import ElementTree as ET

from bridge.limits import RAG_CHUNK_CHARS, RAG_CHUNK_OVERLAP, RAG_MAX_FILE_BYTES, RAG_SUPPORTED_SUFFIXES
from bridge.settings import AppSettings


def extract_pdf_data_bank_text(raw: bytes, *, app_settings: AppSettings) -> str:
    parser = Path(__file__).with_name("pdf_parser.py")
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed interpreter and parser; document passed on stdin
            [
                sys.executable,
                "-I",
                str(parser),
                "--max-bytes",
                str(RAG_MAX_FILE_BYTES),
                "--max-pages",
                str(app_settings.rag_max_pdf_pages),
                "--max-chars",
                str(app_settings.rag_max_extracted_chars),
            ],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=app_settings.rag_pdf_parse_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("PDF parsing timed out") from exc
    except OSError as exc:
        raise ValueError("PDF parser worker is unavailable") from exc
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("PDF parser returned invalid output") from exc
    if not result.get("ok"):
        raise ValueError(str(result.get("error") or "invalid or unreadable PDF file"))
    return str(result.get("text") or "")


def extract_data_bank_text(filename: str, raw: bytes, *, app_settings: AppSettings) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix not in RAG_SUPPORTED_SUFFIXES:
        raise ValueError("unsupported Data Bank format")
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > 50 * 1024 * 1024 or (
                    info.compress_size and info.file_size / info.compress_size > 1000
                ):
                    raise ValueError("DOCX XML member is too large or highly compressed")
                xml = archive.read(info)
            root = ET.fromstring(xml)
            text = "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("invalid DOCX file") from exc
    elif suffix == ".pdf":
        text = extract_pdf_data_bank_text(raw, app_settings=app_settings)
    else:
        text = raw.decode("utf-8", errors="replace")
        if suffix in {".html", ".htm", ".xml"}:
            text = re.sub(r"<[^>]+>", " ", text)
            text = html.unescape(text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()
    if len(normalized) > app_settings.rag_max_extracted_chars:
        raise ValueError(f"extracted document text exceeds {app_settings.rag_max_extracted_chars} characters")
    return normalized


def split_data_bank_chunks(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + RAG_CHUNK_CHARS)
        if end < len(text):
            boundary = text.rfind("\n", start + RAG_CHUNK_CHARS // 2, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - RAG_CHUNK_OVERLAP)
    return chunks
