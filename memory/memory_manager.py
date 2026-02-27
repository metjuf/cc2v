"""Eigy AI Assistant — Memory manager.

Builds optimal context windows with history, profile, and summaries.
Handles session lifecycle (save messages, end-of-session summaries).
Supports real-time fact extraction during conversation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Callable

import config
import chat_engine
from memory.database import Database
from memory.user_profile import UserProfile

# Episodic memory (optional dependency)
try:
    from memory.episodic import EpisodicMemory, is_available as episodic_available
except ImportError:
    EpisodicMemory = None  # type: ignore[assignment,misc]

    def episodic_available() -> bool:  # type: ignore[misc]
        return False

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
Vrať validní JSON se strukturovanými kategoriemi (uveď pouze pokud je nová informace):

{{
  "basic": {{"name": null, "age": null, "gender": null, "location": {{"city": null, "country": null}}}},
  "personality": {{"traits": [], "communication_style": null, "humor_type": null}},
  "life": {{"occupation": null, "company": null, "education": null, "relationship_status": null, "family": {{"partner": null, "children": [], "pets": []}}}},
  "interests": {{"hobbies": [], "topics": [], "music": [], "media": [], "sports": [], "technology": []}},
  "preferences": {{"food": [], "travel": [], "dislikes": [], "other": {{}}}},
  "goals": {{"short_term": [], "long_term": [], "current_projects": []}},
  "health": {{"conditions": [], "fitness": null, "diet": null}},
  "context": {{"misc_facts": {{}}}},
  "eigy_observations": {{
    "behavioral_patterns": [],
    "communication_notes": [],
    "personal_insights": [],
    "relationship_notes": []
  }},
  "people": {{
    "Jméno osoby": {{
      "relation": "vztah k uživateli (strýc, teta, kamarád, kolega, soused...)",
      "notes": ["důležité informace o této osobě"],
      "location": "kde bydlí nebo pracuje (volitelné)"
    }}
  }}
}}

Pravidla:
- Uveď POUZE klíče, které obsahují nové informace. Ostatní klíče vynech.
- Nezahrnuj informace, které jsou již v existujícím profilu.
- Pole plň jen pokud je nová hodnota. Nevracej prázdné seznamy ani null hodnoty.
- DŮLEŽITÉ: basic.name je jméno UŽIVATELE, nikoli osoby, o které mluví. Pokud uživatel mluví o jiné osobě (strýc Robert, kamarád Jan...), NEZAPISUJ její jméno do basic.name. Jméno uživatele měň POUZE pokud uživatel výslovně řekne "jmenuju se..." nebo "jsem...".
- Informace o jiných osobách (rodina, kamarádi, kolegové) VŽDY zapiš do sekce "people", NIKDY do basic/life.
- misc_facts použij pro cokoli, co nezapadá do jiné kategorie.
- eigy_observations: jen opravdu zajímavé postřehy o vzorcích chování a komunikace uživatele.
- people: zaznamenej KAŽDOU osobu, o které uživatel mluví — rodinu, kamarády, kolegy, známé. Klíč = křestní jméno osoby. Uveď relation (strýc, teta, kamarád, kolega...), notes (zajímavé info o osobě — vlastnosti, zvyky, problémy, přezdívky, vztahy k dalším lidem), location (volitelné). Pokud uživatel zmíní kamaráda někoho jiného, zapiš to do notes té osoby NEBO vytvoř nový záznam s relation "kamarád strýce Roberta" apod.

Existující profil: {current_profile}

Vrať POUZE validní JSON, žádný jiný text.\
"""

