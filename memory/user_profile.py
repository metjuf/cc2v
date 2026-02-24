"""Eigy AI Assistant — User profile management.

Higher-level operations for user profile data stored in SQLite.
"""

from __future__ import annotations

from memory.database import Database


class UserProfile:
    """Manages user profile data with typed access."""

    def __init__(self, db: Database):
        self.db = db

    def get_name(self) -> str | None:
        return self.db.get_user_profile("name")

    def set_name(self, name: str) -> None:
        self.db.set_user_profile("name", name)

    def get_interests(self) -> list[str]:
        profile = self.db.get_all_profile()
        return [v for k, v in profile.items() if k.startswith("interest:")]

    def add_interest(self, topic: str, description: str) -> None:
        self.db.set_user_profile(f"interest:{topic}", description)

    def get_preferences(self) -> list[str]:
        profile = self.db.get_all_profile()
        return [v for k, v in profile.items() if k.startswith("preference:")]

    def get_facts(self) -> list[str]:
        profile = self.db.get_all_profile()
        return [v for k, v in profile.items() if k.startswith("fact:")]

    def get_summary(self) -> str:
        """Human-readable summary of everything known about the user."""
        return self.db.get_user_profile_summary()

    def update_from_extraction(self, data: dict) -> None:
        """Apply auto-extracted profile updates from LLM.

        Expected format: {"name": "...", "interests": [...], "preferences": [...],
                          "facts": {"key": "value", ...}}
        """
        if "name" in data and data["name"]:
            self.set_name(data["name"])
        for interest in data.get("interests", []):
            if isinstance(interest, str) and interest.strip():
                key = interest.lower().replace(" ", "_")
                self.add_interest(key, interest)
        for pref in data.get("preferences", []):
            if isinstance(pref, str) and pref.strip():
                key = pref.lower().replace(" ", "_")
                self.db.set_user_profile(f"preference:{key}", pref)
        for fact_key, fact_val in data.get("facts", {}).items():
            if isinstance(fact_val, str) and fact_val.strip():
                self.db.set_user_profile(f"fact:{fact_key}", fact_val)
