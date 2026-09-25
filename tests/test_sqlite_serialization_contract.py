"""Real SQLite statement and cursor lifetimes must agree with the process writer gate."""

from __future__ import annotations

import gc
import sqlite3
import threading

import pytest

from bridge import sqlite_store


def lock_available_to_another_thread():
    results = []

    def probe():
        acquired = sqlite_store._DB_WRITE_LOCK.acquire(timeout=0.05)
        results.append(acquired)
        if acquired:
            sqlite_store._DB_WRITE_LOCK.release()

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive(), "bounded process-lock probe did not finish"
    return results == [True]


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:", factory=sqlite_store._SerializedSQLiteConnection)
    connection.execute("CREATE TABLE data(value INTEGER UNIQUE)")
    connection.commit()
    yield connection
    connection.close()
    assert lock_available_to_another_thread(), "connection leaked a process write lock"


@pytest.mark.parametrize(
    "sql",
    [
        "/* leading comment */ INSERT INTO data VALUES(1)",
        "-- leading comment\nINSERT INTO data VALUES(1)",
        "/* first */ -- second\n INSERT INTO data VALUES(1)",
        "WITH item AS (SELECT 1) INSERT INTO data SELECT * FROM item",
        "PRAGMA user_version=7",
    ],
)
def test_comment_cte_and_pragma_writes_hold_the_transaction_gate(db, sql):
    db.execute("BEGIN")
    db.execute(sql)
    assert db.in_transaction
    assert not lock_available_to_another_thread()
    db.rollback()
    assert lock_available_to_another_thread()


@pytest.mark.parametrize("ending", ["COMMIT", "END", "ROLLBACK"])
def test_sql_transaction_control_releases_gate(db, ending):
    db.execute("INSERT INTO data VALUES(1)")
    assert not lock_available_to_another_thread()
    db.execute(ending)
    assert not db.in_transaction
    assert lock_available_to_another_thread()


def test_multiple_writes_use_one_latch_until_transaction_end(db):
    for value in range(3):
        db.execute("INSERT INTO data VALUES(?)", (value,))
    assert not lock_available_to_another_thread()
    db.commit()
    assert lock_available_to_another_thread()
    assert db.execute("SELECT count(*) FROM data").fetchone() == (3,)


def test_failed_statement_does_not_release_an_existing_transaction(db):
    db.execute("INSERT INTO data VALUES(1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO data VALUES(1)")
    assert db.in_transaction
    assert not lock_available_to_another_thread()
    db.execute("INSERT INTO data VALUES(2)")
    db.rollback()
    assert db.execute("SELECT * FROM data").fetchall() == []
    assert lock_available_to_another_thread()


@pytest.mark.parametrize("fail", [False, True])
def test_standard_connection_context_releases_gate(db, fail):
    try:
        with db:
            db.execute("INSERT INTO data VALUES(1)")
            if fail:
                raise ValueError("rollback fixture")
    except ValueError:
        assert fail
    assert not db.in_transaction
    assert lock_available_to_another_thread()
    assert db.execute("SELECT count(*) FROM data").fetchone()[0] == (0 if fail else 1)


def test_raw_cursor_execute_and_executemany_use_the_same_gate(db):
    cursor = db.cursor()
    try:
        cursor.execute("-- comment\nINSERT INTO data VALUES(1)")
        cursor.executemany("INSERT INTO data VALUES(?)", [(2,), (3,)])
        assert not lock_available_to_another_thread()
        db.commit()
        assert lock_available_to_another_thread()
    finally:
        cursor.close()


def test_executescript_keeps_an_explicit_transaction_serialized(db):
    db.executescript("BEGIN; INSERT INTO data VALUES(1); INSERT INTO data VALUES(2);")
    assert db.in_transaction
    assert not lock_available_to_another_thread()
    db.rollback()
    assert lock_available_to_another_thread()
    assert db.execute("SELECT * FROM data").fetchall() == []


def test_savepoint_lifetime_is_a_transaction_lifetime(db):
    db.execute("SAVEPOINT outer")
    assert not lock_available_to_another_thread()
    db.execute("INSERT INTO data VALUES(1)")
    db.execute("SAVEPOINT inner")
    db.execute("INSERT INTO data VALUES(2)")
    db.execute("ROLLBACK TO inner")
    assert not lock_available_to_another_thread()
    db.execute("RELEASE inner")
    assert not lock_available_to_another_thread()
    db.execute("RELEASE outer")
    assert lock_available_to_another_thread()
    assert db.execute("SELECT * FROM data").fetchall() == [(1,)]