REALTIME_EXTRACTION_PROMPT = """\
Z této výměny zpráv extrahuj NOVÉ osobní informace o uživateli.
Zaměř se na: jméno, věk, zájmy, rodinu, práci, vzdělání, kde bydlí, preference, návyky, cíle, LIDI z okolí uživatele (rodina, kamarádi, kolegové, známí).
Navíc si všímej vzorců chování a komunikace uživatele.

Existující profil: {current_profile}

Uživatel: {user_msg}
Asistent: {assistant_msg}

Vrať POUZE validní JSON se strukturovanými kategoriemi (jen ty, kde máš novou informaci):
{{
  "basic": {{"name": null, "location": {{"city": null}}}},
  "life": {{"occupation": null, "family": {{"pets": []}}}},
  "interests": {{"hobbies": [], "topics": []}},
  "preferences": {{"food": [], "other": {{}}}},
  "context": {{"misc_facts": {{}}}},
  "eigy_observations": {{
    "behavioral_patterns": [],
    "communication_notes": [],
    "personal_insights": []
  }},
  "people": {{
    "Jméno": {{"relation": "vztah k uživateli", "notes": ["důležité info"]}}
  }}
}}

DŮLEŽITÉ: basic.name je jméno UŽIVATELE. Pokud uživatel mluví o jiné osobě (strýc, kamarád, kolega...), NEZAPISUJ její jméno do basic.name — zapiš ji do "people". Jméno uživatele měň jen pokud výslovně řekne "jmenuju se..." nebo "jsem...".

Pravidla pro eigy_observations:
- Zapiš jen opravdu zajímavé postřehy, ne triviální věci
- Formuluj jako: "uživatel má tendenci...", "reaguje dobře na...", "zdá se, že..."
- Maximálně 1-2 postřehy za výměnu

Pravidla pro people:
- Zaznamenej každou osobu, o které uživatel mluví — rodinu, kamarády, kolegy, známé
- Klíč = křestní jméno, relation = vztah k uživateli, notes = důležité info
- Informace o jiných lidech (práce, bydliště, vlastnosti) PATŘÍ do people, NE do basic/life

Pokud nic nového, vrať: {{}}\
"""

MID_SESSION_SUMMARY_PROMPT = """\
Shrň tuto část konverzace ve 3-5 větách v češtině. Zachovej:
- Klíčová témata a rozhodnutí
- Důležité fakty zmíněné uživatelem
- Kontext potřebný pro pokračování konverzace
Piš stručně, ale zachovej všechny důležité informace.\
"""

PROFILE_CONDENSATION_PROMPT = """\
Zkondenzuj tento profil uživatele. Odstraň duplicity, zastaralé záznamy a irelevantní detaily.
Zachovej všechny důležité fakty. Sluč podobné položky.
Klíč "_changelog" zachovej beze změny.

Aktuální profil:
{profile_json}

Vrať POUZE validní JSON se STEJNOU strukturou jako vstup. Žádný jiný text.\
"""

PROFILE_CORRECTION_PROMPT = """\
Uprav profil uživatele podle následující instrukce.

Instrukce: {instruction}

Aktuální profil:
{profile_json}

Vrať POUZE opravený validní JSON se STEJNOU strukturou. Žádný jiný text.\
"""

PRE_REASONING_PROMPT = """\
Analyzuj tuto situaci před odpovědí uživateli. Vrať stručnou analýzu (3-5 bodů):

1. NÁLADA: Jak se uživatel pravděpodobně cítí?
2. PAMĚŤ: Co relevantního víš o uživateli, co bys měla zmínit nebo na co navázat?
3. CO NEŘÍKAT: Je něco, čemu by ses měla vyhnout? (opakování, citlivá témata)
4. PŘÍSTUP: Jaký tón a styl odpovědi zvolit?
5. KONTEXT: Je něco časově relevantního? (denní doba, víkend, nedávné události)

Profil uživatele: {profile_summary}
Poslední zprávy: {recent_messages}

Odpověz ČESKY, stručně, ve formátu odrážek.\
"""

# Czech day and month names for temporal awareness
_DAY_NAMES_CS = [
    "pondělí", "úterý", "středa", "čtvrtek",
    "pátek", "sobota", "neděle",
]
_MONTH_NAMES_CS = [
    "", "ledna", "února", "března", "dubna", "května", "června",
    "července", "srpna", "září", "října", "listopadu", "prosince",
]
_CZECH_HOLIDAYS = {
    (1, 1): "Nový rok",
    (5, 1): "Svátek práce",
    (5, 8): "Den vítězství",
    (7, 5): "Den slovanských věrozvěstů Cyrila a Metoděje",
    (7, 6): "Den upálení mistra Jana Husa",
    (9, 28): "Den české státnosti",
    (10, 28): "Den vzniku samostatného československého státu",
    (11, 17): "Den boje za svobodu a demokracii",
    (12, 24): "Štědrý den",
    (12, 25): "1. svátek vánoční",
    (12, 26): "2. svátek vánoční",
}


