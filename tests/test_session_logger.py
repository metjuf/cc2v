"""Tests for session_logger — SessionLogger + JSONFormatter."""

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from session_logger import SessionLogger, JSONFormatter


@pytest.fixture
def log_dirs(tmp_path):
    """Provide temporary log and sessions directories."""
    return tmp_path / "logs", tmp_path / "sessions"


@pytest.fixture
def slog(log_dirs):
    """SessionLogger with temporary directories."""
    log_dir, sessions_dir = log_dirs
    logger = SessionLogger(
        session_id="test-42",
        log_dir=log_dir,
        sessions_dir=sessions_dir,
        enabled=True,
    )
    yield logger
    logger.close()


def test_session_logger_creates_file(slog, log_dirs):
    _, sessions_dir = log_dirs
    session_files = list(sessions_dir.glob("*.jsonl"))
    assert len(session_files) == 1
    assert "test-42" in session_files[0].name


def test_log_event_format(slog, log_dirs):
    slog.log("test_event", key="value", number=42)

    _, sessions_dir = log_dirs
    session_file = list(sessions_dir.glob("*.jsonl"))[0]
    lines = session_file.read_text().strip().split("\n")
    entry = json.loads(lines[0])

    assert entry["event"] == "test_event"
    assert entry["session"] == "test-42"
    assert "ts" in entry
    assert entry["data"]["key"] == "value"
    assert entry["data"]["number"] == 42


def test_log_user_message(slog, log_dirs):
    slog.log_user_message("Ahoj Eigy", mood="happy")

    _, sessions_dir = log_dirs
    session_file = list(sessions_dir.glob("*.jsonl"))[0]
    lines = session_file.read_text().strip().split("\n")
    entry = json.loads(lines[0])

    assert entry["event"] == "user_message"
    assert entry["data"]["text"] == "Ahoj Eigy"
    assert entry["data"]["mood"] == "happy"
    assert entry["data"]["length"] == 9


def test_log_assistant_message(slog, log_dirs):
    slog.log_assistant_message("Dobrý den!", emotion="happy", tokens=3)

    _, sessions_dir = log_dirs
    session_file = list(sessions_dir.glob("*.jsonl"))[0]
    entry = json.loads(session_file.read_text().strip())

    assert entry["event"] == "assistant_message"
    assert entry["data"]["emotion"] == "happy"
    assert entry["data"]["tokens"] == 3
    assert entry["data"]["length"] == 10


def test_log_mood_detected(slog, log_dirs):
    slog.log_mood_detected("frustrated", "keyword")

    _, sessions_dir = log_dirs
    session_file = list(sessions_dir.glob("*.jsonl"))[0]
    entry = json.loads(session_file.read_text().strip())

    assert entry["event"] == "mood_detected"
    assert entry["data"]["mood"] == "frustrated"
    assert entry["data"]["method"] == "keyword"


def test_log_context_built(slog, log_dirs):
    slog.log_context_built(num_messages=15, total_tokens=5000, trimmed=True, tokens_before=6000)

    _, sessions_dir = log_dirs
    session_file = list(sessions_dir.glob("*.jsonl"))[0]
    entry = json.loads(session_file.read_text().strip())

    assert entry["event"] == "context_built"
    assert entry["data"]["num_messages"] == 15
    assert entry["data"]["total_tokens"] == 5000
    assert entry["data"]["trimmed"] is True
    assert entry["data"]["tokens_before"] == 6000


def test_log_search(slog, log_dirs):
    slog.log_search("cena bitcoinu", num_results=5)

    _, sessions_dir = log_dirs
    session_file = list(sessions_dir.glob("*.jsonl"))[0]
    entry = json.loads(session_file.read_text().strip())

    assert entry["event"] == "search_triggered"
    assert entry["data"]["query"] == "cena bitcoinu"
    assert entry["data"]["num_results"] == 5


def test_log_session_start_end(log_dirs):
    log_dir, sessions_dir = log_dirs
    slog = SessionLogger(
        session_id="lifecycle-1",
        log_dir=log_dir,
        sessions_dir=sessions_dir,
    )
    slog.log_session_start("TestUser", {"model": "claude-sonnet-4"})
    slog.log_user_message("test")
    slog.close()

    session_file = list(sessions_dir.glob("*lifecycle-1.jsonl"))[0]
    lines = session_file.read_text().strip().split("\n")
    entries = [json.loads(line) for line in lines]

    assert entries[0]["event"] == "session_start"
    assert entries[0]["data"]["user_name"] == "TestUser"
    assert entries[-1]["event"] == "session_end"
    assert "duration_seconds" in entries[-1]["data"]


def test_json_formatter():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test_module",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="Test message: %s",
        args=("hello",),
        exc_info=None,
    )
    output = formatter.format(record)
    entry = json.loads(output)

    assert entry["level"] == "INFO"
    assert entry["module"] == "test_module"
    assert entry["msg"] == "Test message: hello"
    assert "ts" in entry


def test_close_writes_session_end(log_dirs):
    log_dir, sessions_dir = log_dirs
    slog = SessionLogger(
        session_id="close-test",
        log_dir=log_dir,
        sessions_dir=sessions_dir,
    )
    slog.close()

    session_file = list(sessions_dir.glob("*close-test.jsonl"))[0]
    lines = session_file.read_text().strip().split("\n")
    last = json.loads(lines[-1])
    assert last["event"] == "session_end"


def test_disabled_logging(tmp_path):
    slog = SessionLogger(
        session_id="disabled-1",
        log_dir=tmp_path / "logs",
        sessions_dir=tmp_path / "sessions",
        enabled=False,
    )
    slog.log("test_event", key="value")
    slog.close()

    # No files should be created
    assert not (tmp_path / "sessions").exists() or not list((tmp_path / "sessions").glob("*.jsonl"))


def test_system_log_created(log_dirs):
    log_dir, sessions_dir = log_dirs
    slog = SessionLogger(
        session_id="syslog-1",
        log_dir=log_dir,
        sessions_dir=sessions_dir,
    )

    # Emit a log message through the Python logger
    test_logger = logging.getLogger("test_session_logger_syslog")
    test_logger.info("System log test message")

    slog.close()

    log_file = log_dir / "eigy.jsonl"
    assert log_file.exists()
    content = log_file.read_text().strip()
    assert len(content) > 0
    # Should be valid JSON
    entry = json.loads(content.split("\n")[-1])
    assert "ts" in entry
    assert "level" in entry
