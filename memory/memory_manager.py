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
        """Build the full messages array for the LLM (legacy single-assistant)."""
        return self.build_context_for("eigy", current_messages)

    def build_context_for(
        self,
        assistant_id: str,
        current_messages: list[dict],
        discussion_mode: bool = False,
    ) -> list[dict]:
        """Build the full messages array for a specific assistant.

        Layers:
        1. System prompt (per-assistant, with dual awareness)
        2. User profile summary
        3. Assistant profile summary
        4. Recent conversation summaries
        5. Tail of previous session (role-remapped)
        6. Current session messages (role-remapped)

        Role remapping (Anthropic API requires strict user/assistant alternation):
        - For Eigy: eigy messages → assistant, delan messages → user with [Delan]: prefix
        - For Delan: delan messages → assistant, eigy messages → user with [Eigy]: prefix
        - User messages → user with [{user_name}]: prefix
        - Consecutive same-role messages are merged
        """
        context: list[dict] = []

        # 1. System prompt (per-assistant with dual awareness)
        system_prompt = config.get_system_prompt(assistant_id, self.user_name, discussion_mode)
        context.append({"role": "system", "content": system_prompt})

        # 2. User profile
        profile_summary = self.db.get_user_profile_summary()
        if profile_summary:
            context.append({
                "role": "system",
                "content": f"Co si pamatuješ o uživateli {self.user_name}: {profile_summary}",
            })

        # 3. Assistant profile
        assistant_profile = self.db.get_assistant_profile_summary(assistant_id)
        if assistant_profile:
            context.append({
                "role": "system",
                "content": f"Tvé osobní poznámky: {assistant_profile}",
            })

        # Other assistant's profile
        other_id = "delan" if assistant_id == "eigy" else "eigy"
        other_name = config.ASSISTANTS[other_id]["name"]
        other_profile = self.db.get_assistant_profile_summary(other_id)
        if other_profile:
            context.append({
                "role": "system",
                "content": f"Poznámky od {other_name}: {other_profile}",
            })

        # 4. Recent conversation summaries
        summaries = self.db.get_recent_summaries(limit=config.MEMORY_SUMMARY_COUNT)
        if summaries:
            formatted = "\n".join(
                f"- {s['date']}: {s['summary']}" for s in summaries
            )
            context.append({
                "role": "system",
                "content": f"Shrnutí nedávných konverzací:\n{formatted}",
            })

        # 5. Tail of previous session (role-remapped)
        prev_messages = self.db.get_previous_session_messages(
            limit=config.MEMORY_TAIL_MESSAGES
        )
        if prev_messages:
            context.append({
                "role": "system",
                "content": "--- Minulá relace (poslední zprávy) ---",
            })
            remapped_prev = self._remap_roles(assistant_id, prev_messages)
            context.extend(remapped_prev)

        # 6. Current session messages (role-remapped)
        remapped = self._remap_roles(assistant_id, current_messages)
        context.extend(remapped)

        return context

    def _remap_roles(self, assistant_id: str, messages: list[dict]) -> list[dict]:
        """Remap message roles for a specific assistant's perspective.

        Anthropic API requires strict user/assistant alternation.
        """
        other_id = "delan" if assistant_id == "eigy" else "eigy"
        other_name = config.ASSISTANTS[other_id]["name"]

        remapped: list[dict] = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            msg_assistant = msg.get("assistant_id")

            if role == "system":
                remapped.append(msg)
                continue

            if role == "assistant":
                if msg_assistant == assistant_id or msg_assistant is None:
                    # This assistant's own message → assistant role
                    remapped.append({"role": "assistant", "content": content})
                else:
                    # Other assistant's message → user role with name prefix
                    remapped.append({"role": "user", "content": f"[{other_name}]: {content}"})
            elif role == "user":
                remapped.append({"role": "user", "content": f"[{self.user_name}]: {content}"})
            else:
                remapped.append(msg)

        # Merge consecutive same-role messages (required by Anthropic API)
        return self._merge_consecutive(remapped)

    @staticmethod
    def _merge_consecutive(messages: list[dict]) -> list[dict]:
        """Merge consecutive messages with the same role."""
        if not messages:
            return []
        merged: list[dict] = [messages[0].copy()]
        for msg in messages[1:]:
            if msg["role"] == merged[-1]["role"] and msg["role"] != "system":
                merged[-1]["content"] += "\n" + msg["content"]
            else:
                merged.append(msg.copy())
        return merged

    def save_message(
        self, role: str, content: str, emotion: str | None = None, assistant_id: str | None = None
    ) -> None:
        """Persist a message to the database."""
        self.db.insert_message(self.session_id, role, content, emotion, assistant_id)

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
