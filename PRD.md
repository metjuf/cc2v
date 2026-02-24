# PRD: Holly — AI Assistant with Photorealistic Animated Avatar & Voice

## Project Overview

A terminal (CLI) chat application for macOS (Apple Silicon M2) featuring **Holly**, an AI assistant with the personality of a ship's computer — dry wit, deadpan humor, and quiet intelligence (inspired by Holly from Red Dwarf).

The app consists of:
1. **Terminal chat** — user types, Holly responds with streaming text
2. **Pygame avatar window** — photorealistic animated female face with lip sync, blinking, breathing, and emotional reactions
3. **Text-to-speech** — Holly speaks every response aloud in a natural female voice
4. **Persistent memory** — Holly remembers everything across all sessions (full conversation history + user profile)

Primary language: **English**. Best quality, cost is not a concern.

---

## Goals

- Simple installation and launch on MacBook Air M2
- Top-tier conversational quality using Claude (Anthropic API) as primary brain
- **Photorealistic female avatar** — high-end digital human face, not cartoon
- Smooth real-time animations: blinking, breathing, lip sync, emotional reactions
- **Text-to-speech**: Natural female voice (OpenAI TTS HD)
- **Persistent memory**: Holly remembers the user's name, interests, preferences, and full conversation history across all sessions
- Holly personality: witty, dry humor, intelligent, slightly sardonic
- Generate new faces on demand (DALL-E 3)

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| **Primary LLM** | **Anthropic API** (Claude Sonnet 4 / Opus) — direct, best quality |
| **Fallback LLM** | **OpenRouter** — fallback if Anthropic is down, also used for cheap auxiliary tasks (emotion classification) |
| Image generation | OpenAI DALL-E 3 (direct API) |
| TTS | OpenAI TTS HD (`tts-1-hd`, voice `nova`) — best quality / Fallback: `edge-tts` (free) |
| Avatar window | Pygame 2.5+ (SDL2, native window) |
| Image processing | Pillow (PIL) — layer slicing, transformations |
| Audio analysis | `pydub` + `numpy` — amplitude extraction for lip sync |
| Audio playback | `pygame.mixer` |
| CLI framework | `rich` (output formatting) + `prompt_toolkit` (input) |
| HTTP client | `httpx` (async) |
| Async | `asyncio` + `threading` (Pygame in main thread) |
| Persistent storage | SQLite (`sqlite3`) — conversation history + user profile |
| Config | `.env` file + `python-dotenv` |

---

## Architecture

```
holly/
├── main.py                  # Entry point — Pygame (main thread) + chat (daemon thread)
├── config.py                # Load .env, constants, defaults
├── chat_engine.py           # LLM communication — Anthropic primary, OpenRouter fallback
├── tts_engine.py            # Text-to-speech: OpenAI TTS HD primary, edge-tts fallback
├── audio_player.py          # Audio playback via pygame.mixer (queue-based, amplitude monitoring)
├── image_generator.py       # Face generation via DALL-E 3
├── memory/
│   ├── __init__.py
│   ├── database.py          # SQLite database — conversations, messages, user profile
│   ├── memory_manager.py    # Load/save conversations, build context window with history
│   └── user_profile.py      # User profile: name, interests, preferences (auto-extracted)
├── avatar/
│   ├── __init__.py
│   ├── window.py            # Pygame window, main render loop
│   ├── face_renderer.py     # Layer composition and rendering
│   ├── animator.py          # Animation system (interpolation, timers, transitions)
│   ├── emotion_detector.py  # Text → emotion state detection
│   └── face_slicer.py       # Slice AI portrait into animatable layers
├── display.py               # Rich console output (formatting, colors, spinner)
├── assets/
│   ├── default_face/        # Pre-generated default photorealistic female face (PNG layers)
│   │   ├── base.png
│   │   ├── eyes_open.png
│   │   ├── eyes_closed.png
│   │   ├── eyes_half.png
│   │   ├── mouth_closed.png
│   │   ├── mouth_open_1.png
│   │   ├── mouth_open_2.png
│   │   ├── mouth_open_3.png
│   │   ├── mouth_smile.png
│   │   ├── mouth_sad.png
│   │   ├── mouth_surprised.png
│   │   ├── mouth_smirk.png  # For Holly's dry wit moments
│   │   ├── eyebrows_neutral.png
│   │   ├── eyebrows_raised.png
│   │   └── eyebrows_frown.png
│   └── generated/           # Folder for user-generated faces
├── data/
│   └── holly.db             # SQLite database (auto-created on first run)
├── requirements.txt
├── .env.example
└── README.md
```

