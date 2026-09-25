"""RAG ownership, injected embeddings, and transactional/versioned behavior."""

from __future__ import annotations

import ast
import importlib
from dataclasses import MISSING
from pathlib import Path

import pytest
from application_test_setup import make_native_test_embedding_port

import bridge.embedding_transport as _owner_embedding_transport
import bridge.rag_indexing as _owner_rag_indexing
import bridge.rag_query as _owner_rag_query
from bridge.settings import load_app_settings
from bridge.sqlite_store import db_connect, write_transaction

ROOT = Path(__file__).parents[1]


def owner(name):
    assert (ROOT / "bridge" / f"{name}.py").is_file(), f"missing canonical owner: {name}"
    return importlib.import_module("bridge." + name)


@pytest.mark.parametrize(
    "name",
    [
        "rag_service",
        "rag_contracts",
        "embedding_port",
        "embedding_transport",
        "embedding_values",
        "document_extraction",
        "rag_repository",
        "rag_indexing",
        "rag_query",
        "rag_composition",
        "databank_commands",
    ],
)
def test_rag_has_canonical_owners(name):
    owner(name)


def test_embedding_port_forwards_exact_single_and_batch_inputs():
    port_type = owner("embedding_port").EmbeddingPort
    calls = []
    port = port_type(
        embed_backend=lambda text: calls.append(("single", text)) or [1.0, 2.0],
        batch_backend=lambda texts: calls.append(("batch", texts)) or [[1.0], None],
    )
    assert port.embed("query") == [1.0, 2.0]
    assert port.embed_batch(["a", "b"]) == [[1.0], None]
    assert calls == [("single", "query"), ("batch", ["a", "b"])]


def make_service(tmp_path, *, revision="1", embed=None, batch=None):
    settings = load_app_settings(
        {"SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS": "2", "SILLYTAVERN_RAG_EMBEDDING_REVISION": revision}, home=tmp_path
    )
    port = owner("embedding_port").EmbeddingPort(
        embed_backend=embed or (lambda text: None),
        batch_backend=batch or (lambda texts: [None] * len(texts)),
    )
    service = owner("rag_composition").build_rag_service(app_settings=settings, embedding_port=port)
    return settings, service


def test_rag_service_is_required_and_cannot_import_adapters():
    service_module = owner("rag_service")
    port_module = owner("embedding_port")
    from bridge.composition import BridgeServices

    assert BridgeServices.__dataclass_fields__["rag"].default is MISSING
    for module in (service_module, port_module):
        imports = {
            n.module
            for n in ast.walk(ast.parse(Path(module.__file__).read_text()))
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("bridge.")
        }
        assert imports <= {"bridge.rag_contracts"}, imports


def test_versioned_lexical_retrieval_remains_available_without_embeddings(tmp_path):
    settings, service = make_service(tmp_path)
    db = db_connect(app_settings=settings)
    try:
        assert service.add_document(db, "chat", "notes.txt", b"apples first version")[0] == "added"
        assert service.add_document(db, "chat", "notes.txt", b"oranges second version")[0] == "versioned"
        assert service.add_document(db, "chat", "notes.txt", b"oranges second version")[0] == "duplicate"
        assert service.retrieve(db, "chat", "apples") == []
        assert service.retrieve(db, "chat", "oranges")[0][1] == "oranges second version"
        assert service.retrieve(db, "other", "oranges") == []
        versions = service.versions(db, "chat", "notes.txt")
        assert [(v[1], v[2]) for v in versions] == [(2, 1), (1, 0)]
        assert service.activate(db, "chat", "notes.txt", 1)
        assert service.retrieve(db, "chat", "apples")[0][1] == "apples first version"
        assert not service.activate(db, "other", "notes.txt", 1)
    finally:
        db.close()


def test_indexing_embeds_before_opening_its_write_scope_and_reindex_changes_namespace(tmp_path):
    holder = {}
    calls = []

    def batch(texts):
        assert not holder["db"].in_transaction
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    settings, service = make_service(tmp_path, batch=batch, embed=lambda text: [1.0, 0.0])
    db = db_connect(app_settings=settings)
    holder["db"] = db
    try:
        service.add_document(db, "chat", "notes.txt", b"apples and oranges")
        assert service.coverage(db, "chat") == (1, 1)
        _, revised = make_service(tmp_path, revision="2", batch=batch)
        assert revised.coverage(db, "chat") == (1, 0)
        assert revised.reindex(db, "chat") == (1, 1)
        assert revised.coverage(db, "chat") == (1, 1)
        assert len(calls) == 2
    finally:
        db.close()


def test_bundle_preserves_context_sources_and_disabled_mode(tmp_path):
    settings, service = make_service(tmp_path)
    from bridge.metadata import set_meta

    db = db_connect(app_settings=settings)
    try:
        service.add_document(db, "chat", "notes.txt", b"apples and oranges")
        bundle = service.bundle(db, "chat", "apples")
        assert bundle["sources"] == ["notes.txt"]
        assert service.context_for_prompt(db, "chat", "apples", bundle) == bundle["context"]
        assert service.citation_footer(db, "chat", "apples", bundle) == "\n\nSources: [notes.txt]"
        set_meta(db, "rag_mode:chat", "off")
        assert service.bundle(db, "chat", "apples") == {"results": [], "context": "", "sources": []}
        assert service.bundle(db, "other", "apples")["results"] == []
    finally:
        db.close()


