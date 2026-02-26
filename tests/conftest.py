"""Shared test fixtures for Eigy tests."""

import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.database import Database
from memory.memory_manager import MemoryManager
from memory.user_profile import UserProfile


@pytest.fixture
def db(tmp_path):
    """Fresh SQLite database in a temp directory."""
    return Database(tmp_path / "test.db")


@pytest.fixture
def profile(db):
    """UserProfile backed by the test database."""
    return UserProfile(db)


@pytest.fixture
def manager(tmp_path):
    """MemoryManager with debug collector."""
    db = Database(tmp_path / "test.db")
    debug_msgs = []
    mgr = MemoryManager(db, debug_callback=debug_msgs.append)
    mgr._debug_msgs = debug_msgs
    return mgr
