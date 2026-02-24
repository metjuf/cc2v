"""Eigy AI Assistant — Memory manager.

Builds optimal context windows with history, profile, and summaries.
Handles session lifecycle (save messages, end-of-session summaries).
Supports real-time fact extraction during conversation.
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

REALTIME_EXTRACTION_PROMPT = """\
Z této výměny zpráv extrahuj NOVÉ osobní informace o uživateli.
Zaměř se na: zájmy, rodinu, práci, co vlastní, kde bydlí, preference, návyky.

Existující profil: {current_profile}

Uživatel: {user_msg}
Asistent: {assistant_msg}

Vrať POUZE validní JSON (žádný jiný text):
{{"interests": [...], "preferences": [...], "facts": {{"klíč": "hodnota"}}}}
Pokud nic nového, vrať: {{}}\
"""


class MemoryManager:
    """Manages memory — context building, message persistence, session lifecycle."""

    def __init__(self, db: Database):
        self.db = db
        self.profile = UserProfile(db)
        self.user_name = self.profile.get_name() or "friend"
        self.session_id = db.create_conversation()

    @property
    def system_prompt(self) -> str:
        return config.SYSTEM_PROMPT_TEMPLATE.format(
            user_name=self.user_name,
            assistant_name=config.ASSISTANT_NAME,
        )

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

    async def extract_facts_realtime(self, user_msg: str, assistant_msg: str) -> None:
        """Extract facts from a single exchange in real-time (background task).

        Only runs if user message is substantial enough (>20 chars).
        Uses the auxiliary model for cheap, fast extraction.
        """
        if len(user_msg.strip()) < 20:
            return

        try:
            current_profile = json.dumps(self.db.get_all_profile(), ensure_ascii=False)
            prompt = REALTIME_EXTRACTION_PROMPT.format(
                current_profile=current_profile,
                user_msg=user_msg,
                assistant_msg=assistant_msg[:500],  # truncate for cost
            )
            prompt_messages = [{"role": "user", "content": prompt}]
            response = await chat_engine.get_auxiliary_response(prompt_messages)
            if not response:
                return

            response = response.strip()
            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join(lines[1:-1])

            data = json.loads(response)

            # Only update if there's actual data
            has_data = (
                data.get("interests")
                or data.get("preferences")
                or data.get("facts")
            )
            if has_data:
                self.profile.update_from_extraction(data)
                logger.info("Real-time extraction found new facts: %s", list(data.keys()))

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("Real-time extraction parse error: %s", e)
        except Exception as e:
            logger.debug("Real-time extraction failed: %s", e)

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