@pytest.mark.parametrize("operation", ["activate", "remove", "query_cache"])
def test_existing_rag_mutations_do_not_commit_the_callers_transaction(tmp_path, monkeypatch, operation):
    # Characterize the existing reachable defect before migrating its owners.
    settings = load_app_settings({"SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS": "2"}, home=tmp_path)
    monkeypatch.setattr(_owner_embedding_transport, "embed_rag_batch", lambda texts, **kwargs: [None] * len(texts))
    monkeypatch.setattr(_owner_embedding_transport, "embed_rag_text", lambda *args, **kwargs: [1.0, 0.0])
    db = db_connect(app_settings=settings)
    try:
        _owner_rag_indexing.add_data_bank_document(
            db,
            "chat",
            "notes.txt",
            b"apples old",
            app_settings=settings,
            embedding_port=make_native_test_embedding_port(app_settings=settings),
        )
        _owner_rag_indexing.add_data_bank_document(
            db,
            "chat",
            "notes.txt",
            b"oranges new",
            app_settings=settings,
            embedding_port=make_native_test_embedding_port(app_settings=settings),
        )
        with pytest.raises(ValueError, match="abort"):
            with write_transaction(db):
                db.execute("INSERT INTO meta(key,value) VALUES('outer','keep-until-rollback')")
                if operation == "activate":
                    _owner_rag_indexing.activate_data_bank_version(db, "chat", "notes.txt", 1)
                elif operation == "remove":
                    _owner_rag_indexing.delete_data_bank_documents(db, "chat", "notes.txt")
                else:
                    _owner_rag_query.cached_rag_embedding(
                        db,
                        "query",
                        app_settings=settings,
                        embedding_port=make_native_test_embedding_port(app_settings=settings),
                    )
                assert db.in_transaction, f"{operation} committed the caller transaction"
                raise ValueError("abort")
        assert db.execute("SELECT value FROM meta WHERE key='outer'").fetchone() is None
        assert db.execute("SELECT COUNT(*) FROM data_bank_documents").fetchone()[0] == 2
        assert db.execute("SELECT version_number FROM data_bank_documents WHERE active=1").fetchone()[0] == 2
        assert (
            db.execute("SELECT COUNT(*) FROM rag_embedding_cache WHERE cache_key LIKE '%:query:%'").fetchone()[0] == 0
        )
    finally:
        db.close()


def test_legacy_rag_facades_are_retired_and_sql_stays_in_repository():
    for name in ("rag.py", "rag_core.py"):
        assert not (ROOT / "bridge" / name).exists(), name
    for name in ("rag_indexing", "rag_query"):
        module = owner(name)
        tree = ast.parse(Path(module.__file__).read_text())
        assert not any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in {"execute", "executemany", "executescript"}
            for n in ast.walk(tree)
        ), name


def test_startup_binds_one_rag_service_and_embedding_configuration_per_application(tmp_path, monkeypatch):
    import bridge.main as main
    from bridge.model_router import ModelRouter
    from bridge.rag_service import RagService

    captured = []
    monkeypatch.setattr(
        main,
        "embed_rag_batch",
        lambda texts, *, app_settings: captured.append((app_settings, list(texts))) or [None] * len(texts),
    )
    settings = load_app_settings({}, home=tmp_path)
    services = main._build_startup_services(settings, model_router=ModelRouter(load_catalog=lambda: {}))
    assert isinstance(services.rag, RagService)
    assert services.conversation.prepare_message.keywords["rag_service"] is services.rag
    assert services.conversation.generate_reply.keywords["rag_service"] is services.rag
    db = db_connect(app_settings=settings)
    try:
        assert services.rag.add_document(db, "chat", "notes.txt", b"fixture document") == ("added", 1)
        assert captured == [(settings, ["fixture document"])]
    finally:
        db.close()


def test_embedding_failure_before_ingestion_does_not_mutate_documents(tmp_path):
    def fail(texts):
        raise RuntimeError("fixture embedding failure")

    settings, service = make_service(tmp_path, batch=fail)
    db = db_connect(app_settings=settings)
    try:
        with pytest.raises(RuntimeError, match="fixture embedding failure"):
            service.add_document(db, "chat", "notes.txt", b"fixture text")
        assert service.documents(db, "chat") == []
        assert not db.in_transaction
    finally:
        db.close()


@pytest.mark.parametrize(
    "name,args",
    [
        ("store_embedding_cache", ([],)),
        ("insert_document", ("chat", "doc", "file.txt", 1, 1, 1, 0.0)),
        ("insert_chunk", ("chat", "doc", "file.txt", 0, "content")),
        ("store_embeddings", ([],)),
        ("deactivate_other_versions", ("chat", "file.txt", "doc", 0.0)),
        ("activate_document", ("chat", "file.txt", "doc", 0.0)),
        ("delete_document_rows", ("chat", "file.txt")),
    ],
)
def test_rag_repository_writers_refuse_unowned_transactions_before_sql(tmp_path, name, args):
    settings = load_app_settings({}, home=tmp_path)
    repository = owner("rag_repository")
    db = db_connect(app_settings=settings)
    calls = []
    db.set_trace_callback(calls.append)
    try:
        with pytest.raises(RuntimeError, match="active caller-owned transaction"):
            getattr(repository, name)(db, *args)
        assert calls == []
        assert not db.in_transaction
    finally:
        db.close()