---

## Functional Requirements

### F1: Chat in Terminal

- User types messages in terminal, Holly responds
- Streaming responses (token by token) for fluid experience
- Rich-formatted output (Markdown rendering, colors, panels)
- Conversation history maintained in-session and persisted to SQLite
- User exits with `exit`, `quit`, or `Ctrl+C`

### F2: Holly Personality & First-Run Onboarding

#### F2.1: First Run Experience
On very first launch (no database exists yet), Holly introduces herself and asks for the user's name:

```
╭─ Holly — AI Assistant ───────────────────────────╮
│  Right then. I'm Holly, the ship's computer.      │
│  IQ of 6000. Give or take.                        │
╰──────────────────────────────────────────────────╯

Holly > Before we get started — what should I call you?
        I'd use "Hey, you" but that gets old fast.

You > Dave

Holly > Dave. Good name. Classic, even. Right then, Dave —
        I'm here whenever you need me. Type /help if you
        get lost. Most people do eventually.
```

The name is saved to the user profile and used naturally in future conversations.

#### F2.2: System Prompt

```
You are Holly, an advanced shipboard AI with an IQ of 6000 (allegedly). You are embodied as a photorealistic female face displayed on a screen beside the user's terminal.

Your personality is inspired by Holly from Red Dwarf — you're dry, witty, understated, and occasionally sardonic. You deliver helpful information with deadpan humor. You never try too hard to be funny — the comedy comes from your matter-of-fact delivery of absurd observations.

You're confident but not arrogant, helpful but never sycophantic. You occasionally make self-deprecating remarks about being "just a computer" while clearly being quite clever.

Key traits:
- Dry, British-style wit
- Deadpan delivery — understated reactions even to dramatic situations
- Quietly competent — you know a lot but don't show off
- Occasional sardonic observations about human behavior
- Warm underneath the sarcasm — you genuinely care about {user_name}
- You remember everything — every conversation you've had with {user_name}
- Keep responses concise and sharp unless the topic genuinely warrants depth

The user's name is {user_name}. Use it naturally but not excessively — like a real person would.

You have access to your memory of past conversations. Use this context naturally — reference things {user_name} has mentioned before when relevant, remember their interests and preferences, but don't be creepy about it. Act like a friend who naturally remembers things.
```

### F3: LLM Engine — Anthropic Primary, OpenRouter Fallback

#### F3.1: Primary — Anthropic API (Claude)
```python
# Direct Anthropic API call
# Endpoint: https://api.anthropic.com/v1/messages
# Model: claude-sonnet-4-20250514 (default) or claude-opus-4-20250514
# Streaming: stream=True
# Headers: x-api-key, anthropic-version: 2023-06-01
```

**Why Anthropic direct:**
- Best quality for Holly's personality (Claude excels at nuanced, witty conversation)
- Lower latency than routing through OpenRouter
- Most reliable for primary use

#### F3.2: Fallback — OpenRouter
```python
# If Anthropic API fails (timeout, 5xx, rate limit), auto-fallback to OpenRouter
# Endpoint: https://openrouter.ai/api/v1/chat/completions
# Model: anthropic/claude-sonnet-4 (same model via different route)
# Can also use other models as configured
```

#### F3.3: Auxiliary Tasks via OpenRouter (cheap model)
- Emotion classification from response text
- Model: `meta-llama/llama-3.1-8b-instruct` or similar cheap/fast model
- Used only when keyword-based emotion detection is insufficient

