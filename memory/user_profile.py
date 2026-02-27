"""Eigy AI Assistant — User profile management (v2 structured JSON).

Higher-level operations for structured user profile stored in SQLite.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Callable

from memory.database import Database

logger = logging.getLogger(__name__)


class UserProfile:
    """Manages structured user profile with deep merge updates."""

    def __init__(self, db: Database, debug_callback: Callable[[str], None] | None = None):
        self.db = db
        self._profile: dict | None = None
        self._dbg = debug_callback

    def _load(self) -> dict:
        if self._profile is None:
            self._profile = self.db.get_structured_profile()
        return self._profile

    def _save(self) -> None:
        if self._profile is not None:
            self.db.save_structured_profile(self._profile)

    def get_full_profile(self) -> dict:
        """Return a deep copy of the full structured profile."""
        return json.loads(json.dumps(self._load()))

    def get_name(self) -> str | None:
        return self._load().get("basic", {}).get("name")

    def set_name(self, name: str) -> None:
        profile = self._load()
        profile.setdefault("basic", {})["name"] = name
        # Also keep legacy KV in sync
        self.db.set_user_profile("name", name)
        self._save()

    def get_summary(self) -> str:
        """Human-readable summary of everything known about the user."""
        return self.db.get_user_profile_summary()

    @staticmethod
    def _guard_field(
        data: dict, profile: dict,
        section: str, key: str, sub_key: str | None = None,
    ) -> None:
        """Block extraction from overwriting an existing identity field.

        Prevents LLM from confusing mentioned person's info with user's info.
        For nested fields (e.g. location.city), use sub_key.
        """
        if section not in data or not isinstance(data[section], dict):
            return
        existing_section = profile.get(section, {})
        if not isinstance(existing_section, dict):
            return

        if sub_key:
            # Nested: e.g. basic.location.city
            existing_val = existing_section.get(key, {})
            if isinstance(existing_val, dict) and existing_val.get(sub_key):
                extracted = data[section].get(key, {})
                if isinstance(extracted, dict) and extracted.get(sub_key):
                    if extracted[sub_key] != existing_val[sub_key]:
                        logger.info(
                            "Blocked %s.%s.%s overwrite: '%s' → '%s'",
                            section, key, sub_key,
                            existing_val[sub_key], extracted[sub_key],
                        )
                        del extracted[sub_key]
                        if not extracted:
                            del data[section][key]
        else:
            # Simple: e.g. basic.name, life.occupation
            existing_val = existing_section.get(key)
            if existing_val:
                extracted_val = data[section].get(key)
                if extracted_val and extracted_val != existing_val:
                    logger.info(
                        "Blocked %s.%s overwrite: '%s' → '%s'",
                        section, key, existing_val, extracted_val,
                    )
                    del data[section][key]

        # Clean up empty section
        if section in data and not data[section]:
            del data[section]

    def update_from_extraction(self, data: dict) -> None:
        """Apply auto-extracted profile updates from LLM.

        Supports both legacy flat format and new structured format.
        Legacy: {"name": "...", "interests": [...], "preferences": [...], "facts": {...}}
        Structured: {"basic": {...}, "personality": {...}, "life": {...}, ...}
        """
        profile = self._load()

        # Protect key identity fields from being overwritten by extraction
        # (LLM sometimes confuses mentioned person's info with user's info)
        self._guard_field(data, profile, "basic", "name")
        self._guard_field(data, profile, "life", "occupation")
        self._guard_field(data, profile, "basic", "location", sub_key="city")

        # Detect format: if top-level keys match structured categories, use deep merge
        structured_keys = {"basic", "personality", "life", "interests", "preferences",
                           "goals", "health", "context", "eigy_observations", "people"}
        if any(k in data for k in structured_keys):
            changelog = profile.get("_changelog", [])
            old_len = len(changelog)
            _deep_merge(profile, data, changelog=changelog)
            if changelog:
                profile["_changelog"] = changelog[-20:]
                # Debug new changelog entries
                if self._dbg and len(changelog) > old_len:
                    for entry in changelog[old_len:]:
                        self._dbg(
                            f"Profil: {entry['field']} "
                            f"'{entry['old']}' → '{entry['new']}'"
                        )
        else:
            # Legacy flat format
            if "name" in data and data["name"]:
                profile.setdefault("basic", {})["name"] = data["name"]
                self.db.set_user_profile("name", data["name"])

            for interest in data.get("interests", []):
                if isinstance(interest, str) and interest.strip():
                    topics = profile.setdefault("interests", {}).setdefault("topics", [])
                    if interest not in topics:
                        topics.append(interest)

            for pref in data.get("preferences", []):
                if isinstance(pref, str) and pref.strip():
                    other = profile.setdefault("preferences", {}).setdefault("other", {})
                    key = pref.lower().replace(" ", "_")[:40]
                    other[key] = pref

            for fact_key, fact_val in data.get("facts", {}).items():
                if isinstance(fact_val, str) and fact_val.strip():
                    misc = profile.setdefault("context", {}).setdefault("misc_facts", {})
                    misc[fact_key] = fact_val

        self._save()


def _deep_merge(
    target: dict,
    source: dict,
    changelog: list | None = None,
    _path: str = "",
) -> None:
    """Recursively merge source into target.

    - Lists: extend with deduplication
    - Dicts: recursive merge
    - Scalars: overwrite (only if source value is not None/empty)
    - If changelog is provided, scalar overwrites are recorded.
    """
    for key, value in source.items():
        if key in ("version", "_changelog"):
            continue  # don't overwrite schema version or changelog

        dotted = f"{_path}.{key}" if _path else key

        if key not in target:
            target[key] = value
            continue

        existing = target[key]

        if isinstance(existing, dict) and isinstance(value, dict):
            _deep_merge(existing, value, changelog=changelog, _path=dotted)
        elif isinstance(existing, list) and isinstance(value, list):
            for item in value:
                if item and item not in existing:
                    existing.append(item)
        elif value is not None and value != "":
            if (changelog is not None
                    and existing != value
                    and existing is not None
                    and existing != ""):
                changelog.append({
                    "field": dotted,
                    "old": existing,
                    "new": value,
                    "date": date.today().isoformat(),
                })
                logger.info(
                    "Profile updated: %s changed from '%s' to '%s'",
                    dotted, existing, value,
                )
            target[key] = value
