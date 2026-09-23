"""Database reads must not decide an enclosing application's transaction."""
import sqlite3

import pytest

from bridge import callback_tokens


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(callback_tokens, "_CALLBACK_TOKEN_VALUES", {})
    monkeypatch.setattr(callback_tokens.time, "time", lambda: 1000.0)
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE callback_tokens (
            token TEXT PRIMARY KEY, kind TEXT, value TEXT,
            chat_id TEXT, expires_at REAL
        );
        CREATE TABLE probe (value TEXT);
    """)
    yield conn
    conn.close()


def seed(db, *, expires=999.0, cached=False, chat="owner"):
    item = ("character", "card.png", chat, expires)
    db.execute("INSERT INTO callback_tokens VALUES (?,?,?,?,?)", ("token", *item))
    db.commit()
    if cached:
        callback_tokens._CALLBACK_TOKEN_VALUES["token"] = item


@pytest.mark.parametrize("cached", [False, True])
def test_expiry_does_not_commit_unrelated_work(db, cached):
    seed(db, cached=cached)
    db.execute("INSERT INTO probe VALUES ('pending')")
    assert callback_tokens.resolve_dynamic_callback_token(
        "token", "character", "owner", db=db
    ) is None
    assert db.in_transaction
    db.rollback()
    assert db.execute("SELECT count(*) FROM probe").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM callback_tokens").fetchone()[0] == 1


@pytest.mark.parametrize("cached", [False, True])
def test_expiry_is_select_only_and_evicts_expired_cache(db, cached):
    seed(db, cached=cached)
    statements = []
    db.set_trace_callback(statements.append)
    assert callback_tokens.resolve_dynamic_callback_token(
        "token", "character", "owner", db=db
    ) is None
    assert all(sql.lstrip().upper().startswith("SELECT ") for sql in statements)
    assert not db.in_transaction
    assert "token" not in callback_tokens._CALLBACK_TOKEN_VALUES
    assert db.execute("SELECT count(*) FROM callback_tokens").fetchone()[0] == 1


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("kind,chat", [("model", "owner"), ("character", "other")])
def test_binding_mismatch_preserves_legitimate_token_and_transaction(db, cached, kind, chat):
    seed(db, expires=1001.0, cached=cached)
    db.execute("INSERT INTO probe VALUES ('pending')")
    assert callback_tokens.resolve_dynamic_callback_token("token", kind, chat, db=db) is None
    assert db.in_transaction
    db.rollback()
    assert db.execute("SELECT count(*) FROM probe").fetchone()[0] == 0
    assert callback_tokens.resolve_dynamic_callback_token(
        "token", "character", "owner", db=db
    ) == "card.png"
    assert db.execute("SELECT count(*) FROM callback_tokens").fetchone()[0] == 1


@pytest.mark.parametrize("cached", [False, True])
def test_valid_token_resolves_without_transaction_changes(db, cached):
    seed(db, expires=1000.0, cached=cached)
    db.execute("INSERT INTO probe VALUES ('pending')")
    assert callback_tokens.resolve_dynamic_callback_token(
        "token", "character", "owner", db=db
    ) == "card.png"
    assert db.in_transaction
    db.rollback()
    assert db.execute("SELECT count(*) FROM probe").fetchone()[0] == 0


def test_unbound_token_resolves_for_any_chat(db):
    seed(db, expires=1001.0, chat="")
    assert callback_tokens.resolve_dynamic_callback_token(
        "token", "character", "any-chat", db=db
    ) == "card.png"


def test_missing_token_does_not_commit(db):
    db.execute("INSERT INTO probe VALUES ('pending')")
    assert callback_tokens.resolve_dynamic_callback_token(
        "missing", "character", "owner", db=db
    ) is None
    assert db.in_transaction
    db.rollback()
    assert db.execute("SELECT count(*) FROM probe").fetchone()[0] == 0
