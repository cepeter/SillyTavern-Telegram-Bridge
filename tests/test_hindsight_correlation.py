"""Correlation follows the actual oldest fact and never resolves ambiguous writes."""

import logging
from types import SimpleNamespace

import pytest
from memory_cleanup_test_support import append, fact, reset, run_fact
from memory_cleanup_test_support import cleanup_runtime as cleanup_runtime
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime

from bridge import memory_backend


def test_timeout_correlation_is_per_actual_document_and_attempt(cleanup_runtime, caplog):
    rt = cleanup_runtime
    append(rt)
    oldest_doc = fact(rt, "PRIVATE_OLDEST_FACT")
    append(rt, "PRIVATE_NEW_SOURCE")
    newer_doc = fact(rt, "PRIVATE_NEWER_FACT")
    rt.db.execute("UPDATE memory_fact_index SET created_at=1 WHERE document_id=?", (oldest_doc,))
    rt.db.execute("UPDATE memory_fact_index SET created_at=2 WHERE document_id=?", (newer_doc,))
    rt.db.commit()
    caplog.set_level(logging.INFO, logger="bridge.events")

    def timeout(values):
        raise TimeoutError("PRIVATE_TIMEOUT_CANARY")

    rt.archive.before_retain = timeout
    assert run_fact(rt) == "retain_failed"
    first = rt.archive.retained[0]
    assert first["document_id"] == oldest_doc
    metadata = first["metadata"]
    assert metadata.get("bridge_attempt_ref"), "request metadata needs an opaque per-attempt reference"
    assert metadata.get("bridge_document_ref")
    failed = next(
        r.diagnostic_fields
        for r in caplog.records
        if getattr(r, "diagnostic_fields", {}).get("event") == "memory.hindsight_retain_failed"
    )
    assert failed["attempt_ref"] == metadata["bridge_attempt_ref"]
    assert failed["document_ref"] == metadata["bridge_document_ref"]
    assert "source_message_id" not in failed, "the job target is not the fact attempted"
    assert "server_operation_ref" not in failed
    assert "PRIVATE" not in caplog.text
    oldest_token = rt.db.execute("SELECT attempt_token FROM memory_archival_attempts").fetchone()[0]
    rt.archive.before_retain = None
    rt.db.execute("UPDATE memory_jobs SET next_attempt_at=0")
    rt.db.commit()
    assert run_fact(rt) == "complete"
    second = rt.archive.retained[1]
    assert second["document_id"] == oldest_doc
    assert second["metadata"]["bridge_attempt_ref"] != metadata["bridge_attempt_ref"]
    assert second["metadata"]["bridge_document_ref"] == metadata["bridge_document_ref"]
    assert rt.db.execute("SELECT attempt_token,finished FROM memory_archival_attempts").fetchall() == [
        (oldest_token, 0)
    ]
    reset(rt)
    # Exact-ID cleanup can run while queued discovery remains deferred.
    rt.db.execute("UPDATE memory_cleanup_discovery SET phase='verify',next_attempt_at=99999999999")
    rt.db.commit()
    rt.archive.objects[oldest_doc] = ["session:s"]
    memory_backend.cleanup_retired_memory_documents(rt.db, "c", "s", app_settings=rt.config)
    assert oldest_doc not in rt.archive.objects
    assert rt.db.execute(
        "SELECT finished FROM memory_archival_attempts WHERE attempt_token=?", (oldest_token,)
    ).fetchone() == (0,)


@pytest.mark.parametrize("operation", [None, "PRIVATE_SERVER_CANARY", "d6b89366-62c0-4598-9ed2-8235fb4af9c4"])
def test_only_real_safe_server_operations_are_reported(cleanup_runtime, monkeypatch, caplog, operation):
    rt = cleanup_runtime
    append(rt)
    fact(rt)
    retain = rt.archive.retain

    def completed(**values):
        retain(**values)
        return SimpleNamespace(operation_id=operation, operation_ids=None)

    monkeypatch.setattr(rt.archive, "retain", completed)
    caplog.set_level(logging.INFO, logger="bridge.events")
    assert run_fact(rt) == "complete"
    records = [
        r.diagnostic_fields
        for r in caplog.records
        if getattr(r, "diagnostic_fields", {}).get("event") == "memory.hindsight_retain_finished"
    ]
    assert records, "successful synchronous response needs correlation diagnostics"
    assert bool(records[0].get("server_operation_ref")) is (operation is not None and operation.startswith("d6b"))
    assert "PRIVATE" not in repr(records)