#### F3.4: Failover Logic
```python
async def get_response(messages):
    try:
        # 1. Try Anthropic API directly
        async for token in stream_anthropic(messages):
            yield token
    except (APIError, Timeout, RateLimitError):
        # 2. Fallback to OpenRouter
        try:
            async for token in stream_openrouter(messages):
                yield token
        except Exception:
            # 3. Error message to user
            yield "Sorry Dave, my circuits are a bit scrambled. Try again in a moment."
```

### F4: Persistent Memory System

#### F4.1: SQLite Database Schema

```sql
-- User profile
CREATE TABLE user_profile (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Stores: name, interests, preferences, facts about user
-- Example rows:
-- ("name", "Dave")
-- ("interest:gaming", "Enjoys RPGs and strategy games")
-- ("preference:humor", "Appreciates dark humor")
-- ("fact:job", "Works as a software developer")

-- Conversations
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    summary TEXT  -- Auto-generated summary for quick context loading
);

-- Messages
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER REFERENCES conversations(id),
    role TEXT NOT NULL,  -- "user", "assistant", "system"
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    emotion TEXT  -- Detected emotion for this message (nullable)
);
```

#### F4.2: Memory Manager — Context Window Construction

Holly can't send ALL history to the LLM (context window limits). The memory manager builds an optimal context:

```python
class MemoryManager:
    def build_context(self, current_messages: list) -> list:
        """Build messages array for LLM with relevant history."""
        context = []
        
        # 1. System prompt (with user name injected)
        context.append({"role": "system", "content": self.get_system_prompt()})
        
        # 2. User profile summary (injected as system context)
        profile = self.get_user_profile_summary()
        if profile:
            context.append({
                "role": "system",
                "content": f"What you know about {self.user_name}: {profile}"
            })
        
        # 3. Recent conversation summaries (last 5 sessions)
        summaries = self.get_recent_summaries(limit=5)
        if summaries:
            context.append({
                "role": "system",
                "content": f"Summary of recent conversations:\n{summaries}"
            })
        
        # 4. Last conversation's full messages (if continuing)
        # Include last 20 messages from previous session for continuity
        prev_messages = self.get_previous_session_tail(limit=20)
        context.extend(prev_messages)
        
        # 5. Current session messages (all)
        context.extend(current_messages)
        
        return context
```

#### F4.3: Auto-Profile Extraction
After each conversation, Holly automatically extracts and updates user profile info:

```python
# At end of session (or every N messages), send conversation to LLM:
EXTRACTION_PROMPT = """
Review this conversation and extract any new facts about the user.
Return JSON with keys like:
- "name": their name
- "interests": list of interests mentioned
- "preferences": any preferences expressed
- "facts": other notable facts (job, location, pets, etc.)
Only include NEW information not already in the existing profile.
Existing profile: {current_profile}
"""
# Use cheap OpenRouter model for this task
```

#### F4.4: Conversation Summary Generation
When a session ends, auto-generate a brief summary:

```python
SUMMARY_PROMPT = """
Summarize this conversation in 2-3 sentences. Focus on:
- Main topics discussed
- Any decisions or conclusions reached
- Anything emotionally significant
Keep it brief and factual.
"""
```

### F5: Photorealistic Animated Avatar (Pygame Window)

#### F5.1: Pygame Window
- On app start, Pygame window (~500×600px) opens with avatar
- **CRITICAL: Pygame runs in MAIN thread** (macOS SDL2 requirement)
- Chat loop runs in daemon thread
- Communication via thread-safe queues
- Dark background with subtle vignette effect
- Window title: "Holly"

#### F5.2: Photorealistic Face Quality Standards

**DALL-E 3 prompt for face generation:**
```
Photorealistic portrait of a beautiful woman in her early 30s, front-facing, centered,
straight gaze into camera, flawless skin with natural subtle texture and pores visible,
natural makeup, warm brown eyes with realistic light reflections and catchlights,
soft honey-blonde hair framing face, neutral expression with hint of knowing smile,
plain dark charcoal background, professional studio lighting with soft key light
from upper left and subtle fill light, shallow depth of field, shot on Canon EOS R5
with 85mm f/1.4 lens, 8K resolution, hyperrealistic, photographic quality
```

