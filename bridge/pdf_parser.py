"""Resource-limited PDF text extraction worker.

The parent bridge sends one PDF on stdin and receives a small JSON response on
stdout. This file intentionally has no bridge imports so the parser runs in an
isolated interpreter.
"""

from __future__ import annotations

import argparse
import io
import json
import sys


def apply_limits(memory_mb: int, cpu_seconds: int) -> None:
    try:
        import resource

        memory = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024))
    except (ImportError, OSError, ValueError):
        # Windows does not expose resource; the parent timeout and byte limits
        # still apply there.
        return


def extract(raw: bytes, max_pages: int, max_chars: int) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw), strict=False)
    if len(reader.pages) > max_pages:
        raise ValueError(f"PDF exceeds {max_pages} pages")
    pages = []
    total = 0
    for page in reader.pages:
        page_text = page.extract_text() or ""
        total += len(page_text)
        if total > max_chars:
            raise ValueError(f"extracted document text exceeds {max_chars} characters")
        pages.append(page_text)
    return "\n".join(pages)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--max-pages", type=int, required=True)
    parser.add_argument("--max-chars", type=int, required=True)
    parser.add_argument("--memory-mb", type=int, default=384)
    parser.add_argument("--cpu-seconds", type=int, default=30)
    args = parser.parse_args()
    apply_limits(args.memory_mb, args.cpu_seconds)
    raw = sys.stdin.buffer.read(args.max_bytes + 1)
    if len(raw) > args.max_bytes:
        result = {"ok": False, "error": "PDF exceeds the upload byte limit"}
    else:
        try:
            result = {"ok": True, "text": extract(raw, args.max_pages, args.max_chars)}
        except ValueError as exc:
            result = {"ok": False, "error": str(exc)}
        except Exception:
            result = {"ok": False, "error": "invalid or unreadable PDF file"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
