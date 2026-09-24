"""Opaque callback tokens are scoped, durable, and transaction-safe."""

from __future__ import annotations

import logging
import re
import sqlite3
from unittest.mock import patch

import pytest

from bridge import callback_tokens as tokens


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / "tokens.sqlite3")
    connection.executescript("""
        CREATE TABLE callback_tokens (token TEXT PRIMARY KEY, kind TEXT NOT NULL,
            value TEXT NOT NULL, chat_id TEXT NOT NULL, expires_at REAL NOT NULL);
        CREATE TABLE probe (value TEXT);
    """)
    yield connection
    connection.close()


def test_equal_inputs_get_different_opaque_tokens(db):
    first = tokens.dynamic_callback_token("character", "card.png", "owner", db=db)
    second = tokens.dynamic_callback_token("character", "card.png", "owner", db=db)
    assert first != second
    assert re.fullmatch(r"t[A-Za-z0-9_-]{22}", first)
    assert len("characterdeleteconfirm:" + first) <= 64
    assert tokens.resolve_dynamic_callback_token(first, "character", "owner", db=db) == "card.png"


@pytest.mark.parametrize("chat", ["", " ", None])
def test_mint_rejects_empty_scope(db, chat):
    with pytest.raises(ValueError, match="chat"):
        tokens.dynamic_callback_token("character", "card.png", chat, db=db)
    assert db.execute("SELECT count(*) FROM callback_tokens").fetchone()[0] == 0


def test_chat_argument_is_required(db):
    with pytest.raises(TypeError):
        tokens.dynamic_callback_token("character", "card.png", db=db)
    with pytest.raises(TypeError):
        tokens.resolve_dynamic_callback_token("unknown", "character", db=db)


def test_legacy_unbound_rows_are_rejected(db):
    db.execute("INSERT INTO callback_tokens VALUES ('legacy', 'character', 'card.png', '', 9999999999)")
    db.commit()
    assert tokens.resolve_dynamic_callback_token("legacy", "character", "other", db=db) is None


def test_database_is_authoritative_not_process_cache(db):
    token = tokens.dynamic_callback_token("character", "card.png", "owner", db=db)
    db.execute("DELETE FROM callback_tokens WHERE token=?", (token,))
    db.commit()
    assert tokens.resolve_dynamic_callback_token(token, "character", "owner", db=db) is None
    assert not hasattr(tokens, "_CALLBACK_TOKEN_VALUES")


def test_collision_retries_without_replacing_mapping(db):
    duplicate, unique = "A" * 22, "B" * 22
    db.execute("INSERT INTO callback_tokens VALUES (?, 'world', 'keep.json', 'owner', 9999999999)", ("t" + duplicate,))
    db.commit()
    with patch("bridge.callback_tokens.secrets.token_urlsafe", side_effect=[duplicate, unique]):
        token = tokens.dynamic_callback_token("character", "card.png", "owner", db=db)
    assert token == "t" + unique
    assert (
        db.execute("SELECT value FROM callback_tokens WHERE token=?", ("t" + duplicate,)).fetchone()[0] == "keep.json"
    )


def test_standalone_mint_is_durable_in_second_connection(db):
    token = tokens.dynamic_callback_token("character", "card.png", "owner", db=db)
    path = db.execute("PRAGMA database_list").fetchone()[2]
    with sqlite3.connect(path) as other:
        assert tokens.resolve_dynamic_callback_token(token, "character", "owner", db=other) == "card.png"
    assert not db.in_transaction


def test_mint_does_not_commit_callers_pending_writes(db):
    db.execute("INSERT INTO probe VALUES ('pending')")
    token = tokens.dynamic_callback_token("character", "card.png", "owner", db=db)
    assert db.in_transaction
    db.rollback()
    assert db.execute("SELECT count(*) FROM probe").fetchone()[0] == 0
    assert tokens.resolve_dynamic_callback_token(token, "character", "owner", db=db) is None


def test_persistence_failure_does_not_issue_ram_only_token(db, caplog):
    db.execute("DROP TABLE callback_tokens")
    db.commit()
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="persist callback"):
            tokens.dynamic_callback_token("character", "card.png", "owner", db=db)
    assert "Could not persist callback token" in caplog.text
    assert "card.png" not in caplog.text


def test_mint_prunes_expired_rows_in_its_write_transaction(db, monkeypatch):
    db.execute("INSERT INTO callback_tokens VALUES ('expired', 'character', 'old.png', 'owner', 1)")
    db.commit()
    monkeypatch.setattr(tokens.time, "time", lambda: 1000.0)
    tokens.dynamic_callback_token("character", "card.png", "owner", db=db)
    assert db.execute("SELECT count(*) FROM callback_tokens").fetchone()[0] == 1


def test_expiry_is_exclusive(db, monkeypatch):
    monkeypatch.setattr(tokens.time, "time", lambda: 1000.0)
    db.execute("INSERT INTO callback_tokens VALUES ('expired', 'character', 'old.png', 'owner', 1000.0)")
    db.commit()
    assert tokens.resolve_dynamic_callback_token("expired", "character", "owner", db=db) is None


def test_collision_attempts_are_bounded_and_preserve_existing(db):
    duplicate = "C" * 22
    db.execute("INSERT INTO callback_tokens VALUES (?, 'world', 'keep.json', 'owner', 9999999999)", ("t" + duplicate,))
    db.commit()
    with patch("bridge.callback_tokens.secrets.token_urlsafe", return_value=duplicate) as random_token:
        with pytest.raises(RuntimeError, match="persist callback"):
            tokens.dynamic_callback_token("character", "new.png", "owner", db=db)
    assert random_token.call_count == 8
    assert db.execute("SELECT count(*) FROM callback_tokens").fetchone()[0] == 1
    assert not db.in_transaction


def test_random_source_failure_rolls_back_owned_write(db):
    with patch("bridge.callback_tokens.secrets.token_urlsafe", side_effect=OSError("entropy source failed")):
        with pytest.raises(RuntimeError, match="persist callback"):
            tokens.dynamic_callback_token("character", "new.png", "owner", db=db)
    assert not db.in_transaction


def test_same_token_is_not_shared_between_databases(db):
    token = tokens.dynamic_callback_token("character", "card.png", "owner", db=db)
    second = sqlite3.connect(":memory:")
    second.execute(
        "CREATE TABLE callback_tokens(token TEXT PRIMARY KEY, kind TEXT, value TEXT, chat_id TEXT, expires_at REAL)"
    )
    try:
        assert tokens.resolve_dynamic_callback_token(token, "character", "owner", db=second) is None
    finally:
        second.close()