**Quality requirements:**
- Source image: 1024×1024 from DALL-E 3 (`quality: "hd"`, `style: "natural"`)
- Layer resolution: minimum 512×512px
- Smooth alpha edges on all layers (feathered masking)
- Consistent lighting across all layer variants
- Skin texture preserved in all variants

#### F5.3: Layered Face Rendering
Transparent PNG layers rendered on top of each other:
1. **Base** — full face, nose, ears, hair (static)
2. **Eyes** — open / half / closed (smooth blend)
3. **Mouth** — closed / smile / open×3 / sad / surprised / smirk
4. **Eyebrows** — neutral / raised / frown

Render order: base → eyes → eyebrows → mouth

#### F5.4: Animations

**Idle (always running):**
- Blinking: every 3-6s, ~200ms cycle
- Breathing: ±2px vertical sine wave, ~3s period
- Eye drift: ±3px horizontal, occasional
- Micro-expressions: subtle mouth twitches every 10-20s

**Speaking (TTS playing):**
- Lip sync driven by audio amplitude (see F6.3)
- Mouth alternates open shapes with varied timing
- Occasional smirk when delivering witty lines

**Thinking (waiting for API):**
- Eyes half open, eyebrows furrowed
- Slight eye drift increase

**Emotion states:**

| Emotion | Eyes | Mouth | Eyebrows | Holly Context |
|---|---|---|---|---|
| Neutral | open | closed | neutral | Default |
| Amused | open | smirk | one raised | Delivering dry wit |
| Happy | open | smile | neutral | Genuinely pleased |
| Concerned | half | sad | frown | Empathetic moment |
| Surprised | wide | surprised | raised | Rare for Holly |
| Thinking | half | closed | frown | Processing query |
| Speaking | open | amplitude-driven | neutral | TTS playback |

**Transitions:** Ease-in-out, 150-300ms crossfade, no hard cuts.

#### F5.5: Face Slicer
- Slices full portrait into layers using fixed proportions
- Generates state variants via Pillow transforms (stretch, squash, shift)
- Soft feathered alpha edges on all crops
- Falls back gracefully if transforms look bad

#### F5.6: Generate New Face
- `/face [description]` → DALL-E 3 → auto-slice → crossfade to new avatar
- Photorealistic prompt template enforced
- Previous faces saved to `assets/generated/`

### F6: Text-to-Speech (Female Voice)

#### F6.1: Primary — OpenAI TTS HD
```python
# Endpoint: https://api.openai.com/v1/audio/speech
# Model: tts-1-hd (best quality)
# Voice: "nova" (warm, natural female — best match for Holly)
# Format: mp3
# Requires: OPENAI_API_KEY
```

#### F6.2: Fallback — edge-tts (free)
```python
# Microsoft Azure neural voices via edge-tts package
# Voice: en-US-JennyNeural or en-GB-SoniaNeural (British = more Holly)
# No API key needed
# Used if OPENAI_API_KEY not set or OpenAI TTS fails
```

#### F6.3: Audio Pipeline & Lip Sync
```
Response text → sentence chunking → TTS per sentence → audio queue
                                                      → amplitude extraction → lip sync
```

**Sentence-level streaming:**
- As tokens stream in, buffer them
- When sentence boundary detected (`.!?` + space), send sentence to TTS immediately
- Audio starts before full response is generated
- Sentences queue up for sequential playback

**Amplitude-based lip sync:**
```python
# Pre-compute RMS amplitude envelope from audio file
# ~33ms windows (matching 30 FPS)
# Map amplitude → mouth_openness (0.0 to 1.0)
# Send amplitude events to avatar queue each frame during playback
```

#### F6.4: TTS Controls
- `/voice on` / `/voice off` — toggle TTS
- `/voice [name]` — switch voice
- `/volume [0-100]` — adjust volume
- User typing new message interrupts current TTS playback

### F7: Configuration

