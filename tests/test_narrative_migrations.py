"""Migration contracts for the approved Narrative Engine foundation."""

import json
import sqlite3
from contextlib import closing

import pytest

from bridge.migrations import MigrationError, run_migrations
from bridge.schema import SCHEMA_MIGRATIONS

NARRATIVE_TABLES = {
    "narrative_defaults",
    "narrative_settings",
    "narrative_state",
    "narrative_scenes",
    "narrative_threads",
    "narrative_arcs",
    "director_state",
    "director_decisions",
    "ending_state",
    "ending_goal_history",
    "narrative_checkpoints",
}


def old_database():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    run_migrations(db, tuple(item for item in SCHEMA_MIGRATIONS if item.version < 10))
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("chat", "story", "Story", "card.png", "fixture::model", "", "", 1.0, 1.0),
    )
    db.execute("INSERT INTO director_goals VALUES(?,?,?,?)", ("chat", "story", "Keep the gate closed.", 2.0))
    db.execute("INSERT INTO scene_states VALUES(?,?,?,?,?)", ("chat", "story", '{"location":"Gate"}', 0, 2.0))
    db.commit()
    return db


def table_names(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_narrative_migration_is_registered_after_existing_history():
    assert [migration.version for migration in SCHEMA_MIGRATIONS] == list(range(1, 11))
    assert SCHEMA_MIGRATIONS[-1].name == "narrative_engine_foundation"


def test_upgrade_preserves_goals_and_physical_scene_and_pins_existing_defaults():
    with closing(old_database()) as db:
        before_scene = db.execute("SELECT * FROM scene_states").fetchall()
        run_migrations(db, SCHEMA_MIGRATIONS)
        assert NARRATIVE_TABLES <= table_names(db)
        assert "director_goals" not in table_names(db)
        assert db.execute("SELECT chat_id,session_id,goal FROM director_state").fetchall() == [
            ("chat", "story", "Keep the gate closed.")
        ]
        assert db.execute("SELECT * FROM scene_states").fetchall() == before_scene
        row = db.execute("SELECT settings_json FROM narrative_settings WHERE chat_id='chat'").fetchone()
        settings = json.loads(row[0])
        assert settings["preset"] == "player_centric"
        assert settings["user_control"] == "physical_continuity"
        assert settings["ending_mode"] == "open_ended"
        assert db.execute("SELECT COUNT(*) FROM narrative_scenes").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM narrative_arcs").fetchone()[0] == 0


def test_each_session_owned_table_has_composite_cascade():
    with closing(old_database()) as db:
        run_migrations(db, SCHEMA_MIGRATIONS)
        for table in sorted(NARRATIVE_TABLES - {"narrative_defaults"}):
            foreign_keys = db.execute("SELECT * FROM pragma_foreign_key_list(?)", (table,)).fetchall()
            owner = {row[3]: row[4] for row in foreign_keys if row[2] == "sessions" and row[6] == "CASCADE"}
            assert owner == {"chat_id": "chat_id", "session_id": "session_id"}, table
        db.execute("DELETE FROM sessions WHERE chat_id=? AND session_id=?", ("chat", "story"))
        db.commit()
        assert db.execute("SELECT COUNT(*) FROM narrative_settings").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM director_state").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM scene_states").fetchone()[0] == 0


def test_second_migration_run_changes_no_application_data():
    with closing(old_database()) as db:
        run_migrations(db, SCHEMA_MIGRATIONS)
        before = db.total_changes
        run_migrations(db, SCHEMA_MIGRATIONS)
        assert db.total_changes == before
        assert db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=10").fetchone()[0] == 1


def test_corrupted_goal_copy_rolls_back_before_legacy_data_is_dropped():
    from bridge.narrative_schema import migrate_narrative_engine_foundation

    with closing(old_database()) as db:
        # A pre-existing destination with the wrong value models an interrupted
        # manual migration. Matching counts alone must not authorize data loss.
        db.execute(
            "CREATE TABLE director_state(chat_id TEXT NOT NULL,session_id TEXT NOT NULL,"
            "goal TEXT NOT NULL DEFAULT '',state_revision INTEGER NOT NULL DEFAULT 0,"
            "updated_at REAL NOT NULL,PRIMARY KEY(chat_id,session_id))"
        )
        db.execute("INSERT INTO director_state VALUES(?,?,?,?,?)", ("chat", "story", "Wrong goal", 0, 2.0))
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises((ValueError, RuntimeError, sqlite3.DatabaseError), match="goal|schema|column"):
                migrate_narrative_engine_foundation(db)
        finally:
            db.rollback()
        assert "director_goals" in table_names(db)
        assert db.execute("SELECT goal FROM director_goals").fetchone() == ("Keep the gate closed.",)


def test_failed_migration_is_not_recorded_in_ledger(monkeypatch):
    from bridge import narrative_schema

    with closing(old_database()) as db:
        original = narrative_schema.verify_director_goal_copy

        def reject_copy(connection):
            original(connection)
            raise ValueError("Director goal copy verification failed")

        monkeypatch.setattr(narrative_schema, "verify_director_goal_copy", reject_copy)
        with pytest.raises(MigrationError, match="goal copy"):
            run_migrations(db, SCHEMA_MIGRATIONS)
        assert db.execute("SELECT version FROM schema_migrations WHERE version=10").fetchone() is None
        assert "director_goals" in table_names(db)
        assert "narrative_settings" not in table_names(db)
