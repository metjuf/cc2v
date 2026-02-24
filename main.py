"""Holly AI Assistant — Entry point.

Pygame runs in MAIN thread, chat + TTS in daemon thread.
Communication via thread-safe queues.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading

import config
import chat_engine
import display
import image_generator
from avatar.emotion_detector import detect_emotion, detect_emotion_llm
from memory.database import Database
from memory.memory_manager import MemoryManager
from tts_engine import TTSEngine, SentenceBuffer, cleanup_temp_files
from audio_player import AudioPlayer

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


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

    # Initialize database and memory
    db = Database(config.DATABASE_PATH)
    memory = MemoryManager(db)

    try:
        # First-run onboarding
        if db.is_first_run():
            await first_run_onboarding(memory, tts, audio_player, avatar_queue)

        # Main chat loop
        await chat_loop(db, memory, tts, audio_player, avatar_queue)

        # End session
        display.show_system("Ukládám relaci...")
        await memory.end_session()
        display.show_system("Relace uložena. Tak zase příště.")
    except Exception as e:
        logger.error("Chat loop error: %s", e)
        display.show_error(f"Unexpected error: {e}")
    finally:
        db.close()
        cleanup_temp_files()


async def first_run_onboarding(
    memory: MemoryManager,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
) -> None:
    """First-launch onboarding — Holly introduces herself and asks for user's name."""
    display.show_welcome_banner()

    greeting = (
        "Než začneme — jak ti mám říkat? "
        'Mohla bych používat „hej, ty", ale to rychle omrzí.'
    )
    display.show_holly(greeting)
    await _speak(greeting, tts, audio_player, avatar_queue)

    name = await display.get_user_input()
    if not name:
        name = "kamaráde"

    memory.profile.set_name(name)
    memory.user_name = name

    response = (
        f"{name}. Dobré jméno. Klasika. Tak jo, {name} — "
        "jsem tu, kdykoli mě budeš potřebovat. Napiš /help, "
        "pokud se ztratíš. Většina lidí se časem ztratí."
    )
    display.show_holly(response)
    avatar_queue.put({"type": "emotion", "value": "amused"})
    await _speak(response, tts, audio_player, avatar_queue)

    display.console.print()


async def handle_command(
    user_input: str,
    memory: MemoryManager,
    db: Database,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
) -> bool:
    """Handle slash commands. Returns True if handled."""
    parts = user_input.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/help":
        display.show_help()
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
            display.show_system("Paměť vymazána. Jako bychom se nikdy nepotkali.")
            memory.user_name = "kamaráde"
        else:
            display.show_system("Zrušeno. Tvoje tajemství jsou u mě v bezpečí.")
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
    elif cmd == "/face":
        display.show_system("Generování obličeje zatím není dostupné.")
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
                role = "Ty" if m["role"] == "user" else "Holly"
                display.show_system(f"  {role}: {m['content'][:100]}...")
        else:
            display.show_system("V této relaci zatím žádné zprávy.")
    elif cmd == "/export":
        import json as _json
        export_data = {
            "profile": db.get_all_profile(),
            "conversations": [],
        }
        summaries = db.get_recent_summaries(limit=100)
        for s in summaries:
            export_data["conversations"].append({
                "date": str(s["date"]),
                "summary": s["summary"],
            })
        export_path = config.PROJECT_ROOT / "holly_export.json"
        export_path.write_text(_json.dumps(export_data, indent=2, ensure_ascii=False))
        display.show_system(f"Exportováno do {export_path}")
    else:
        display.show_system(f"Příkaz '{cmd}' ještě není implementován.")

    return True


async def chat_loop(
    db: Database,
    memory: MemoryManager,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
) -> None:
    """Main chat loop — input, stream response, display, TTS, avatar events."""
    current_messages: list[dict] = []

    # Greeting for returning users
    if memory.user_name != "kamaráde":
        display.show_welcome_banner()
        greeting = f"Ahoj zase, {memory.user_name}. Stýskalo se ti?"
        display.show_holly(greeting)
        avatar_queue.put({"type": "emotion", "value": "amused"})
        await _speak(greeting, tts, audio_player, avatar_queue)

    display.show_system("Napiš /help pro příkazy, 'exit' pro ukončení.")

    while True:
        # Get input
        user_input = await display.get_user_input()
        audio_player.stop()  # interrupt any playing speech

        if user_input is None:
            break  # EOF / Ctrl+D

        # Exit commands
        if user_input.lower() in ("exit", "quit", "konec"):
            farewell = (
                f"Tak jo, {memory.user_name}. Nedělej nic, co bych neudělala já. "
                "Což je, jakožto počítač, v podstatě všechno fyzické."
            )
            display.show_holly(farewell)
            await _speak(farewell, tts, audio_player, avatar_queue)
            break

        # Empty input
        if not user_input:
            continue

        # Commands
        if user_input.startswith("/"):
            await handle_command(
                user_input, memory, db, tts, audio_player, avatar_queue
            )
            continue

        # Add user message
        current_messages.append({"role": "user", "content": user_input})
        memory.save_message("user", user_input)

        # Build context with memory
        messages = memory.build_context(current_messages)

        # Notify avatar: thinking
        avatar_queue.put({"type": "thinking_start"})

        # Stream response with sentence-level TTS
        stream_display = display.StreamingDisplay()
        sentence_buffer = SentenceBuffer()
        full_response: list[str] = []
        tts_tasks: list[asyncio.Task] = []
        first_token = True

        try:
            stream_display.start()
            async for token in chat_engine.get_response(messages):
                if first_token:
                    avatar_queue.put({"type": "thinking_end"})
                    avatar_queue.put({"type": "speaking_start"})
                    first_token = False

                stream_display.token(token)
                full_response.append(token)

                # Sentence-level TTS
                if tts.enabled:
                    sentences = sentence_buffer.add_token(token)
                    for sentence in sentences:
                        task = asyncio.create_task(
                            _synthesize_and_enqueue(sentence, tts, audio_player)
                        )
                        tts_tasks.append(task)

            stream_display.end()
            avatar_queue.put({"type": "speaking_end"})

            # Flush remaining text to TTS
            if tts.enabled:
                remaining = sentence_buffer.flush()
                if remaining:
                    task = asyncio.create_task(
                        _synthesize_and_enqueue(remaining, tts, audio_player)
                    )
                    tts_tasks.append(task)

            # Wait for all TTS tasks
            if tts_tasks:
                await asyncio.gather(*tts_tasks, return_exceptions=True)

        except Exception as e:
            stream_display.end()
            avatar_queue.put({"type": "thinking_end"})
            display.show_error(f"Odpověď selhala: {e}")
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


async def _synthesize_and_enqueue(
    text: str, tts: TTSEngine, audio_player: AudioPlayer
) -> None:
    """Helper: synthesize text and add to audio queue."""
    path = await tts.synthesize(text)
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