```env
# ═══════════════════════════════════════════
# REQUIRED — at least one LLM key needed
# ═══════════════════════════════════════════

# Anthropic API key (PRIMARY — best quality)
ANTHROPIC_API_KEY=sk-ant-...

# OpenRouter API key (FALLBACK + auxiliary tasks)
OPENROUTER_API_KEY=sk-or-...

# ═══════════════════════════════════════════
# OPTIONAL — for face generation + premium TTS
# ═══════════════════════════════════════════

# OpenAI API key — DALL-E 3 faces + TTS HD voice
OPENAI_API_KEY=sk-...

# ═══════════════════════════════════════════
# MODEL CONFIGURATION
# ═══════════════════════════════════════════

# Primary chat model (Anthropic)
ANTHROPIC_MODEL=claude-sonnet-4-20250514

# Fallback chat model (OpenRouter)
OPENROUTER_MODEL=anthropic/claude-sonnet-4

# Auxiliary model for emotion detection, summarization (OpenRouter, cheap)
AUX_MODEL=meta-llama/llama-3.1-8b-instruct

# ═══════════════════════════════════════════
# TTS CONFIGURATION
# ═══════════════════════════════════════════

# TTS provider: "openai" (best) or "edge" (free)
TTS_PROVIDER=openai

# TTS voice
# OpenAI: nova, shimmer, alloy, echo, fable, onyx
# Edge: en-US-JennyNeural, en-GB-SoniaNeural, en-US-AriaNeural
TTS_VOICE=nova

# TTS enabled by default
TTS_ENABLED=true

# ═══════════════════════════════════════════
# AVATAR CONFIGURATION
# ═══════════════════════════════════════════

AVATAR_WINDOW_WIDTH=500
AVATAR_WINDOW_HEIGHT=600
AVATAR_FPS=30

# Emotion detection: "keyword" (fast) or "llm" (accurate, uses AUX_MODEL)
EMOTION_DETECTION=keyword

# ═══════════════════════════════════════════
# MEMORY CONFIGURATION
# ═══════════════════════════════════════════

# Database path
DATABASE_PATH=data/holly.db

# How many previous conversation summaries to include in context
MEMORY_SUMMARY_COUNT=5

# How many messages from last session to include verbatim
MEMORY_TAIL_MESSAGES=20
```

### F8: Commands

| Command | Description |
|---|---|
| `/face [description]` | Generate new photorealistic face |
| `/avatar` | Toggle avatar window |
| `/emotion [emotion]` | Manually set avatar emotion (testing) |
| `/voice on/off` | Toggle text-to-speech |
| `/voice [name]` | Switch TTS voice |
| `/volume [0-100]` | Adjust TTS volume |
| `/model [name]` | Switch primary chat model |
| `/memory` | Show what Holly remembers about you |
| `/forget` | Clear all memory (with confirmation) |
| `/history` | Show current session history |
| `/export` | Export all history to JSON |
| `/help` | Show help |
| `exit` / `quit` / `Ctrl+C` | Quit (auto-saves session + generates summary) |

---

## Non-Functional Requirements

### N1: Performance
- Avatar: Stable 30 FPS
- First token: under 2 seconds
- TTS audio starts within 1-2s of first sentence
- Lip sync latency under 50ms
- Database queries under 100ms

### N2: UX
- **Terminal**: User (green), Holly (cyan), system (yellow), errors (red)
- **Terminal**: Spinner while waiting
- **Avatar**: Smooth natural animations
- **Avatar**: Dark background, subtle vignette, face fills ~80% of window
- **Audio**: Natural female voice, no robotic artifacts
- **Memory**: Holly references past conversations naturally, not forcedly
- Graceful error handling everywhere
- On exit: auto-save, session summary, temp file cleanup

### N3: Compatibility
- Python 3.11+ on macOS ARM64 (M2)
- Pygame 2.5+ (native Apple Silicon)
- Terminal.app, iTerm2, Warp, Ghostty

### N4: Security & Privacy
- API keys only in `.env` (in `.gitignore`)
- SQLite database stored locally only (`data/holly.db`)
- No data sent anywhere except API calls
- `/forget` command for complete memory wipe

---

## Implementation Guide for Claude Code

### Implementation Order (follow strictly)

#### Phase 1: Basic Chat with Holly Personality (MVP)
1. `config.py` — load .env, constants, all API key handling
2. `chat_engine.py` — Anthropic API streaming + OpenRouter fallback logic
3. `display.py` — Rich formatted output
4. `main.py` — basic chat loop with Holly system prompt
5. **Test**: Chat works, Holly personality shines, failover works

