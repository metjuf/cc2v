"""Eigy AI Assistant — Entry point.

Pygame runs in MAIN thread, chat + TTS in daemon thread.
Communication via thread-safe queues.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import re
import sys
import threading

import config
import chat_engine
import display
from avatar.emotion_detector import (
    detect_emotion, detect_emotion_llm,
    detect_user_mood, detect_user_mood_llm,
)
from memory.database import Database
from memory.memory_manager import MemoryManager
from plugins import PluginManager
from plugins.base import PluginContext
from proactive import IdleMonitor
from session_logger import SessionLogger
from tts_engine import TTSEngine, SentenceBuffer, cleanup_temp_files
from audio_player import AudioPlayer

# Episodic memory (optional dependency)
try:
    from memory.episodic import EpisodicMemory, is_available as episodic_available
except ImportError:
    EpisodicMemory = None  # type: ignore[assignment,misc]

    def episodic_available() -> bool:  # type: ignore[misc]
        return False

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
# Suppress noisy warnings from search dependencies
logging.getLogger("primp").setLevel(logging.ERROR)
logging.getLogger("ddgs").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

_NAME = config.ASSISTANT_NAME

# Strip emoji/emoticons from LLM output (prevents TTS reading them)
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002702-\U000027B0"  # dingbats
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero width joiner
    "\U000020E3"             # combining enclosing keycap
    "\U00002600-\U000026FF"  # misc symbols
    "\U00002300-\U000023FF"  # misc technical
    "]+",
)


# ── Chat thread (daemon) ──────────────────────────────────────────


def chat_thread_main(
    avatar_queue: queue.Queue,
    audio_player: AudioPlayer,
) -> None:
    """Daemon thread entry point: runs the async chat loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(chat_main(avatar_queue, audio_player))
    except Exception as e:
        logger.error("Chat thread error: %s", e)
    finally:
        # Signal avatar window to close
        avatar_queue.put({"type": "quit"})
        loop.close()


async def chat_main(
    avatar_queue: queue.Queue,
    audio_player: AudioPlayer,
) -> None:
    """Async chat main — init memory, onboard, run chat loop."""
    tts = TTSEngine()

    # Validate configuration
    warnings = config.validate_config()
    for w in warnings:
        display.show_system(f"Warning: {w}")

    if not config.ANTHROPIC_API_KEY and not config.OPENROUTER_API_KEY:
        display.show_error(
            "Není nastaven žádný LLM API klíč. "
            "Zkopíruj .env.example do .env a přidej svůj API klíč."
        )
        avatar_queue.put({"type": "quit"})
        return

    # Initialize debug mode
    display.set_debug(config.DEBUG_ENABLED)
    _dbg = display.show_debug

    # Initialize database and memory
    db = Database(config.DATABASE_PATH)

    # Initialize episodic memory (optional — requires chromadb + sentence-transformers)
    episodic = None
    if config.EPISODIC_MEMORY_ENABLED and episodic_available():
        try:
            episodic = EpisodicMemory(config.CHROMADB_PATH, debug_callback=_dbg)
            display.show_system(f"Epizodická paměť aktivní ({episodic.count()} vzpomínek)")
        except Exception as e:
            logger.warning("Failed to initialize episodic memory: %s", e)
            display.show_system("Epizodická paměť nedostupná, pokračuji bez ní.")

    memory = MemoryManager(db, episodic=episodic, debug_callback=_dbg)
    memory.start_workers()

    # Initialize session logger
    slog = SessionLogger(
        session_id=memory.session_id,
        log_dir=config.LOG_DIR if config.LOG_TO_FILE else None,
        sessions_dir=config.SESSIONS_DIR if config.LOG_TO_FILE else None,
        enabled=config.LOG_TO_FILE,
    )
    slog.log_session_start(
        user_name=memory.user_name,
        config_snapshot={
            "anthropic_model": config.ANTHROPIC_MODEL,
            "aux_model": config.AUX_MODEL,
            "tts_enabled": config.TTS_ENABLED,
            "tts_voice": config.TTS_VOICE,
            "emotion_detection": config.EMOTION_DETECTION,
            "episodic_memory": config.EPISODIC_MEMORY_ENABLED,
            "temporal_awareness": config.TEMPORAL_AWARENESS_ENABLED,
            "emotional_adaptation": config.EMOTIONAL_ADAPTATION_ENABLED,
            "style_variation": config.STYLE_VARIATION_ENABLED,
            "chain_of_thought": config.CHAIN_OF_THOUGHT_ENABLED,
            "observations": config.EIGY_OBSERVATIONS_ENABLED,
            "intent_tagging": config.ASSISTANT_INTENT_TAGGING_ENABLED,
            "smart_proactive": config.SMART_PROACTIVE_ENABLED,
            "proactive_enabled": config.PROACTIVE_ENABLED,
        },
    )

    try:
        # First-run onboarding
        if db.is_first_run():
            await first_run_onboarding(memory, tts, audio_player, avatar_queue)

        # Main chat loop
        await chat_loop(db, memory, tts, audio_player, avatar_queue, slog)

        # End session
        display.show_system("Ukládám relaci...")
        await memory.end_session()
        display.show_system("Relace uložena. Tak zase příště.")
    except Exception as e:
        logger.error("Chat loop error: %s", e)
        slog.log_error(str(e), "chat_main")
        display.show_error(f"Unexpected error: {e}")
    finally:
        slog.close()
        db.close()
        cleanup_temp_files()


