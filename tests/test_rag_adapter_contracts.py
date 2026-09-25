"""Transport/extraction parity without external endpoints or user documents."""

from __future__ import annotations

import io
import json
import subprocess
import zipfile
from types import SimpleNamespace

import pytest

from bridge import document_extraction as extraction
from bridge import embedding_transport as embedding
from bridge.settings import load_app_settings


def settings(tmp_path, **values):
    return load_app_settings({"SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS": "2", **values}, home=tmp_path)


def test_embedding_http_uses_only_its_dedicated_key_and_strict_transport(tmp_path, monkeypatch):
    app = settings(tmp_path, SILLYTAVERN_RAG_EMBEDDING_API_KEY="fixture-rag-key", LLM_API_KEY="fixture-story-key")
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"data":[{"embedding":[1,2]}]}'

    def open_request(request, **kwargs):
        captured.append((request, kwargs))
        return Response()

    monkeypatch.setattr(embedding, "strict_urlopen", open_request)
    assert embedding.embed_rag_text("x" * 6001, app_settings=app) == [1.0, 2.0]
    request, kwargs = captured[0]
    assert request.get_method() == "POST"
    assert request.full_url == app.rag_embedding_url
    assert request.get_header("Authorization") == "Bearer fixture-rag-key"
    assert "fixture-story-key" not in repr(request.header_items())
    assert json.loads(request.data)["input"] == "x" * 6000
    assert kwargs == {"timeout": 60, "allowed_env": "SILLYTAVERN_RAG_ALLOWED_HOSTS", "environ": app.environ}


def test_external_embedding_requires_dedicated_key_after_endpoint_validation(tmp_path, monkeypatch):
    app = settings(
        tmp_path,
        SILLYTAVERN_RAG_EMBEDDING_URL="https://embedding.example/v1/embeddings",
        LLM_API_KEY="fixture-story-key",
    )
    checked = []
    monkeypatch.setattr(embedding, "validate_provider_endpoint", lambda *args, **kwargs: checked.append((args, kwargs)))
    with pytest.raises(RuntimeError, match="dedicated SILLYTAVERN_RAG_EMBEDDING_API_KEY"):
        embedding.rag_embedding_headers(app_settings=app)
    assert checked == [((app.rag_embedding_url, "SILLYTAVERN_RAG_ALLOWED_HOSTS"), {"environ": app.environ})]


def test_loopback_embedding_does_not_require_auth_header(tmp_path):
    assert embedding.rag_embedding_headers(app_settings=settings(tmp_path)) == {"Content-Type": "application/json"}