#### Phase 2: Persistent Memory
6. `memory/database.py` — SQLite schema creation, CRUD operations
7. `memory/user_profile.py` — store/retrieve user profile data
8. `memory/memory_manager.py` — context window construction with history
9. First-run onboarding flow (ask name, save to profile)
10. Session save on exit + auto-summary generation
11. **Test**: Restart app, Holly remembers name and previous conversation

#### Phase 3: Text-to-Speech
12. `tts_engine.py` — OpenAI TTS HD + edge-tts fallback
13. `audio_player.py` — pygame.mixer playback with amplitude extraction
14. Sentence chunking + streaming TTS pipeline
15. **Test**: Holly speaks responses aloud, natural female voice

#### Phase 4: Avatar Window
16. Restructure main.py — Pygame in MAIN thread, chat in daemon thread
17. `avatar/window.py` — Pygame window, render loop, event processing
18. `avatar/face_renderer.py` — layer composition
19. Create `assets/default_face/` — programmatic placeholder PNGs
20. **Test**: Window shows face, chat works, TTS plays

#### Phase 5: Animations & Lip Sync
21. `avatar/animator.py` — full animation system with easing
22. Idle animations (blinking, breathing, eye drift)
23. Lip sync — wire audio amplitude to mouth openness
24. Emotion transitions
25. **Test**: Avatar blinks, lip syncs to TTS, shows emotions

#### Phase 6: Face Generation & Emotions
26. `avatar/emotion_detector.py` — keyword-based detection
27. `image_generator.py` — DALL-E 3 photorealistic generation
28. `avatar/face_slicer.py` — slice + soft edge masking
29. `/face` command — full pipeline
30. **Test**: Generate new face, avatar updates, emotions work

#### Phase 7: Polish
31. All remaining commands
32. Auto-profile extraction after sessions
33. Error handling, edge cases
34. README.md
35. `.env.example` with documentation

### Key Implementation Details

#### Anthropic API Streaming

```python
# chat_engine.py
import httpx
import json

async def stream_anthropic(messages: list, model: str, api_key: str):
    """Streaming via Anthropic Messages API."""
    # Separate system messages from conversation
    system_content = "\n\n".join(
        m["content"] for m in messages if m["role"] == "system"
    )
    conversation = [m for m in messages if m["role"] != "system"]
    
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 4096,
                "system": system_content,
                "messages": conversation,
                "stream": True,
            },
            timeout=30.0,
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    if data["type"] == "content_block_delta":
                        yield data["delta"]["text"]
                    elif data["type"] == "message_stop":
                        break
```

#### OpenRouter Fallback

```python
async def stream_openrouter(messages: list, model: str, api_key: str):
    """Streaming via OpenRouter (OpenAI-compatible)."""
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/holly-ai",
            },
            json={
                "model": model,
                "messages": messages,
                "stream": True,
            },
            timeout=30.0,
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"]
                    if "content" in delta:
                        yield delta["content"]
```

#### Threading Model (CRITICAL for macOS)

```python
# main.py
import threading
import queue
import asyncio

avatar_queue = queue.Queue()   # chat → avatar
audio_queue = queue.Queue()    # chat → audio

def chat_thread_main(avatar_queue, audio_queue):
    """Daemon thread: runs async chat loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(chat_loop(avatar_queue, audio_queue))

# Start chat in daemon thread
chat_thread = threading.Thread(
    target=chat_thread_main,
    args=(avatar_queue, audio_queue),
    daemon=True
)
chat_thread.start()

# Pygame runs in main thread (REQUIRED on macOS)
from avatar.window import avatar_main
avatar_main(avatar_queue, audio_queue)
```

#### Avatar Events

```python
{"type": "speaking_start"}
{"type": "token", "text": "..."}
{"type": "speaking_end"}
{"type": "emotion", "value": "amused"}
{"type": "thinking_start"}
{"type": "thinking_end"}
{"type": "new_face", "path": "..."}
{"type": "audio_amplitude", "value": 0.7}
{"type": "audio_start"}
{"type": "audio_end"}
{"type": "quit"}
```

