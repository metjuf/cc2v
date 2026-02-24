"""Holly AI Assistant — Memory manager.

Builds optimal context windows with history, profile, and summaries.
Handles session lifecycle (save messages, end-of-session summaries).
"""

from __future__ import annotations

import json
import logging

import config
import chat_engine
from memory.database import Database
from memory.user_profile import UserProfile

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """\
Shrň tento rozhovor ve 2-3 větách v češtině. Zaměř se na:
- Hlavní probíraná témata
- Případná rozhodnutí nebo závěry
- Cokoli emočně významného
Piš stručně a věcně.\
"""

EXTRACTION_PROMPT = """\
Projdi tento rozhovor a extrahuj nové informace o uživateli.
Vrať validní JSON s těmito klíči (uveď pouze pokud je nová informace):
- "interests": seznam zájmů zmíněných v rozhovoru (řetězce)
- "preferences": seznam preferencí vyjádřených uživatelem (řetězce)
- "facts": objekt {"klíč": "popis"} pro zajímavé fakty (práce, bydliště, mazlíčci atd.)

Uveď POUZE NOVÉ informace, které ještě nejsou v existujícím profilu.
Existující profil: {current_profile}

Vrať POUZE validní JSON, žádný jiný text.\
"""


class MemoryManager:
    """Manages Holly's memory — context building, message persistence, session lifecycle."""

    def __init__(self, db: Database):
        self.db = db
        self.profile = UserProfile(db)
        self.user_name = self.profile.get_name() or "friend"
        self.session_id = db.create_conversation()

    @property
    def system_prompt(self) -> str:
        return config.SYSTEM_PROMPT_TEMPLATE.format(user_name=self.user_name)

    def build_context(self, current_messages: list[dict]) -> list[dict]:
        """Build the full messages array for the LLM with relevant history.

        Layers:
        1. System prompt (with user name)
        2. User profile summary
        3. Recent conversation summaries
        4. Tail of previous session
        5. Current session messages
        """
        context: list[dict] = []

        # 1. System prompt
        context.append({"role": "system", "content": self.system_prompt})

        # 2. User profile
        profile_summary = self.db.get_user_profile_summary()
        if profile_summary:
            context.append({
                "role": "system",
                "content": f"Co si pamatuješ o uživateli {self.user_name}: {profile_summary}",
            })

        # 3. Recent conversation summaries
        summaries = self.db.get_recent_summaries(limit=config.MEMORY_SUMMARY_COUNT)
        if summaries:
            formatted = "\n".join(
                f"- {s['date']}: {s['summary']}" for s in summaries
            )
            context.append({
                "role": "system",
                "content": f"Shrnutí nedávných konverzací:\n{formatted}",
            })

        # 4. Tail of previous session (for continuity)
        prev_messages = self.db.get_previous_session_messages(
            limit=config.MEMORY_TAIL_MESSAGES
        )
        if prev_messages:
            context.append({
                "role": "system",
                "content": "--- Minulá relace (poslední zprávy) ---",
            })
            context.extend(prev_messages)

        # 5. Current session messages
        context.extend(current_messages)

        return context

    def save_message(self, role: str, content: str, emotion: str | None = None) -> None:
        """Persist a message to the database."""
        self.db.insert_message(self.session_id, role, content, emotion)

    async def end_session(self) -> None:
        """Called on exit — generate summary, extract profile updates."""
        messages = self.db.get_session_messages(self.session_id)
        if not messages:
            self.db.end_conversation(self.session_id)
            return

        # Generate summary
        try:
            summary = await self._generate_summary(messages)
            if summary:
                self.db.update_conversation_summary(self.session_id, summary)
        except Exception as e:
            logger.warning("Failed to generate session summary: %s", e)

        # Extract profile updates
        try:
            await self._extract_profile_updates(messages)
        except Exception as e:
            logger.warning("Failed to extract profile updates: %s", e)

        self.db.end_conversation(self.session_id)

    async def _generate_summary(self, messages: list[dict]) -> str:
        """Generate a brief summary of the conversation."""
        formatted = "\n".join(
            f"{m['role'].capitalize()}: {m['content']}" for m in messages
        )
        prompt_messages = [
            {"role": "user", "content": f"{SUMMARY_PROMPT}\n\nConversation:\n{formatted}"}
        ]
        return await chat_engine.get_auxiliary_response(prompt_messages)

    async def _extract_profile_updates(self, messages: list[dict]) -> None:
        """Extract new user facts from conversation and update profile."""
        current_profile = json.dumps(self.db.get_all_profile())
        formatted = "\n".join(
            f"{m['role'].capitalize()}: {m['content']}" for m in messages
        )
        prompt = EXTRACTION_PROMPT.format(current_profile=current_profile)
        prompt_messages = [
            {"role": "user", "content": f"{prompt}\n\nConversation:\n{formatted}"}
        ]
        response = await chat_engine.get_auxiliary_response(prompt_messages)
        if not response:
            return
        try:
            # Try to extract JSON from the response
            response = response.strip()
            if response.startswith("```"):
                # Strip markdown code blocks
                lines = response.split("\n")
                response = "\n".join(lines[1:-1])
            data = json.loads(response)
            self.profile.update_from_extraction(data)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("Could not parse profile extraction: %s", e)