async def first_run_onboarding(
    memory: MemoryManager,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
) -> None:
    """First-launch onboarding — Eigy introduces herself and asks for user's name."""
    display.show_welcome_banner()

    greeting = (
        "Dobrý den. Jsem vaše osobní asistentka. "
        "Než začneme — jak vám mám říkat?"
    )
    display.show_assistant(greeting)
    await _speak(greeting, tts, audio_player, avatar_queue)

    name = await display.get_user_input()
    if not name:
        name = "šéfe"

    memory.profile.set_name(name)
    memory.user_name = name

    response = (
        f"Těší mě, {name}. Jsem {_NAME} — vaše osobní AI asistentka. "
        f"Budu si pamatovat, co mi řeknete, a pomohu vám s čímkoli potřebujete. "
        "Napište /help pro seznam příkazů."
    )
    display.show_assistant(response)
    avatar_queue.put({"type": "emotion", "value": "happy"})
    await _speak(response, tts, audio_player, avatar_queue)

    display.console.print()


async def handle_command(
    user_input: str,
    memory: MemoryManager,
    db: Database,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
    slog: SessionLogger | None = None,
) -> bool:
    """Handle slash commands. Returns True if handled."""
    parts = user_input.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if slog:
        slog.log_command(cmd, arg)

    if cmd == "/help":
        display.show_help()
    elif cmd == "/debug":
        new_state = not display.is_debug()
        display.set_debug(new_state)
        status = "zapnutý" if new_state else "vypnutý"
        display.show_system(f"Debug režim {status}.")
    elif cmd == "/memory":
        summary = memory.profile.get_summary()
        if summary:
            display.show_system(f"Co o tobě vím: {summary}")
        else:
            display.show_system("Zatím o tobě nic nemám uložené.")
        summaries = db.get_recent_summaries(limit=5)
        if summaries:
            display.show_system("Nedávné konverzace:")
            for s in summaries:
                display.show_system(f"  {s['date']}: {s['summary']}")
    elif cmd == "/forget":
        display.show_system(
            "Tím smažeš VŠECHNY moje vzpomínky na tebe. Jsi si jistý/á? (ano/ne)"
        )
        confirm = await display.get_user_input()
        if confirm and confirm.lower() in ("ano", "a", "yes", "y"):
            db.clear_all()
            if memory.episodic:
                memory.episodic.clear_all()
            display.show_system("Paměť vymazána. Jako bychom se nikdy nepotkali.")
            memory.user_name = "šéfe"
        else:
            display.show_system("Zrušeno.")
    elif cmd == "/voice":
        if arg and arg.lower() == "on":
            tts.set_enabled(True)
            display.show_system("Hlas zapnutý.")
        elif arg and arg.lower() == "off":
            tts.set_enabled(False)
            audio_player.stop()
            display.show_system("Hlas vypnutý.")
        elif arg:
            tts.set_voice(arg)
            display.show_system(f"Hlas nastaven na: {arg}")
        else:
            status = "zapnutý" if tts.enabled else "vypnutý"
            display.show_system(f"Hlas je {status}. Použití: /voice on|off|<jméno>")
    elif cmd == "/volume":
        if arg:
            try:
                level = int(arg)
                audio_player.set_volume(level)
                display.show_system(f"Hlasitost nastavena na {level} %.")
            except ValueError:
                display.show_system("Použití: /volume 0-100")
        else:
            display.show_system(f"Hlasitost: {int(audio_player.volume * 100)} %")
    elif cmd == "/emotion":
        if arg:
            avatar_queue.put({"type": "emotion", "value": arg.lower()})
            display.show_system(f"Emoce nastavena na: {arg}")
        else:
            display.show_system("Použití: /emotion neutral|amused|happy|concerned|surprised|thinking")
    elif cmd == "/avatar":
        avatar_queue.put({"type": "toggle_avatar"})
        display.show_system("Okno avatara přepnuto.")
    elif cmd == "/model":
        if arg:
            config.ANTHROPIC_MODEL = arg
            display.show_system(f"Primární model nastaven na: {arg}")
        else:
            display.show_system(f"Aktuální model: {config.ANTHROPIC_MODEL}")
    elif cmd == "/history":
        msgs = db.get_session_messages(memory.session_id)
        if msgs:
            for m in msgs:
                role = "Ty" if m["role"] == "user" else _NAME
                display.show_system(f"  {role}: {m['content'][:100]}...")
        else:
            display.show_system("V této relaci zatím žádné zprávy.")
    elif cmd == "/oprav":
        if not arg:
            display.show_system("Použití: /oprav <instrukce>  (např. /oprav nepracuji v Google)")
        else:
            display.show_system("Opravuji profil...")
            success = await memory.correct_profile(arg)
            if success:
                updated = memory.profile.get_summary()
                display.show_system(f"Profil opraven. Aktuální: {updated}")
            else:
                display.show_system("Opravu se nepodařilo provést.")
    elif cmd == "/export":
        import json as _json
        export_data = {
            "profile": memory.profile.get_full_profile(),
            "conversations": [],
        }
        summaries = db.get_recent_summaries(limit=100)
        for s in summaries:
            export_data["conversations"].append({
                "date": str(s["date"]),
                "summary": s["summary"],
            })
        export_path = config.PROJECT_ROOT / "eigy_export.json"
        export_path.write_text(_json.dumps(export_data, indent=2, ensure_ascii=False))
        display.show_system(f"Exportováno do {export_path}")
    else:
        display.show_system(f"Příkaz '{cmd}' ještě není implementován.")

    return True


