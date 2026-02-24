"""Holly AI Assistant — SQLite database layer.

Schema creation and all CRUD operations for conversations,
messages, and user profile.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


class Database:
    """SQLite database for Holly's persistent memory."""

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        cursor = self.conn.cursor()
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS user_profile (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP,
                summary TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER REFERENCES conversations(id),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                emotion TEXT
            );
        """)
        # Set schema version if not present
        existing = cursor.execute("SELECT version FROM schema_version").fetchone()
        if not existing:
            cursor.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (self.SCHEMA_VERSION,),
            )
        self.conn.commit()

    # ── User Profile ───────────────────────────────────────────────

    def is_first_run(self) -> bool:
        """Check if this is the very first launch (no user profile exists)."""
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM user_profile"
        ).fetchone()
        return row["cnt"] == 0

    def get_user_profile(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM user_profile WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_user_profile(self, key: str, value: str) -> None:
        self.conn.execute(
            """INSERT INTO user_profile (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?""",
            (key, value, datetime.now(), value, datetime.now()),
        )
        self.conn.commit()

    def get_all_profile(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT key, value FROM user_profile").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def get_user_profile_summary(self) -> str:
        """Build a human-readable summary of the user profile."""
        profile = self.get_all_profile()
        if not profile:
            return ""
        parts = []
        for key, value in profile.items():
            if key == "name":
                parts.append(f"Name: {value}")
            elif key.startswith("interest:"):
                parts.append(f"Interest: {value}")
            elif key.startswith("preference:"):
                parts.append(f"Preference: {value}")
            elif key.startswith("fact:"):
                parts.append(f"Fact: {value}")
            else:
                parts.append(f"{key}: {value}")
        return "; ".join(parts)

    # ── Conversations ──────────────────────────────────────────────

    def create_conversation(self) -> int:
        cursor = self.conn.execute(
            "INSERT INTO conversations (started_at) VALUES (?)",
            (datetime.now(),),
        )
        self.conn.commit()
        return cursor.lastrowid

    def end_conversation(self, conv_id: int) -> None:
        self.conn.execute(
            "UPDATE conversations SET ended_at = ? WHERE id = ?",
            (datetime.now(), conv_id),
        )
        self.conn.commit()

    def update_conversation_summary(self, conv_id: int, summary: str) -> None:
        self.conn.execute(
            "UPDATE conversations SET summary = ? WHERE id = ?",
            (summary, conv_id),
        )
        self.conn.commit()

    # ── Messages ───────────────────────────────────────────────────

    def insert_message(
        self, conv_id: int, role: str, content: str, emotion: str | None = None
    ) -> None:
        self.conn.execute(
            """INSERT INTO messages (conversation_id, role, content, timestamp, emotion)
               VALUES (?, ?, ?, ?, ?)""",
            (conv_id, role, content, datetime.now(), emotion),
        )
        self.conn.commit()

    def get_session_messages(self, conv_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
            (conv_id,),
        ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def get_recent_summaries(self, limit: int = 5) -> list[dict]:
        """Get recent conversation summaries (excluding current, most recent first)."""
        rows = self.conn.execute(
            """SELECT id, started_at, summary FROM conversations
               WHERE summary IS NOT NULL
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "date": row["started_at"],
                "summary": row["summary"],
            }
            for row in rows
        ]

    def get_previous_session_messages(self, limit: int = 20) -> list[dict]:
        """Get the tail of the most recent completed conversation."""
        # Find the most recent conversation that has an ended_at timestamp
        conv = self.conn.execute(
            """SELECT id FROM conversations
               WHERE ended_at IS NOT NULL
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        if not conv:
            return []
        rows = self.conn.execute(
            """SELECT role, content FROM messages
               WHERE conversation_id = ? AND role IN ('user', 'assistant')
               ORDER BY id DESC LIMIT ?""",
            (conv["id"], limit),
        ).fetchall()
        # Reverse to chronological order
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    # ── Maintenance ────────────────────────────────────────────────

    def clear_all(self) -> None:
        """Clear all data (for /forget command)."""
        self.conn.executescript("""
            DELETE FROM messages;
            DELETE FROM conversations;
            DELETE FROM user_profile;
        """)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
