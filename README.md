# Holly — AI Assistant with Animated Avatar & Voice

A terminal chat application for macOS featuring **Holly**, an AI assistant with the personality of a ship's computer — dry wit, deadpan humor, and quiet intelligence (inspired by Holly from Red Dwarf).

## Features

- **Terminal chat** with streaming responses and rich formatting
- **Photorealistic animated avatar** (Pygame window) with blinking, breathing, lip sync, and emotional reactions
- **Text-to-speech** — Holly speaks every response (OpenAI TTS HD or edge-tts)
- **Persistent memory** — Holly remembers your name, interests, and all conversations across sessions
- **Face generation** — Generate new photorealistic faces on demand via DALL-E 3
- **Failover** — Anthropic API primary, OpenRouter fallback

## Prerequisites

- Python 3.11+
- macOS (Apple Silicon recommended)
- ffmpeg (`brew install ffmpeg`)

## Installation

```bash
# Clone and enter directory
git clone <repo>
cd cc2v

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install ffmpeg (required for audio processing)
brew install ffmpeg

# Configure API keys
cp .env.example .env
# Edit .env and add your API keys
```

## Configuration

Edit `.env` with your API keys:

| Key | Required | Purpose |
|-----|----------|---------|
| `ANTHROPIC_API_KEY` | Yes* | Primary chat (Claude) |
| `OPENROUTER_API_KEY` | Yes* | Fallback chat + auxiliary tasks |
| `OPENAI_API_KEY` | No | Face generation (DALL-E 3) + premium TTS |

*At least one of ANTHROPIC_API_KEY or OPENROUTER_API_KEY must be set.

## Usage

```bash
source venv/bin/activate
python main.py
```

On first launch, Holly will ask for your name. A Pygame window opens showing the avatar, and the chat runs in the terminal.

## Commands

| Command | Description |
|---------|-------------|
| `/face [description]` | Generate new photorealistic face |
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
| `exit` / `quit` | Quit (auto-saves session) |

## Architecture

```
main.py              Entry point — Pygame main thread, chat daemon thread
config.py            Configuration (.env loading)
chat_engine.py       LLM communication (Anthropic + OpenRouter)
display.py           Terminal output (Rich) + input (prompt_toolkit)
tts_engine.py        Text-to-speech (OpenAI TTS HD + edge-tts)
audio_player.py      Audio playback + amplitude extraction
image_generator.py   DALL-E 3 face generation
memory/
  database.py        SQLite database layer
  user_profile.py    User profile management
  memory_manager.py  Context window construction
avatar/
  window.py          Pygame window + render loop
  face_renderer.py   PNG layer composition
  animator.py        Animation system (blink, breathe, lip sync)
  emotion_detector.py Text → emotion classification
  face_slicer.py     Portrait → animatable layers
```

## Troubleshooting

- **Pygame window doesn't open**: Pygame requires the main thread on macOS. Ensure you're running `python main.py` directly (not through some wrapper).
- **No audio**: Install ffmpeg (`brew install ffmpeg`). Check `/voice on`.
- **API errors**: Verify your API keys in `.env`. Holly auto-fails over to OpenRouter if Anthropic is down.
- **Face generation fails**: Requires `OPENAI_API_KEY` in `.env`.
