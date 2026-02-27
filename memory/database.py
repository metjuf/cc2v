"""Eigy AI Assistant — SQLite database layer.

Schema creation and all CRUD operations for conversations,
messages, and user profile (v2 structured JSON).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


def _now() -> str:
    """Return current timestamp as ISO string (avoids Python 3.12+ sqlite3 warning)."""
    return datetime.now().isoformat()


_DEFAULT_PROFILE = {
    "version": 2,
    "basic": {
        "name": None, "nickname": None, "age": None, "gender": None,
        "location": {"city": None, "country": None},
        "languages": ["cs"],
    },
    "personality": {
        "traits": [], "communication_style": None, "humor_type": None,
        "formality_preference": "mixed", "response_length_preference": "mixed",
        "emotional_baseline": None,
    },
    "life": {
        "occupation": None, "company": None, "education": None,
        "relationship_status": None,
        "family": {"partner": None, "children": [], "pets": [], "other": []},
    },
    "interests": {
        "hobbies": [], "topics": [], "music": [], "media": [],
        "sports": [], "technology": [], "other": [],
    },
    "preferences": {
        "food": [], "travel": [], "daily_routines": [],
        "dislikes": [], "other": {},
    },
    "goals": {
        "short_term": [], "long_term": [], "current_projects": [],
    },
    "health": {
        "conditions": [], "fitness": None, "diet": None,
    },
    "context": {
        "recent_topics": [], "ongoing_conversations": [],
        "important_dates": {}, "misc_facts": {},
    },
    "eigy_observations": {
        "behavioral_patterns": [],
        "communication_notes": [],
        "personal_insights": [],
        "relationship_notes": [],
    },
    "people": {},
}


class Database:
    """SQLite database for Eigy's persistent memory."""

    SCHEMA_VERSION = 3

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        self._migrate()

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

            CREATE TABLE IF NOT EXISTS user_profile_v2 (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                profile_json TEXT NOT NULL DEFAULT '{}',
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

            CREATE TABLE IF NOT EXISTS profile_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_json TEXT NOT NULL,
                created_at TIMESTAMP
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

    def _migrate(self) -> None:
        """Run migrations if needed."""
        row = self.conn.execute("SELECT version FROM schema_version").fetchone()
        current_version = row["version"] if row else 1

        if current_version < 2:
            self._migrate_v1_to_v2()
        if current_version < 3:
            self._migrate_v2_to_v3()

        if current_version < self.SCHEMA_VERSION:
            self.conn.execute(
                "UPDATE schema_version SET version = ?", (self.SCHEMA_VERSION,)
            )
            self.conn.commit()

    def _migrate_v1_to_v2(self) -> None:
        """Migrate flat KV profile to structured JSON."""
        old_profile = self.get_all_profile()
        if not old_profile:
            return

        profile = self._default_profile()

        # Migrate name
        if "name" in old_profile:
            profile["basic"]["name"] = old_profile["name"]

        # Migrate interests
        for key, value in old_profile.items():
            if key.startswith("interest:"):
                if value not in profile["interests"]["topics"]:
                    profile["interests"]["topics"].append(value)

        # Migrate preferences
        for key, value in old_profile.items():
            if key.startswith("preference:"):
                category = key.split(":", 1)[1] if ":" in key else "other"
                if isinstance(profile["preferences"].get(category), list):
                    if value not in profile["preferences"][category]:
                        profile["preferences"][category].append(value)
                elif isinstance(profile["preferences"].get("other"), dict):
                    profile["preferences"]["other"][category] = value
                else:
                    if value not in profile["preferences"].get("dislikes", []):
                        profile["preferences"].setdefault("other", {})[category] = value

        # Migrate facts
        for key, value in old_profile.items():
            if key.startswith("fact:"):
                fact_key = key.split(":", 1)[1]
                profile["context"]["misc_facts"][fact_key] = value

        self.save_structured_profile(profile)

    def _migrate_v2_to_v3(self) -> None:
        """Add profile_snapshots table."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS profile_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_json TEXT NOT NULL,
                created_at TIMESTAMP
            )
        """)
        self.conn.commit()

    @staticmethod
    def _default_profile() -> dict:
        """Return a deep copy of the default empty profile."""
        return json.loads(json.dumps(_DEFAULT_PROFILE))

    # ── Structured Profile (v2) ──────────────────────────────────────

    def get_structured_profile(self) -> dict:
        """Get the full structured profile (v2). Returns default if empty."""
        row = self.conn.execute(
            "SELECT profile_json FROM user_profile_v2 WHERE id = 1"
        ).fetchone()
        if row and row["profile_json"]:
            try:
                return json.loads(row["profile_json"])
            except json.JSONDecodeError:
                pass
        return self._default_profile()

    def save_structured_profile(self, profile: dict) -> None:
        """Save the full structured profile (v2)."""
        profile_json = json.dumps(profile, ensure_ascii=False)
        self.conn.execute(
            """INSERT INTO user_profile_v2 (id, profile_json, updated_at)
               VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET profile_json = ?, updated_at = ?""",
            (profile_json, _now(), profile_json, _now()),
        )
        self.conn.commit()

    def save_profile_snapshot(self, profile_json: str) -> None:
        """Save a snapshot of the profile before condensation."""
        self.conn.execute(
            "INSERT INTO profile_snapshots (profile_json, created_at) VALUES (?, ?)",
            (profile_json, _now()),
        )
        self.conn.commit()

    def cleanup_old_snapshots(self, keep: int = 5) -> None:
        """Keep only the N most recent profile snapshots."""
        self.conn.execute(
            """DELETE FROM profile_snapshots WHERE id NOT IN (
                SELECT id FROM profile_snapshots ORDER BY id DESC LIMIT ?
            )""",
            (keep,),
        )
        self.conn.commit()

    # ── User Profile (legacy KV — still used for is_first_run) ────────

    def is_first_run(self) -> bool:
        """Check if this is the very first launch (no profile exists)."""
        # Check v2 first
        row = self.conn.execute(
            "SELECT profile_json FROM user_profile_v2 WHERE id = 1"
        ).fetchone()
        if row:
            try:
                p = json.loads(row["profile_json"])
                if p.get("basic", {}).get("name"):
                    return False
            except json.JSONDecodeError:
                pass
        # Fallback to v1
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
            (key, value, _now(), value, _now()),
        )
        self.conn.commit()

    def get_all_profile(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT key, value FROM user_profile").fetchall()
        return {row["key"]: row["value"] for row in rows}

    @staticmethod
    def _safe_list(value) -> list[str]:
        """Ensure value is a list of strings (LLM may return bools/ints)."""
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item is not None and item is not False]

    def get_user_profile_summary(self) -> str:
        """Build a human-readable summary from structured profile."""
        profile = self.get_structured_profile()
        profile.pop("_changelog", None)  # internal field, not for display
        parts = []

        basic = profile.get("basic", {})
        if not isinstance(basic, dict):
            basic = {}
        if basic.get("name"):
            parts.append(f"Jméno: {basic['name']}")
        if basic.get("age"):
            parts.append(f"Věk: {basic['age']}")
        if basic.get("gender"):
            parts.append(f"Pohlaví: {basic['gender']}")
        loc = basic.get("location", {})
        if isinstance(loc, dict):
            loc_parts = [str(v) for v in [loc.get("city"), loc.get("country")] if v]
            if loc_parts:
                parts.append(f"Lokace: {', '.join(loc_parts)}")

        life = profile.get("life", {})
        if not isinstance(life, dict):
            life = {}
        if life.get("occupation"):
            parts.append(f"Práce: {life['occupation']}")
        if life.get("education"):
            parts.append(f"Vzdělání: {life['education']}")
        if life.get("relationship_status"):
            parts.append(f"Vztah: {life['relationship_status']}")
        fam = life.get("family", {})
        if isinstance(fam, dict):
            if fam.get("partner"):
                parts.append(f"Partner: {fam['partner']}")
            children = self._safe_list(fam.get("children"))
            if children:
                parts.append(f"Děti: {', '.join(children)}")
            pets = self._safe_list(fam.get("pets"))
            if pets:
                parts.append(f"Mazlíčci: {', '.join(pets)}")

        people = profile.get("people", {})
        if isinstance(people, dict) and people:
            people_parts = []
            for name, info in list(people.items())[:15]:
                if not isinstance(info, dict):
                    continue
                relation = info.get("relation", "")
                notes = self._safe_list(info.get("notes"))
                location = info.get("location", "")
                desc = str(name)
                if relation:
                    desc += f" ({relation})"
                details = []
                if location:
                    details.append(str(location))
                details.extend(notes[:3])
                if details:
                    desc += f" — {'; '.join(details)}"
                people_parts.append(desc)
            if people_parts:
                parts.append(f"Lidé: {' | '.join(people_parts)}")

        personality = profile.get("personality", {})
        if isinstance(personality, dict):
            traits = self._safe_list(personality.get("traits"))
            if traits:
                parts.append(f"Povaha: {', '.join(traits)}")

        interests = profile.get("interests", {})
        if isinstance(interests, dict):
            all_interests = []
            for cat in ("hobbies", "topics", "music", "media", "sports", "technology", "other"):
                all_interests.extend(self._safe_list(interests.get(cat)))
            if all_interests:
                parts.append(f"Zájmy: {', '.join(all_interests[:15])}")

        prefs = profile.get("preferences", {})
        if isinstance(prefs, dict):
            pref_items = []
            for cat in ("food", "travel", "daily_routines"):
                pref_items.extend(self._safe_list(prefs.get(cat)))
            dislikes = self._safe_list(prefs.get("dislikes"))
            if dislikes:
                pref_items.append(f"nemá rád: {', '.join(dislikes)}")
            if isinstance(prefs.get("other"), dict):
                for k, v in prefs["other"].items():
                    pref_items.append(f"{k}: {v}" if isinstance(v, str) else str(v))
            if pref_items:
                parts.append(f"Preference: {', '.join(pref_items[:10])}")

        goals = profile.get("goals", {})
        if isinstance(goals, dict):
            goal_items = self._safe_list(goals.get("short_term")) + self._safe_list(goals.get("long_term"))
            if goal_items:
                parts.append(f"Cíle: {', '.join(goal_items[:5])}")
            projects = self._safe_list(goals.get("current_projects"))
            if projects:
                parts.append(f"Projekty: {', '.join(projects[:5])}")

        health = profile.get("health", {})
        if isinstance(health, dict) and health.get("diet"):
            parts.append(f"Dieta: {health['diet']}")

        ctx = profile.get("context", {})
        if isinstance(ctx, dict) and isinstance(ctx.get("misc_facts"), dict):
            facts = [f"{k}: {v}" for k, v in list(ctx["misc_facts"].items())[:10]]
            parts.append(f"Fakta: {'; '.join(facts)}")

        observations = profile.get("eigy_observations", {})
        if isinstance(observations, dict):
            obs_items = []
            for cat in ("behavioral_patterns", "communication_notes",
                         "personal_insights", "relationship_notes"):
                obs_items.extend(self._safe_list(observations.get(cat)))
            if obs_items:
                parts.append(f"Postřehy: {'; '.join(obs_items[:8])}")

        return "; ".join(parts) if parts else ""

    # ── Conversations ──────────────────────────────────────────────

    def create_conversation(self) -> int:
        cursor = self.conn.execute(
            "INSERT INTO conversations (started_at) VALUES (?)",
            (_now(),),
        )
        self.conn.commit()
        return cursor.lastrowid

    def end_conversation(self, conv_id: int) -> None:
        self.conn.execute(
            "UPDATE conversations SET ended_at = ? WHERE id = ?",
            (_now(), conv_id),
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
            (conv_id, role, content, _now(), emotion),
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
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    # ── Maintenance ────────────────────────────────────────────────

    def clear_all(self) -> None:
        """Clear all data (for /forget command)."""
        self.conn.executescript("""
            DELETE FROM messages;
            DELETE FROM conversations;
            DELETE FROM user_profile;
            DELETE FROM user_profile_v2;
            DELETE FROM profile_snapshots;
        """)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