class MemoryManager:
    """Manages memory — context building, message persistence, session lifecycle."""

    def __init__(
        self,
        db: Database,
        episodic: EpisodicMemory | None = None,
        debug_callback: Callable[[str], None] | None = None,
    ):
        self.db = db
        self._dbg = debug_callback
        self.profile = UserProfile(db, debug_callback=debug_callback)
        self.user_name = self.profile.get_name() or "friend"
        self.session_id = db.create_conversation()
        self.mid_session_summaries: list[str] = []
        self._summarizing = False
        self.episodic = episodic
        # Extraction retry queue
        self._extraction_queue: asyncio.Queue = asyncio.Queue()
        self._extraction_worker_task: asyncio.Task | None = None

    def _debug(self, msg: str) -> None:
        """Send debug message if callback is set."""
        if self._dbg:
            self._dbg(msg)

    # ── Human-Like Helpers ─────────────────────────────────────────

    @staticmethod
    def _build_temporal_block() -> str:
        """Build temporal awareness XML block with current time context."""
        now = datetime.now()
        hour = now.hour

        if 5 <= hour < 9:
            time_of_day = "ráno"
        elif 9 <= hour < 12:
            time_of_day = "dopoledne"
        elif 12 <= hour < 14:
            time_of_day = "poledne"
        elif 14 <= hour < 18:
            time_of_day = "odpoledne"
        elif 18 <= hour < 22:
            time_of_day = "večer"
        else:
            time_of_day = "noc"

        day_name = _DAY_NAMES_CS[now.weekday()]
        is_weekend = now.weekday() >= 5
        date_str = f"{now.day}. {_MONTH_NAMES_CS[now.month]} {now.year}"
        time_str = now.strftime("%H:%M")

        parts = [
            "<current_time>",
            f"Datum: {day_name} {date_str}",
            f"Čas: {time_str} ({time_of_day})",
        ]
        if is_weekend:
            parts.append("Je víkend.")

        holiday = _CZECH_HOLIDAYS.get((now.month, now.day))
        if holiday:
            parts.append(f"Dnes je: {holiday}")

        parts.append("</current_time>")
        return "\n".join(parts)

    @staticmethod
    def _mood_to_guidance(mood: str) -> str:
        """Convert detected user mood to Czech guidance for the LLM."""
        guidance = {
            "happy": "Uživatel je v dobré náladě. Můžeš být uvolněnější a sdílet jeho radost.",
            "frustrated": "Uživatel je frustrovaný. Buď empatická, věcná a trpělivá. Nebuď příliš veselá.",
            "sad": "Uživatel je smutný. Buď citlivá a vřelá. Nabídni podporu, ale nevnucuj se.",
            "curious": "Uživatel je zvědavý a chce se dozvědět víc. Můžeš jít víc do hloubky.",
            "stressed": "Uživatel je ve stresu. Buď stručná, efektivní a uklidňující.",
            "excited": "Uživatel je nadšený. Sdílej jeho nadšení, ale zůstaň přirozená.",
        }
        return guidance.get(mood, "")

    def _get_observations_block(self) -> str | None:
        """Build XML block with Eigy's observations about the user."""
        profile = self.profile.get_full_profile()
        observations = profile.get("eigy_observations", {})
        if not isinstance(observations, dict):
            return None
        obs_items = []
        for cat in ("behavioral_patterns", "communication_notes",
                     "personal_insights", "relationship_notes"):
            val = observations.get(cat, [])
            if isinstance(val, list):
                obs_items.extend(str(item) for item in val if item)
        if not obs_items:
            return None
        formatted = "\n".join(f"- {item}" for item in obs_items[:10])
        return (
            "<eigy_observations>\n"
            "Tvoje postřehy o uživateli (využij přirozeně, neříkej je nahlas):\n"
            f"{formatted}\n"
            "</eigy_observations>"
        )

    _STYLE_DIRECTIVES: dict[str, str] = {
        "KRATCE": "Odpověz MAXIMÁLNĚ 1-2 větami. Nic víc.",
        "ROZVIN": "Rozviň odpověď trochu víc než obvykle.",
        "BEZ_OTAZKY": "Nekončí otázkou. Prostě dokonči myšlenku.",
        "OTAZKA": "Zkus zakončit otázkou.",
    }

    def _compute_style_hint(self, current_messages: list[dict]) -> str | None:
        """Compute a single response style directive based on conversation flow.

        Returns at most ONE short, forceful instruction — multiple hints
        dilute each other and get ignored by the model.
        """
        import random

        if len(current_messages) < 4:
            return None

        recent_assistant = [
            m for m in current_messages[-8:]
            if m["role"] == "assistant"
        ]
        recent_user = [
            m for m in current_messages[-8:]
            if m["role"] == "user"
        ]

        if not recent_assistant:
            return None

        avg_assistant_len = (
            sum(len(m["content"]) for m in recent_assistant) / len(recent_assistant)
        )
        avg_user_len = (
            sum(len(m["content"]) for m in recent_user) / len(recent_user)
            if recent_user else 0
        )
        assistant_count = len(recent_assistant)
        last_3_lens = [len(m["content"]) for m in recent_assistant[-3:]]

        # Priority order — return the FIRST match only.

        # 1. User writes short, assistant way too verbose → strongest signal
        if avg_user_len < 50 and avg_assistant_len > 200:
            return "KRATCE"

        # 2. All last 3 responses long and similar → monotony
        if (len(last_3_lens) >= 3
                and all(l > 200 for l in last_3_lens)):
            return "KRATCE"

        # 3. Monotony — similar lengths (any range)
        if len(last_3_lens) >= 3:
            mid = sum(last_3_lens) / 3
            if mid > 0 and all(abs(l - mid) / mid < 0.25 for l in last_3_lens):
                if mid > 150:
                    return "KRATCE"
                else:
                    return "ROZVIN"

        # 4. Too many questions in a row
        if (len(recent_assistant) >= 3
                and all("?" in m["content"][-50:]
                        for m in recent_assistant[-3:])):
            return "BEZ_OTAZKY"

        # 5. Random variator — every ~3rd message, 50% chance brevity nudge
        if assistant_count >= 3 and assistant_count % 3 == 0:
            if random.random() < 0.5:
                return "KRATCE"

        # 6. No questions in a while → suggest one
        if (len(recent_assistant) >= 4
                and not any("?" in m["content"]
                            for m in recent_assistant[-4:])):
            return "OTAZKA"

        return None

    async def generate_pre_reasoning(
        self,
        current_messages: list[dict],
    ) -> str | None:
        """Generate chain-of-thought pre-reasoning via aux model.

        Returns reasoning text to inject into context, or None on failure/skip.
        """
        if not config.CHAIN_OF_THOUGHT_ENABLED:
            return None

        profile_summary = self.db.get_user_profile_summary()
        recent = current_messages[-6:] if len(current_messages) > 6 else current_messages
        recent_text = "\n".join(
            f"{m['role'].capitalize()}: {m['content'][:200]}" for m in recent
        )

        prompt = PRE_REASONING_PROMPT.format(
            profile_summary=profile_summary or "(žádný profil)",
            recent_messages=recent_text,
        )
        prompt_messages = [{"role": "user", "content": prompt}]

        try:
            result = await chat_engine.get_auxiliary_response(prompt_messages)
            if result and len(result.strip()) > 20:
                self._debug(f"Pre-reasoning: {result[:100]}...")
                return result.strip()
        except Exception as e:
            self._debug(f"Pre-reasoning selhalo: {e}")
            logger.debug("Pre-reasoning failed: %s", e)

        return None

    def start_workers(self) -> None:
        """Start background workers. Call after event loop is running."""
        self._extraction_worker_task = asyncio.create_task(self._extraction_worker())

    @property
    def system_prompt(self) -> str:
        return config.SYSTEM_PROMPT_TEMPLATE.format(
            user_name=self.user_name,
            assistant_name=config.ASSISTANT_NAME,
        )

    def build_context(
        self,
        current_messages: list[dict],
        user_mood: str | None = None,
        internal_reasoning: str | None = None,
    ) -> list[dict]:
        """Build the full messages array for the LLM with relevant history.

        Layers:
        1. System prompt (with user name)
        1.5. Temporal awareness (current time/date)
        1.6. User mood (emotional adaptation)
        2. Structured user profile
        2.5. Eigy's observations about user
        3. Recent conversation summaries
        3.5. Episodic memories (semantic retrieval from past exchanges)
        4. Tail of previous session
        4.5. Mid-session summaries (rolling window)
        5. Current session messages
        5.5. Response style hint
        5.6. Internal reasoning (chain-of-thought)
        """
        context: list[dict] = []

        # 1. System prompt
        context.append({"role": "system", "content": self.system_prompt})

        # 1.5. Temporal awareness
        if config.TEMPORAL_AWARENESS_ENABLED:
            temporal = self._build_temporal_block()
            context.append({"role": "system", "content": temporal})

        # 1.6. User mood (emotional adaptation)
        if config.EMOTIONAL_ADAPTATION_ENABLED and user_mood and user_mood != "neutral":
            mood_guidance = self._mood_to_guidance(user_mood)
            if mood_guidance:
                context.append({
                    "role": "system",
                    "content": (
                        f"<user_mood current=\"{user_mood}\">\n"
                        f"{mood_guidance}\n"
                        "</user_mood>"
                    ),
                })
                self._debug(f"Nálada uživatele: {user_mood}")

        # 2. User profile (structured)
        profile_summary = self.db.get_user_profile_summary()
        if profile_summary:
            context.append({
                "role": "system",
                "content": (
                    f"<user_profile verified=\"true\">\n"
                    f"{profile_summary}\n"
                    f"</user_profile>"
                ),
            })

        # 2.5. Eigy's observations about user
        if config.EIGY_OBSERVATIONS_ENABLED:
            observations_block = self._get_observations_block()
            if observations_block:
                context.append({"role": "system", "content": observations_block})

        # 3. Recent conversation summaries
        summaries = self.db.get_recent_summaries(limit=config.MEMORY_SUMMARY_COUNT)
        if summaries:
            formatted = "\n".join(
                f"- {s['date']}: {s['summary']}" for s in summaries
            )
            context.append({
                "role": "system",
                "content": (
                    "<conversation_summaries>\n"
                    f"{formatted}\n"
                    "</conversation_summaries>"
                ),
            })

        # 3.5. Episodic memories (semantic retrieval from past exchanges)
        if self.episodic and current_messages:
            # Collect last N user messages for richer query context
            user_msgs = [
                m["content"] for m in reversed(current_messages)
                if m["role"] == "user"
            ][:config.EPISODIC_QUERY_MESSAGES]
            if user_msgs:
                query = " ".join(reversed(user_msgs))
                episodes = self.episodic.retrieve_relevant(
                    query=query,
                    top_k=config.EPISODIC_TOP_K,
                )
                if episodes:
                    formatted_eps = "\n---\n".join(
                        ep["document"] for ep in episodes
                    )
                    context.append({
                        "role": "system",
                        "content": (
                            "<episodic_memories reliability=\"medium\" note=\"Mohou být zastaralé\">\n"
                            f"{formatted_eps}\n"
                            "</episodic_memories>"
                        ),
                    })

        # 4. Tail of previous session (for continuity)
        prev_messages = self.db.get_previous_session_messages(
            limit=config.MEMORY_TAIL_MESSAGES
        )
        if prev_messages:
            context.append({
                "role": "system",
                "content": "<previous_session>",
            })
            context.extend(prev_messages)
            context.append({
                "role": "system",
                "content": "</previous_session>",
            })

        # 4.5. Mid-session summaries (rolling window)
        if self.mid_session_summaries:
            formatted_mid = "\n".join(
                f"[Část {i + 1}] {s}"
                for i, s in enumerate(self.mid_session_summaries)
            )
            context.append({
                "role": "system",
                "content": (
                    "<mid_session_summaries>\n"
                    f"{formatted_mid}\n"
                    "</mid_session_summaries>"
                ),
            })

        # 5. Current session messages
        context.extend(current_messages)

        # 5.5. Response style hint (appended to last user message)
        self.last_style_hint = None
        if config.STYLE_VARIATION_ENABLED:
            style_hint = self._compute_style_hint(current_messages)
            if style_hint:
                self.last_style_hint = style_hint
                directive = self._STYLE_DIRECTIVES.get(style_hint)
                if directive and context and context[-1]["role"] == "user":
                    context[-1] = {
                        **context[-1],
                        "content": (
                            f"{context[-1]['content']}\n\n"
                            f"[Styl odpovědi: {directive}]"
                        ),
                    }
                self._debug(f"Style hint: {style_hint}")

        # 5.6. Internal reasoning (chain-of-thought, inserted before last message)
        if internal_reasoning:
            context.insert(-1, {
                "role": "system",
                "content": (
                    "<internal_reasoning>\n"
                    f"{internal_reasoning}\n"
                    "</internal_reasoning>"
                ),
            })
            self._debug("Pre-reasoning injektován do kontextu")

        # 6. Enforce token budget
        total_before = sum(self._estimate_tokens(m["content"]) for m in context)
        context = self._enforce_token_budget(context)
        total_after = sum(self._estimate_tokens(m["content"]) for m in context)

        self._debug(f"Kontext: {len(context)} zpráv, ~{total_after} tokenů")
        if total_before != total_after:
            self._debug(f"Budget: trimováno {total_before} → {total_after} tokenů")

        logger.info(
            "Context built: %d msgs, ~%d tokens (before: %d, trimmed: %s)",
            len(context), total_after, total_before, total_before != total_after,
        )

        return context

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimate. Czech averages ~3 chars per token."""
        return len(text) // 3

    def _enforce_token_budget(self, context: list[dict]) -> list[dict]:
        """Trim context layers by priority if total exceeds budget.

        Priority order (1 = trim first): mid-session summaries, episodic,
        conversation summaries, prev session tail, profile.
        System prompt and current messages are never trimmed.
        """
        total = sum(self._estimate_tokens(m["content"]) for m in context)
        if total <= config.MAX_CONTEXT_TOKENS:
            return context

        # (prefix, priority, per-layer budget) — lower priority = trimmed first
        trim_rules = [
            ("<internal_reasoning>", 1, 500),
            ("<eigy_observations>", 3, 300),
            ("<user_mood", 4, 100),
            ("<current_time>", 5, 100),
            ("<mid_session_summaries>", 6, config.BUDGET_MID_SESSION),
            ("<episodic_memories", 7, config.BUDGET_EPISODIC),
            ("<conversation_summaries>", 8, config.BUDGET_SUMMARIES),
            ("<previous_session>", 9, config.BUDGET_PREV_SESSION),
            ("<user_profile", 10, config.BUDGET_PROFILE),
        ]

        # Phase 1: truncate oversized layers to their budget
        for prefix, _priority, max_tokens in trim_rules:
            if total <= config.MAX_CONTEXT_TOKENS:
                break
            for msg in context:
                if msg["role"] != "system" or not msg["content"].startswith(prefix):
                    continue
                msg_tokens = self._estimate_tokens(msg["content"])
                if msg_tokens > max_tokens:
                    char_limit = max_tokens * 3
                    saved = msg_tokens - max_tokens
                    msg["content"] = msg["content"][:char_limit] + "\n[...zkráceno]"
                    total -= saved
                break

        # Phase 2: remove entire layers if still over budget
        for prefix, _priority, _ in trim_rules:
            if total <= config.MAX_CONTEXT_TOKENS:
                break

            if prefix == "<previous_session>":
                # Special: prev session tail spans multiple messages
                # (opening tag, user/assistant pairs, closing tag)
                filtered = []
                in_prev = False
                for msg in context:
                    if msg["role"] == "system" and msg["content"].startswith(prefix):
                        in_prev = True
                        total -= self._estimate_tokens(msg["content"])
                        continue
                    if in_prev and msg["content"] == "</previous_session>":
                        total -= self._estimate_tokens(msg["content"])
                        in_prev = False
                        continue
                    if in_prev and msg["role"] in ("user", "assistant"):
                        total -= self._estimate_tokens(msg["content"])
                        continue
                    in_prev = False
                    filtered.append(msg)
                context = filtered
            else:
                new_context = []
                for m in context:
                    if m["role"] == "system" and m["content"].startswith(prefix):
                        total -= self._estimate_tokens(m["content"])
                        continue
                    new_context.append(m)
                context = new_context

        logger.debug("Token budget enforced: ~%d tokens", total)
        return context

    async def maybe_summarize_window(self, current_messages: list[dict]) -> list[dict]:
        """Trim current_messages if it exceeds the rolling window trigger.

        If threshold is reached and no summarization is in progress,
        slices off the oldest chunk, kicks off a background summary task,
        and returns the trimmed tail immediately (non-blocking).
        """
        if len(current_messages) < config.ROLLING_WINDOW_TRIGGER:
            return current_messages
        if self._summarizing:
            return current_messages

        n = config.ROLLING_WINDOW_CHUNK
        to_summarize = current_messages[:n]
        remaining = current_messages[n:]

        self._summarizing = True
        asyncio.create_task(self._mid_session_summarize(to_summarize))

        return remaining

    async def _mid_session_summarize(self, messages: list[dict]) -> None:
        """Background task: summarize a chunk of messages via aux model."""
        try:
            summary = await self._generate_summary(messages)
            if summary:
                self.mid_session_summaries.append(summary)
                logger.info(
                    "Mid-session summary #%d generated (%d msgs → %d chars)",
                    len(self.mid_session_summaries),
                    len(messages),
                    len(summary),
                )
        except Exception as e:
            logger.warning("Mid-session summarization failed: %s", e)
        finally:
            self._summarizing = False

    def save_message(self, role: str, content: str, emotion: str | None = None) -> None:
        """Persist a message to the database."""
        self.db.insert_message(self.session_id, role, content, emotion)

    async def store_episode(self, user_msg: str, assistant_msg: str) -> None:
        """Store an exchange in episodic memory (if available)."""
        if not self.episodic:
            return
        if len(user_msg.strip()) < 10:
            return
        try:
            self.episodic.store_exchange(
                user_msg=user_msg,
                assistant_msg=assistant_msg,
                session_id=self.session_id,
            )
            logger.info("Episode stored: user_msg_len=%d", len(user_msg))
        except Exception as e:
            logger.debug("Failed to store episode: %s", e)

    async def extract_facts_realtime(self, user_msg: str, assistant_msg: str) -> None:
        """Enqueue a fact extraction task (processed by background worker)."""
        if len(user_msg.strip()) < 20:
            return
        await self._extraction_queue.put((user_msg, assistant_msg, 0))

    async def _extraction_worker(self) -> None:
        """Background worker that processes fact extraction from a queue.

        On failure, retries once after a delay. None sentinel stops the worker.
        """
        while True:
            item = await self._extraction_queue.get()
            if item is None:
                break
            user_msg, assistant_msg, retry_count = item
            try:
                await self._do_extract_facts(user_msg, assistant_msg)
            except Exception as e:
                if retry_count < 1:
                    logger.debug("Extraction failed, retrying in 5s: %s", e)
                    await asyncio.sleep(5)
                    await self._extraction_queue.put((user_msg, assistant_msg, retry_count + 1))
                else:
                    logger.warning("Extraction failed after retry: %s", e)

    async def _do_extract_facts(self, user_msg: str, assistant_msg: str) -> None:
        """Actually perform fact extraction from a single exchange."""
        profile = self.profile.get_full_profile()
        current_profile = json.dumps(profile, ensure_ascii=False)
        prompt = REALTIME_EXTRACTION_PROMPT.format(
            current_profile=current_profile,
            user_msg=user_msg,
            assistant_msg=assistant_msg[:500],
        )
        prompt_messages = [{"role": "user", "content": prompt}]
        response = await chat_engine.get_auxiliary_json_response(prompt_messages)
        if not response:
            return

        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            response = "\n".join(lines[1:-1])

        data = json.loads(response)

        if data:
            self.profile.update_from_extraction(data)
            keys = list(data.keys())
            self._debug(f"Extrakce: {keys}")
            logger.info("Real-time extraction: keys=%s, user_msg_len=%d", keys, len(user_msg))

    async def condense_profile(self) -> None:
        """Condense the user profile via LLM to remove duplicates and stale info.

        Called once per session during end_session(), after extraction.
        Preserves schema version and user name regardless of LLM output.
        """
        if not config.PROFILE_EVICTION_ENABLED:
            return

        profile = self.profile.get_full_profile()
        profile_json = json.dumps(profile, ensure_ascii=False, indent=2)

        # Only condense if profile is substantial
        if len(profile_json) < 500:
            return

        # Save snapshot before condensation (recoverable backup)
        try:
            self.db.save_profile_snapshot(profile_json)
            self.db.cleanup_old_snapshots(keep=config.PROFILE_SNAPSHOT_KEEP)
        except Exception as e:
            logger.warning("Failed to save profile snapshot: %s", e)

        prompt = PROFILE_CONDENSATION_PROMPT.format(profile_json=profile_json)
        prompt_messages = [{"role": "user", "content": prompt}]

        try:
            response = await chat_engine.get_auxiliary_json_response(prompt_messages)
            if not response:
                return

            response = response.strip()
            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join(lines[1:-1])

            condensed = json.loads(response)

            if not isinstance(condensed, dict):
                logger.warning("Profile condensation returned non-dict, skipping.")
                return

            # Preserve schema version
            condensed["version"] = profile.get("version", 2)

            # Preserve name (never evict the user's name)
            if profile.get("basic", {}).get("name"):
                condensed.setdefault("basic", {})["name"] = profile["basic"]["name"]

            # Preserve changelog (internal tracking, not for LLM to modify)
            if "_changelog" in profile:
                condensed["_changelog"] = profile["_changelog"]

            self.profile._profile = condensed
            self.profile._save()
            new_len = len(json.dumps(condensed, ensure_ascii=False))
            self._debug(f"Profil zkondenzován: {len(profile_json)} → {new_len} znaků")
            logger.info("Profile condensed: %d → %d chars", len(profile_json), new_len)

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("Profile condensation parse error: %s", e)
        except Exception as e:
            logger.warning("Profile condensation failed: %s", e)

    async def correct_profile(self, instruction: str) -> bool:
        """Correct the user profile based on a user instruction.

        Sends the current profile + instruction to the aux model,
        which returns a corrected JSON profile.
        Returns True if the profile was updated.
        """
        profile = self.profile.get_full_profile()
        profile_json = json.dumps(profile, ensure_ascii=False, indent=2)

        prompt = PROFILE_CORRECTION_PROMPT.format(
            instruction=instruction,
            profile_json=profile_json,
        )
        prompt_messages = [{"role": "user", "content": prompt}]

        try:
            response = await chat_engine.get_auxiliary_json_response(prompt_messages)
            if not response:
                return False

            response = response.strip()
            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join(lines[1:-1])

            corrected = json.loads(response)

            if not isinstance(corrected, dict):
                logger.warning("Profile correction returned non-dict, skipping.")
                return False

            # Preserve schema version, name, changelog
            corrected["version"] = profile.get("version", 2)
            if profile.get("basic", {}).get("name"):
                corrected.setdefault("basic", {})["name"] = profile["basic"]["name"]
            if "_changelog" in profile:
                corrected["_changelog"] = profile["_changelog"]

            self.profile._profile = corrected
            self.profile._save()
            self._debug(f"Profil opraven: {instruction}")
            logger.info("Profile corrected via /oprav: %s", instruction)
            return True

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("Profile correction parse error: %s", e)
            return False
        except Exception as e:
            logger.warning("Profile correction failed: %s", e)
            return False

    async def end_session(self) -> None:
        """Called on exit — generate summary, extract profile updates, condense profile."""
        # Drain extraction queue before final extraction
        if self._extraction_worker_task:
            await self._extraction_queue.put(None)  # sentinel to stop worker
            await self._extraction_worker_task
            self._extraction_worker_task = None

        messages = self.db.get_session_messages(self.session_id)
        if not messages:
            self.db.end_conversation(self.session_id)
            logger.info("Session %s ended with no messages.", self.session_id)
            return

        logger.info("Ending session %s with %d messages.", self.session_id, len(messages))

        # Generate summary
        try:
            summary = await self._generate_summary(messages)
            if summary:
                self.db.update_conversation_summary(self.session_id, summary)
                logger.info("Session summary: %s", summary[:150])
        except Exception as e:
            logger.warning("Failed to generate session summary: %s", e)

        # Extract profile updates
        try:
            await self._extract_profile_updates(messages)
        except Exception as e:
            logger.warning("Failed to extract profile updates: %s", e)

        # Condense profile (after extraction so new facts are included)
        try:
            await self.condense_profile()
        except Exception as e:
            logger.warning("Failed to condense profile: %s", e)

        # Prune old episodic memories
        if self.episodic:
            try:
                pruned = self.episodic.prune_old_episodes(
                    max_age_days=config.EPISODIC_MAX_AGE_DAYS,
                    min_importance=config.EPISODIC_MIN_IMPORTANCE_FOR_KEEP,
                )
                if pruned:
                    logger.info("Pruned %d old episodes at session end.", pruned)
            except Exception as e:
                logger.warning("Failed to prune old episodes: %s", e)

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
        profile = self.profile.get_full_profile()
        current_profile = json.dumps(profile, ensure_ascii=False)
        formatted = "\n".join(
            f"{m['role'].capitalize()}: {m['content']}" for m in messages
        )
        prompt = EXTRACTION_PROMPT.format(current_profile=current_profile)
        prompt_messages = [
            {"role": "user", "content": f"{prompt}\n\nConversation:\n{formatted}"}
        ]
        response = await chat_engine.get_auxiliary_json_response(prompt_messages)
        if not response:
            return
        try:
            response = response.strip()
            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join(lines[1:-1])
            data = json.loads(response)
            if data:
                self.profile.update_from_extraction(data)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("Could not parse profile extraction: %s", e)