@pytest.mark.parametrize(
    "payload", [{}, {"data": []}, {"data": [{"embedding": [1]}]}, {"data": [{"embedding": ["not-a-number", 0]}]}]
)
def test_invalid_embedding_payload_preserves_lexical_fallback(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(embedding, "_post_embedding_request", lambda *args, **kwargs: payload)
    assert embedding.embed_rag_text("query", app_settings=settings(tmp_path)) is None


def test_batch_embeddings_preserve_indexes_and_ignore_invalid_items(tmp_path, monkeypatch):
    calls = []

    def post(payload, timeout, **kwargs):
        calls.append((payload, timeout))
        return {
            "data": [
                {"index": 1, "embedding": [2, 3]},
                {"index": 0, "embedding": [0, 1]},
                {"index": 2, "embedding": [1]},
                {"index": 9, "embedding": [4, 5]},
            ]
        }

    monkeypatch.setattr(embedding, "_post_embedding_request", post)
    assert embedding.embed_rag_batch(["a" * 6001, "b", "c"], app_settings=settings(tmp_path)) == [
        [0.0, 1.0],
        [2.0, 3.0],
        None,
    ]
    assert calls[0][0]["input"] == ["a" * 6000, "b", "c"]
    assert calls[0][1] == 120


def test_empty_batch_has_no_transport_call(tmp_path, monkeypatch):
    monkeypatch.setattr(embedding, "_post_embedding_request", lambda *args, **kwargs: pytest.fail("unexpected HTTP"))
    assert embedding.embed_rag_batch([], app_settings=settings(tmp_path)) == []


def test_failed_batch_falls_back_to_bounded_single_requests(tmp_path, monkeypatch):
    calls = []

    def post(payload, timeout, **kwargs):
        calls.append((payload["input"], timeout))
        if isinstance(payload["input"], list):
            raise OSError("fixture unavailable")
        return {"data": [{"embedding": [1, 0]}]}

    monkeypatch.setattr(embedding, "_post_embedding_request", post)
    assert embedding.embed_rag_batch(["a", "b"], app_settings=settings(tmp_path)) == [[1.0, 0.0], [1.0, 0.0]]
    assert calls == [(["a", "b"], 120), ("a", 60), ("b", 60)]


@pytest.mark.parametrize(
    "filename,raw,expected",
    [
        ("notes.txt", b"  hello\t world\r\n\rnext\n", "hello world\nnext"),
        ("page.html", b"<p>Hello &amp; world</p>", "Hello & world"),
    ],
)
def test_text_and_markup_extraction_remain_normalized(tmp_path, filename, raw, expected):
    assert extraction.extract_data_bank_text(filename, raw, app_settings=settings(tmp_path)) == expected


def test_document_format_and_character_limits_are_enforced(tmp_path):
    with pytest.raises(ValueError, match="unsupported Data Bank format"):
        extraction.extract_data_bank_text("unknown.exe", b"abc", app_settings=settings(tmp_path))
    with pytest.raises(ValueError, match="extracted document text exceeds 5"):
        extraction.extract_data_bank_text(
            "notes.txt", b"abcdef", app_settings=settings(tmp_path, SILLYTAVERN_RAG_MAX_EXTRACTED_CHARS="5")
        )


def test_docx_extracts_text_and_rejects_invalid_archive(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(
            "word/document.xml", '<w:document xmlns:w="urn:fixture"><w:t>Hello</w:t><w:t>world</w:t></w:document>'
        )
    assert (
        extraction.extract_data_bank_text("fixture.docx", data.getvalue(), app_settings=settings(tmp_path))
        == "Hello\nworld"
    )
    with pytest.raises(ValueError, match="invalid DOCX file"):
        extraction.extract_data_bank_text("bad.docx", b"not-a-zip", app_settings=settings(tmp_path))


@pytest.mark.parametrize("size,compressed", [(50 * 1024 * 1024 + 1, 100000), (1001, 1)])
def test_docx_rejects_oversized_or_overcompressed_metadata_before_reading(tmp_path, monkeypatch, size, compressed):
    class Archive:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getinfo(self, name):
            assert name == "word/document.xml"
            return SimpleNamespace(file_size=size, compress_size=compressed)

        def read(self, info):
            pytest.fail("refused archive member must not be decompressed")

    monkeypatch.setattr(extraction.zipfile, "ZipFile", Archive)
    with pytest.raises(ValueError, match="too large or highly compressed"):
        extraction.extract_data_bank_text("fixture.docx", b"fixture", app_settings=settings(tmp_path))


@pytest.mark.parametrize(
    "failure,message",
    [
        (subprocess.TimeoutExpired("fixture", 1), "PDF parsing timed out"),
        (OSError("fixture"), "PDF parser worker is unavailable"),
    ],
)
def test_pdf_worker_process_failures_are_translated(tmp_path, monkeypatch, failure, message):
    def run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(extraction.subprocess, "run", run)
    with pytest.raises(ValueError, match=message):
        extraction.extract_pdf_data_bank_text(b"fixture", app_settings=settings(tmp_path))


@pytest.mark.parametrize(
    "stdout,message",
    [
        (b"not-json", "PDF parser returned invalid output"),
        (b'{"ok":false,"error":"fixture unreadable"}', "fixture unreadable"),
    ],
)
def test_pdf_worker_malformed_or_refused_output_is_translated(tmp_path, monkeypatch, stdout, message):
    monkeypatch.setattr(extraction.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=stdout))
    with pytest.raises(ValueError, match=message):
        extraction.extract_pdf_data_bank_text(b"fixture", app_settings=settings(tmp_path))


def test_chunking_preserves_bounded_newline_split_and_overlap():
    chunks = extraction.split_data_bank_chunks("A" * 1000 + "\n" + "B" * 1000)
    assert chunks[0] == "A" * 1000
    assert chunks[1].startswith("A" * 220 + "\n")
    assert chunks[-1].endswith("B" * 1000)
    assert all(len(chunk) <= extraction.RAG_CHUNK_CHARS for chunk in chunks)
    assert extraction.split_data_bank_chunks(" " * 2000) == []
    assert extraction.split_data_bank_chunks("") == []