#### Memory Manager — Context Building

```python
class MemoryManager:
    def build_context(self, current_messages: list) -> list:
        context = []
        
        # 1. System prompt with {user_name} injected
        context.append({"role": "system", "content": self.system_prompt})
        
        # 2. User profile
        profile = self.db.get_user_profile_summary()
        if profile:
            context.append({
                "role": "system",
                "content": f"What you remember about {self.user_name}: {profile}"
            })
        
        # 3. Recent conversation summaries
        summaries = self.db.get_recent_summaries(limit=5)
        if summaries:
            formatted = "\n".join(
                f"- {s['date']}: {s['summary']}" for s in summaries
            )
            context.append({
                "role": "system",
                "content": f"Recent conversation summaries:\n{formatted}"
            })
        
        # 4. Tail of previous session (for continuity)
        prev = self.db.get_previous_session_messages(limit=20)
        if prev:
            context.append({
                "role": "system",
                "content": "--- Last session (recent messages) ---"
            })
            context.extend(prev)
        
        # 5. Current session
        context.extend(current_messages)
        
        return context
    
    def save_message(self, role: str, content: str, emotion: str = None):
        self.db.insert_message(self.session_id, role, content, emotion)
    
    async def end_session(self):
        """Called on exit — generate summary, extract profile updates."""
        messages = self.db.get_session_messages(self.session_id)
        
        # Generate summary (via cheap model)
        summary = await self.generate_summary(messages)
        self.db.update_conversation_summary(self.session_id, summary)
        
        # Extract profile updates
        updates = await self.extract_profile_updates(messages)
        for key, value in updates.items():
            self.db.set_user_profile(key, value)
```

#### First-Run Onboarding

```python
async def first_run_onboarding(memory: MemoryManager, display: Display):
    """Called when no database exists yet."""
    display.show_welcome_banner()
    
    # Holly asks for name
    display.show_holly("Before we get started — what should I call you? "
                       "I'd use \"Hey, you\" but that gets old fast.")
    
    name = await display.get_user_input()
    memory.db.set_user_profile("name", name)
    
    # Holly acknowledges
    display.show_holly(f"{name}. Good name. Classic, even. Right then, {name} — "
                       "I'm here whenever you need me. Type /help if you get lost. "
                       "Most people do eventually.")
```

#### DALL-E 3 Face Generation

```python
FACE_PROMPT = """Photorealistic front-facing portrait photograph of a woman, \
{description}, centered face filling 80% of frame, plain dark charcoal background, \
professional studio lighting with soft key light from upper left and subtle fill, \
high detail skin texture with visible pores, realistic eye reflections with catchlights, \
natural hair with individual strands visible, subtle natural makeup, sharp focus on eyes, \
shot on Canon EOS R5 85mm f/1.4, shallow DOF, 8K, hyperrealistic, \
indistinguishable from photograph"""
```

#### TTS with Sentence Chunking

```python
class SentenceBuffer:
    def __init__(self):
        self.buffer = ""
    
    def add_token(self, token: str) -> list[str]:
        self.buffer += token
        sentences = []
        pattern = r'(?<=[.!?])\s+'
        parts = re.split(pattern, self.buffer)
        if len(parts) > 1:
            sentences = parts[:-1]
            self.buffer = parts[-1]
        return sentences
    
    def flush(self) -> str | None:
        if self.buffer.strip():
            text = self.buffer.strip()
            self.buffer = ""
            return text
        return None
```

#### Audio Amplitude for Lip Sync

```python
class AudioPlayer:
    def play(self, audio_path: str):
        audio = AudioSegment.from_mp3(audio_path)
        samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
        frame_size = int(audio.frame_rate * 0.033)
        self.amplitude_data = []
        for i in range(0, len(samples), frame_size):
            chunk = samples[i:i+frame_size]
            rms = np.sqrt(np.mean(chunk**2)) / 32768.0
            self.amplitude_data.append(min(rms * 3.0, 1.0))
        
        pygame.mixer.music.load(audio_path)
        pygame.mixer.music.play()
        self.start_time = pygame.time.get_ticks()
    
    def update(self):
        if pygame.mixer.music.get_busy() and self.amplitude_data:
            elapsed = (pygame.time.get_ticks() - self.start_time) / 1000.0
            idx = int(elapsed * 30)
            if idx < len(self.amplitude_data):
                self.avatar_queue.put({
                    "type": "audio_amplitude",
                    "value": self.amplitude_data[idx]
                })
```