@pytest.mark.parametrize(
    "sql", ["INSERT INTO data VALUES(1)", "WITH item AS (SELECT 1) INSERT INTO data SELECT * FROM item"]
)
def test_autocommit_write_releases_gate_after_statement(db, sql):
    db.isolation_level = None
    db.execute(sql)
    assert not db.in_transaction
    assert lock_available_to_another_thread()


@pytest.mark.parametrize("ending", ["fetchall", "iterate", "close", "drop"])
def test_autocommit_returning_keeps_gate_until_cursor_finished(db, ending):
    db.isolation_level = None
    cursor = db.execute("WITH item AS (SELECT 1) INSERT INTO data SELECT * FROM item RETURNING value")
    assert not db.in_transaction  # SQLite autocommit can still have a busy writing statement.
    assert not lock_available_to_another_thread()
    db.commit()  # No-op when no explicit transaction: must not unlock the live statement.
    assert not lock_available_to_another_thread()
    if ending == "fetchall":
        assert cursor.fetchall() == [(1,)]
    elif ending == "iterate":
        assert list(cursor) == [(1,)]
    elif ending == "close":
        cursor.close()
    else:
        del cursor
        gc.collect()
    assert lock_available_to_another_thread()


def test_simple_reads_do_not_keep_a_write_gate(db):
    cursor = db.execute("SELECT 1 UNION ALL SELECT 2")
    try:
        assert lock_available_to_another_thread()
        assert cursor.fetchall() == [(1,), (2,)]
    finally:
        cursor.close()


def test_deferred_commit_failure_rolls_back_owned_transaction(db):
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
    db.execute("CREATE TABLE child(parent_id REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)")
    db.commit()
    with pytest.raises(sqlite3.IntegrityError), sqlite_store.write_transaction(db):
        db.execute("INSERT INTO child VALUES(99)")
    assert not db.in_transaction
    assert lock_available_to_another_thread()
    assert db.execute("SELECT * FROM child").fetchall() == []


def test_nested_sql_from_a_callback_does_not_unbalance_gate(db):
    def insert(value):
        db.execute("INSERT INTO data VALUES(?)", (value,))
        return value

    db.create_function("insert_value", 1, insert)
    assert db.execute("SELECT insert_value(1)").fetchall() == [(1,)]
    assert not lock_available_to_another_thread()
    db.commit()
    assert lock_available_to_another_thread()


def test_syntax_error_without_transaction_does_not_leak_gate(db):
    with pytest.raises(sqlite3.OperationalError):
        db.execute("WITH invalid syntax")
    assert lock_available_to_another_thread()


@pytest.mark.parametrize("method", ["fetchone", "next"])
def test_none_row_factory_is_not_mistaken_for_end_of_writing_cursor(db, method):
    db.isolation_level = None
    db.row_factory = lambda _cursor, _row: None
    cursor = db.execute("INSERT INTO data VALUES(1),(2),(3) RETURNING value")
    try:
        assert not lock_available_to_another_thread()
        first = cursor.fetchone() if method == "fetchone" else next(cursor)
        assert first is None
        assert not lock_available_to_another_thread(), "a custom row is not cursor exhaustion"
        assert cursor.fetchall() == [None, None]
        assert lock_available_to_another_thread()
    finally:
        cursor.close()


def test_failed_commit_keeps_gate_until_rollback(db):
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
    db.execute("CREATE TABLE child(parent_id REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)")
    db.execute("INSERT INTO child VALUES(1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.commit()
    assert db.in_transaction
    assert not lock_available_to_another_thread()
    db.rollback()
    assert lock_available_to_another_thread()


def test_closed_connection_rejects_cursor_operations_without_leaking_gate(db):
    cursor = db.execute("INSERT INTO data VALUES(1) RETURNING value")
    db.close()
    assert lock_available_to_another_thread()
    with pytest.raises(sqlite3.ProgrammingError):
        cursor.fetchone()
    assert lock_available_to_another_thread()
