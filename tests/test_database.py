"""Tests for memory.database.Database."""

import json

from memory.database import Database


def test_schema_version(db):
    row = db.conn.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] == 3


def test_create_conversation(db):
    conv_id = db.create_conversation()
    assert isinstance(conv_id, int)
    assert conv_id >= 1


def test_insert_and_get_messages(db):
    conv_id = db.create_conversation()
    db.insert_message(conv_id, "user", "Ahoj")
    db.insert_message(conv_id, "assistant", "Dobrý den!")

    msgs = db.get_session_messages(conv_id)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "Ahoj"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "Dobrý den!"


def test_structured_profile_default(db):
    profile = db.get_structured_profile()
    assert isinstance(profile, dict)
    assert profile["version"] == 2
    assert profile["basic"]["name"] is None


def test_structured_profile_roundtrip(db):
    profile = db.get_structured_profile()
    profile["basic"]["name"] = "Test"
    profile["interests"]["hobbies"] = ["coding"]
    db.save_structured_profile(profile)

    loaded = db.get_structured_profile()
    assert loaded["basic"]["name"] == "Test"
    assert loaded["interests"]["hobbies"] == ["coding"]


def test_profile_summary_excludes_changelog(db):
    profile = db.get_structured_profile()
    profile["basic"]["name"] = "Jan"
    profile["_changelog"] = [{"field": "basic.name", "old": None, "new": "Jan"}]
    db.save_structured_profile(profile)

    summary = db.get_user_profile_summary()
    assert "Jan" in summary
    assert "_changelog" not in summary
    assert "changelog" not in summary.lower()


def test_profile_snapshots(db):
    db.save_profile_snapshot('{"test": 1}')
    db.save_profile_snapshot('{"test": 2}')
    db.save_profile_snapshot('{"test": 3}')

    db.cleanup_old_snapshots(keep=2)

    rows = db.conn.execute("SELECT * FROM profile_snapshots").fetchall()
    assert len(rows) == 2


def test_recent_summaries(db):
    conv_id = db.create_conversation()
    db.insert_message(conv_id, "user", "test")
    db.update_conversation_summary(conv_id, "Test summary")
    db.end_conversation(conv_id)

    summaries = db.get_recent_summaries(limit=5)
    assert len(summaries) == 1
    assert summaries[0]["summary"] == "Test summary"


def test_clear_all(db):
    conv_id = db.create_conversation()
    db.insert_message(conv_id, "user", "test")
    db.set_user_profile("name", "Jan")
    profile = db.get_structured_profile()
    profile["basic"]["name"] = "Jan"
    db.save_structured_profile(profile)
    db.save_profile_snapshot('{"test": 1}')

    db.clear_all()

    assert db.get_session_messages(conv_id) == []
    assert db.get_all_profile() == {}
    rows = db.conn.execute("SELECT * FROM profile_snapshots").fetchall()
    assert len(rows) == 0


def test_is_first_run(db):
    assert db.is_first_run() is True

    profile = db.get_structured_profile()
    profile["basic"]["name"] = "Jan"
    db.save_structured_profile(profile)

    assert db.is_first_run() is False


def test_previous_session_messages(db):
    # Create and finish a conversation
    conv_id = db.create_conversation()
    db.insert_message(conv_id, "user", "Ahoj")
    db.insert_message(conv_id, "assistant", "Čau")
    db.end_conversation(conv_id)

    msgs = db.get_previous_session_messages(limit=10)
    assert len(msgs) == 2
    assert msgs[0]["content"] == "Ahoj"
    assert msgs[1]["content"] == "Čau"


def test_profile_summary_people(db):
    """People section appears in profile summary."""
    profile = db.get_structured_profile()
    profile["basic"]["name"] = "Matouš"
    profile["people"] = {
        "Robert": {
            "relation": "strýc",
            "notes": ["zručný", "rád jezdí do lesa"],
            "location": "Hejnice u Žamberka",
        },
        "Jindřich": {
            "relation": "kamarád strýce Roberta",
            "notes": ["přezdívka Pinďa"],
        },
    }
    db.save_structured_profile(profile)

    summary = db.get_user_profile_summary()
    assert "Lidé:" in summary
    assert "Robert (strýc)" in summary
    assert "Hejnice u Žamberka" in summary
    assert "Jindřich" in summary
    assert "Pinďa" in summary


def test_profile_summary_people_empty(db):
    """Empty people section does not appear in summary."""
    profile = db.get_structured_profile()
    profile["basic"]["name"] = "Jan"
    profile["people"] = {}
    db.save_structured_profile(profile)

    summary = db.get_user_profile_summary()
    assert "Lidé:" not in summary