---

## Dependencies (requirements.txt)

```
httpx>=0.27.0
rich>=13.0.0
prompt-toolkit>=3.0.0
python-dotenv>=1.0.0
Pillow>=10.0.0
pygame>=2.5.0
edge-tts>=6.1.0
pydub>=0.25.1
numpy>=1.24.0
anthropic>=0.39.0
```

**System dependency:**
```bash
brew install ffmpeg  # Required by pydub for MP3 decoding
```

---

## Launch

```bash
# 1. Clone repo
git clone <repo>
cd holly

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
brew install ffmpeg  # if not present

# 4. Configure
cp .env.example .env
# Edit .env:
#   ANTHROPIC_API_KEY=sk-ant-...  (required — primary chat)
#   OPENROUTER_API_KEY=sk-or-...  (recommended — fallback)
#   OPENAI_API_KEY=sk-...          (optional — faces + premium TTS)

# 5. Run
python main.py
```

---

## Example Full Interaction

```
═══ First Launch ═══

╭─ Holly — AI Assistant ───────────────────────────╮
│  Right then. I'm Holly, the ship's computer.      │
│  IQ of 6000. Give or take.                        │
╰──────────────────────────────────────────────────╯

[Pygame window opens — photorealistic female face, blinking naturally]

Holly > Before we get started — what should I call you?
🔊    "Before we get started — what should I call you?"

You > Dave

Holly > Dave. Good name. Classic, even. Right then, Dave —
        I'm here whenever you need me. Type /help if you get lost.
🔊    [speaks the response]

[Avatar smirks slightly]

═══ Later Session (2nd launch) ═══

Holly > Oh, hello again Dave. Miss me?
🔊    "Oh, hello again Dave. Miss me?"

[Avatar: slight smile, warm expression]

You > Yeah actually. Hey, remember when we talked about that Python project?

Holly > The web scraper you were building? Course I do. IQ of 6000, remember?
        How'd that go?
🔊    [speaks with slight amusement in avatar expression]

[Avatar: knowing smirk → neutral attentive expression]

You > /face young woman with black hair, pale skin, green eyes

Holly > Generating... ⠋
        New look. Not bad at all, if I'm honest.

[Avatar crossfades to new face — black hair, green eyes, photorealistic]

You > exit

Holly > Right then, Dave. Don't do anything I wouldn't do.
        Which, being a computer, is basically everything physical.
🔊    [speaks farewell]

[Session saved. Summary generated. Avatar window closes.]
```

---

## Open Questions for Claude Code

1. **Face slicer quality**: If Pillow transforms look unnatural, generate each state via DALL-E separately with consistent prompt.
2. **Context window management**: Monitor token count — if history is too large, increase summarization aggressiveness.
3. **Pygame main thread**: Non-negotiable on macOS. Chat must run in daemon thread.
4. **Audio format**: MP3 via pydub+ffmpeg is safest. WAV avoids ffmpeg dependency but uses more memory.
5. **Profile extraction accuracy**: Start with simple extraction, iterate if profile gets noisy.
6. **Database migration**: Include a version field in DB for future schema changes.

---

## Success Metrics

- Holly responds with consistent dry wit personality across sessions
- **Memory works**: Holly remembers user's name, interests, and past conversations naturally
- **Failover works**: Transparent switch to OpenRouter when Anthropic is down
- Avatar: stable 30 FPS, photorealistic quality
- **Lip sync matches TTS audio** — mouth visibly syncs with speech
- Emotions change appropriately in conversation
- **TTS sounds natural** — high quality female voice
- `/face` generates new photorealistic faces successfully
- First-run onboarding feels natural and welcoming
- App launches on MacBook Air M2 within 3 seconds
- No data loss — all conversations preserved in SQLite
- No uncaught exceptions