# Backwards compatibility for tests
from plugins.book_reader_plugin import detect_book_command  # noqa: E402,F401


# ── Input/event race ──────────────────────────────────────────────


async def _wait_for_action(
    event_queue: asyncio.Queue,
    input_task: asyncio.Task | None = None,
) -> tuple[str, object, asyncio.Task | None]:
    """Race between user input and internal events (idle, iMessage).

    Returns (action_type, payload, ongoing_input_task).
    - ("input", user_text, None) — user typed something
    - ("idle_trigger", event_dict, input_task) — idle timeout
    - ("imessage_new", event_dict, input_task) — new iMessage
    """
    if input_task is None:
        input_task = asyncio.create_task(display.get_user_input())

    event_task = asyncio.create_task(event_queue.get())

    done, pending = await asyncio.wait(
        {input_task, event_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if input_task in done:
        # User typed something — cancel event wait
        event_task.cancel()
        try:
            await event_task
        except asyncio.CancelledError:
            pass
        return ("input", input_task.result(), None)
    else:
        # Internal event fired — DON'T cancel input (user may be typing)
        event = event_task.result()
        return (event.get("type", "unknown"), event, input_task)


# ── Proactive response ────────────────────────────────────────────

PROACTIVE_PROMPT_TIER1 = """\
Jsi {assistant_name}, osobní AI asistentka. Právě je chvíli ticho v konverzaci s {user_name}.
Řekni něco přirozeného — zeptej se na něco, nabídni pomoc, udělej postřeh, nebo navrhni aktivitu.
Buď stručná (1-2 věty). Nebuď otravná, buď přirozená.
Odpovídej ČESKY. NEPOUŽÍVEJ *akce v hvězdičkách*.\
"""

SMART_PROACTIVE_PROMPT_TIER1 = """\
Jsi {assistant_name}, osobní AI asistentka. Právě je chvíli ticho v konverzaci s {user_name}.
Máš k dispozici kontext z předchozích rozhovorů a profil uživatele.

Řekni něco přirozeného a KONTEXTOVĚ relevantního — například:
- Zeptej se na výsledek něčeho, co uživatel zmínil dříve
- Nabídni pomoc s něčím, co víš že uživatel řeší
- Reaguj na denní dobu nebo situaci (ráno/večer/víkend)
- Vrať se k zajímavému tématu z minulé konverzace
- Udělej postřeh na základě toho, co víš o uživateli

Buď stručná (1-2 věty). Nebuď otravná, buď přirozená a relevantní.
Odpovídej ČESKY. NEPOUŽÍVEJ *akce v hvězdičkách*.\
"""

PROACTIVE_PROMPT_TIER2 = """\
Jsi {assistant_name}, osobní AI asistentka. {user_name} je pryč už delší dobu.
Napiš krátkou zprávu (1 věta), že tu stále jsi, ale pokud se neozve, za chvíli se vypneš.
Buď přátelská a nenápadná. Odpovídej ČESKY. NEPOUŽÍVEJ *akce v hvězdičkách*.\
"""

SHUTDOWN_MESSAGE = "Tak já se vypínám. Kdybyste mě potřebovali, {user_name}, stačí mě zase spustit. Na shledanou."


async def proactive_response(
    text: str | None,
    memory: MemoryManager,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
    current_messages: list[dict],
    tier: int = 1,
) -> None:
    """Generate and display a proactive message from Eigy.

    If text is provided, use it directly.
    If text is None, ask the LLM to generate a contextual message.
    tier controls which prompt is used (1 = casual check-in, 2 = pre-shutdown notice).
    """
    if text is None:
        # Generate contextual message via LLM
        try:
            if tier >= 2:
                prompt_template = PROACTIVE_PROMPT_TIER2
            elif config.SMART_PROACTIVE_ENABLED:
                prompt_template = SMART_PROACTIVE_PROMPT_TIER1
            else:
                prompt_template = PROACTIVE_PROMPT_TIER1

            proactive_system = prompt_template.format(
                assistant_name=config.ASSISTANT_NAME,
                user_name=memory.user_name,
            )
            # Build slim context: system + last few messages
            messages: list[dict] = [
                {"role": "system", "content": proactive_system},
            ]
            profile_summary = memory.db.get_user_profile_summary()
            if profile_summary:
                messages.append({
                    "role": "system",
                    "content": f"Znáš o {memory.user_name}: {profile_summary}",
                })

            # Smart proactive: add episodic context for relevant follow-ups
            if config.SMART_PROACTIVE_ENABLED and tier == 1 and memory.episodic:
                recent_user = [m for m in current_messages if m["role"] == "user"]
                if recent_user:
                    query = recent_user[-1]["content"]
                    episodes = memory.episodic.retrieve_relevant(query, top_k=3)
                    if episodes:
                        ep_text = "\n---\n".join(ep["document"] for ep in episodes)
                        messages.append({
                            "role": "system",
                            "content": (
                                "Relevantní vzpomínky z minulých konverzací:\n"
                                f"{ep_text}"
                            ),
                        })

                # Add temporal context
                if config.TEMPORAL_AWARENESS_ENABLED:
                    messages.append({
                        "role": "system",
                        "content": MemoryManager._build_temporal_block(),
                    })

            # Add last 5 messages for context
            recent = current_messages[-5:] if len(current_messages) > 5 else current_messages
            messages.extend(recent)
            messages.append({
                "role": "user",
                "content": "(ticho — řekni něco sama od sebe)",
            })

            response_parts: list[str] = []
            async for token in chat_engine.get_response(messages):
                response_parts.append(token)
            text = "".join(response_parts).strip()

            if not text:
                return
        except Exception as e:
            logger.warning("Failed to generate proactive message: %s", e)
            return

    # Display and speak
    display.show_assistant(text)

    # Detect emotion
    if config.EMOTION_DETECTION == "llm":
        emotion = await detect_emotion_llm(text)
    else:
        emotion = detect_emotion(text)
    avatar_queue.put({"type": "emotion", "value": emotion})

    # Save to memory
    current_messages.append({"role": "assistant", "content": text})
    memory.save_message("assistant", text, emotion=emotion)

    # TTS
    await _speak(text, tts, audio_player, avatar_queue)


# ── Main chat loop ────────────────────────────────────────────────


async def chat_loop(
    db: Database,
    memory: MemoryManager,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
    slog: SessionLogger | None = None,
) -> None:
    """Main chat loop — input, stream response, display, TTS, avatar events."""
    from functools import partial

    current_messages: list[dict] = []

    # Initialize event queue and idle monitor
    event_queue: asyncio.Queue = asyncio.Queue()
    idle_monitor = IdleMonitor(event_queue)
    idle_task = asyncio.create_task(idle_monitor.run())

    # Create plugin context
    ctx = PluginContext(
        db=db,
        memory=memory,
        tts=tts,
        audio_player=audio_player,
        avatar_queue=avatar_queue,
        event_queue=event_queue,
        current_messages=current_messages,
        slog=slog,
        speak=partial(_speak, tts=tts, audio_player=audio_player, avatar_queue=avatar_queue),
        proactive=partial(
            proactive_response,
            memory=memory, tts=tts, audio_player=audio_player,
            avatar_queue=avatar_queue, current_messages=current_messages,
        ),
    )

    # Initialize and discover plugins
    pm = PluginManager()
    pm.discover()
    await pm.start_backgrounds(ctx)

    # Greeting for returning users
    if memory.user_name not in ("friend", "šéfe"):
        display.show_welcome_banner()
        greeting = f"Vítejte zpět, {memory.user_name}. Jak vám mohu pomoci?"
        display.show_assistant(greeting)
        avatar_queue.put({"type": "emotion", "value": "happy"})
        await _speak(greeting, tts, audio_player, avatar_queue)

    display.show_system("Napiš /help pro příkazy, 'exit' pro ukončení.")

    ongoing_input_task: asyncio.Task | None = None

    try:
        while True:
            # Wait for user input OR internal event
            action_type, payload, ongoing_input_task = await _wait_for_action(
                event_queue, ongoing_input_task
            )

            if action_type == "input":
                user_input = payload
                idle_monitor.reset()

                # Check if any plugin wants its activity interrupted
                if isinstance(user_input, str) and pm.check_interrupt(ctx, user_input):
                    # Plugin handles its own interruption via shutdown hook
                    for p in pm.plugins:
                        if p.should_interrupt_on_input(ctx, user_input):
                            await p.shutdown(ctx)
                elif not pm.any_active_task(ctx):
                    audio_player.stop()

                if user_input is None:
                    break  # EOF / Ctrl+D

                # Exit commands
                if user_input.lower() in ("exit", "quit", "konec"):
                    farewell = (
                        f"Na shledanou, {memory.user_name}. "
                        "Kdybyste cokoli potřebovali, jsem tu."
                    )
                    display.show_assistant(farewell)
                    await _speak(farewell, tts, audio_player, avatar_queue)
                    break

                # Empty input
                if not user_input:
                    continue

                # Slash commands (core — stay in main.py)
                if user_input.startswith("/"):
                    await handle_command(
                        user_input, memory, db, tts, audio_player,
                        avatar_queue, slog,
                    )
                    continue

                # Plugin command detection (replaces iMessage + book reader blocks)
                cmd_result = await pm.detect_command(ctx, user_input)
                if cmd_result.handled:
                    continue

                # Plugin pre-response (replaces crypto + web search blocks)
                pre = await pm.pre_response(ctx, user_input)

                # Add user message
                current_messages.append({"role": "user", "content": user_input})
                memory.save_message("user", user_input)

                # Detect user mood (for emotional adaptation)
                user_mood = None
                if config.EMOTIONAL_ADAPTATION_ENABLED:
                    if config.EMOTION_DETECTION == "llm":
                        user_mood = await detect_user_mood_llm(user_input)
                    else:
                        user_mood = detect_user_mood(user_input)
                    if user_mood and user_mood != "neutral":
                        display.show_debug(f"Nálada uživatele: {user_mood}")

                if slog:
                    slog.log_user_message(user_input, mood=user_mood)
                    if user_mood:
                        slog.log_mood_detected(user_mood, config.EMOTION_DETECTION)

                # Rolling window: summarize old messages if threshold exceeded
                trimmed = await memory.maybe_summarize_window(current_messages)
                if trimmed is not current_messages:
                    current_messages.clear()
                    current_messages.extend(trimmed)

                # Chain-of-thought pre-reasoning (optional, adds latency)
                internal_reasoning = None
                if config.CHAIN_OF_THOUGHT_ENABLED:
                    display.show_debug("Generuji pre-reasoning...")
                    internal_reasoning = await memory.generate_pre_reasoning(
                        current_messages
                    )
                    if slog:
                        slog.log_pre_reasoning(internal_reasoning)

                # Build context with memory
                messages = memory.build_context(
                    current_messages,
                    user_mood=user_mood,
                    internal_reasoning=internal_reasoning,
                )
                if slog:
                    ctx_tokens = sum(len(m["content"]) // 3 for m in messages)
                    slog.log_context_built(
                        num_messages=len(messages),
                        total_tokens=ctx_tokens,
                        style_hint=memory.last_style_hint,
                    )

                # Inject plugin context messages (crypto, search, etc.)
                for cm in pre.context_messages:
                    messages.insert(-1, cm)

                # Notify avatar: thinking (if plugins didn't already)
                if not pre.show_thinking:
                    avatar_queue.put({"type": "thinking_start"})

                # Stream response with sentence-level TTS
                stream_display = display.StreamingDisplay()
                sentence_buffer = SentenceBuffer()
                full_response: list[str] = []
                first_token = True

                # Sequential TTS worker — synthesizes sentences in order
                tts_queue: asyncio.Queue[str | None] = asyncio.Queue()
                tts_worker: asyncio.Task | None = None
                if tts.enabled:
                    tts_worker = asyncio.create_task(
                        _tts_sequential_worker(tts_queue, tts, audio_player)
                    )

                try:
                    stream_display.start()
                    async for token in chat_engine.get_response(messages):
                        # Strip emoji from output
                        token = _EMOJI_RE.sub("", token)
                        if not token:
                            continue

                        if first_token:
                            avatar_queue.put({"type": "thinking_end"})
                            avatar_queue.put({"type": "speaking_start"})
                            first_token = False

                        stream_display.token(token)
                        full_response.append(token)

                        # Sentence-level TTS (in-order via queue)
                        if tts.enabled:
                            sentences = sentence_buffer.add_token(token)
                            for sentence in sentences:
                                await tts_queue.put(sentence)
                                if slog:
                                    slog.log_tts(sentence, config.TTS_VOICE)

                    stream_display.end()
                    avatar_queue.put({"type": "speaking_end"})

                    # Flush remaining text to TTS
                    if tts.enabled:
                        remaining = sentence_buffer.flush()
                        if remaining:
                            await tts_queue.put(remaining)
                        # Signal worker to stop and wait for it
                        await tts_queue.put(None)
                        if tts_worker:
                            await tts_worker

                except Exception as e:
                    stream_display.end()
                    avatar_queue.put({"type": "thinking_end"})
                    if tts_worker and not tts_worker.done():
                        await tts_queue.put(None)
                        await tts_worker
                    display.show_error(f"Odpověď selhala: {e}")
                    if slog:
                        slog.log_error(str(e), "response_streaming")
                    current_messages.pop()
                    continue

                # Save assistant response and detect emotion
                response_text = "".join(full_response)
                current_messages.append({"role": "assistant", "content": response_text})

                # Emotion detection
                if config.EMOTION_DETECTION == "llm":
                    emotion = await detect_emotion_llm(response_text)
                else:
                    emotion = detect_emotion(response_text)
                avatar_queue.put({"type": "emotion", "value": emotion})

                memory.save_message("assistant", response_text, emotion=emotion)

                if slog:
                    slog.log_emotion_detected(emotion, config.EMOTION_DETECTION)
                    slog.log_assistant_message(
                        response_text, emotion=emotion,
                        tokens=len(response_text) // 3,
                    )

                # Real-time fact extraction (background, non-blocking)
                asyncio.create_task(
                    memory.extract_facts_realtime(user_input, response_text)
                )

                # Store in episodic memory (background, non-blocking)
                asyncio.create_task(
                    memory.store_episode(user_input, response_text)
                )

            elif action_type == "idle_trigger":
                # Suppress proactive messages while a plugin has active work
                if pm.any_active_task(ctx):
                    continue
                tier = payload.get("tier", 1)
                if slog:
                    slog.log_proactive(tier, "(generating...)")
                await proactive_response(
                    None, memory, tts, audio_player,
                    avatar_queue, current_messages, tier=tier,
                )

            elif action_type == "idle_shutdown":
                if pm.any_active_task(ctx):
                    continue
                farewell = SHUTDOWN_MESSAGE.format(user_name=memory.user_name)
                display.show_assistant(farewell)
                current_messages.append({"role": "assistant", "content": farewell})
                memory.save_message("assistant", farewell)
                await _speak(farewell, tts, audio_player, avatar_queue)
                if slog:
                    slog.log_proactive(3, farewell)
                break

            else:
                # Delegate all other events to plugins
                consumed = await pm.handle_event(ctx, payload if isinstance(payload, dict) else {"type": action_type})
                if consumed:
                    idle_monitor.reset()

    finally:
        if ongoing_input_task is not None and not ongoing_input_task.done():
            ongoing_input_task.cancel()
        avatar_queue.put({"type": "quit"})
        # Shutdown plugins (cancels background tasks, closes resources)
        await pm.shutdown_all(ctx)
        # Cleanup idle monitor
        idle_monitor.stop()
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


async def _speak(
    text: str,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
) -> None:
    """Synthesize and enqueue a complete text for TTS."""
    if tts.enabled:
        path = await tts.synthesize(text)
        if path:
            audio_player.enqueue(path)


async def _tts_sequential_worker(
    sentence_queue: asyncio.Queue,
    tts: TTSEngine,
    audio_player: AudioPlayer,
) -> None:
    """Sequential TTS worker — synthesizes sentences in order.

    Reads sentences from the queue one by one and enqueues audio
    in the same order, preventing out-of-order playback.
    Stops when it receives None as sentinel.
    """
    while True:
        sentence = await sentence_queue.get()
        if sentence is None:
            break
        path = await tts.synthesize(sentence)
        if path:
            audio_player.enqueue(path)


# ── Entry point ────────────────────────────────────────────────────


def main() -> None:
    """Entry point: start Pygame in main thread, chat in daemon thread."""
    # Create thread-safe queues
    avatar_queue: queue.Queue = queue.Queue()

    # Create audio player
    audio_player = AudioPlayer(avatar_queue=avatar_queue)

    # Start chat in daemon thread
    chat = threading.Thread(
        target=chat_thread_main,
        args=(avatar_queue, audio_player),
        daemon=True,
    )
    chat.start()

    # Run Pygame avatar in main thread (REQUIRED on macOS)
    from avatar.window import avatar_main

    try:
        avatar_main(avatar_queue, audio_player)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_temp_files()


if __name__ == "__main__":
    main()
