"""Eigy AI Assistant — EPUB book reader.

Parses EPUB files, splits text into chunks ("pages"), and provides
a background reading task that feeds chunks to TTS sequentially.
No LLM is used — only ebooklib + edge-tts + pygame.mixer.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import warnings

import ebooklib
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from ebooklib import epub

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

import config

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1500  # characters per "page" (~30s of TTS)


@dataclass
class BookInfo:
    """Parsed book metadata + text chunks."""

    title: str
    file_path: Path
    chunks: list[str]
    total_pages: int


def find_book(name: str) -> Path | None:
    """Find an EPUB file in BOOKS_DIR by name (case-insensitive, no extension).

    Searches for exact stem match first, then prefix match.
    """
    books_dir = config.BOOKS_DIR
    books_dir.mkdir(parents=True, exist_ok=True)

    name_lower = name.strip().lower()

    # Exact stem match
    for f in books_dir.iterdir():
        if f.suffix.lower() == ".epub" and f.stem.lower() == name_lower:
            return f

    # Prefix match (e.g. "slova" matches "slova_druhe_vydani.epub")
    for f in books_dir.iterdir():
        if f.suffix.lower() == ".epub" and f.stem.lower().startswith(name_lower):
            return f

    return None


def parse_epub(path: Path) -> BookInfo:
    """Parse an EPUB file into text chunks (pages).

    Extracts text from all document items, joins them, and splits
    into chunks of ~CHUNK_SIZE characters at sentence boundaries.
    """
    book = epub.read_epub(str(path), options={"ignore_ncx": True})

    full_text_parts: list[str] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        html_content = item.get_content()
        soup = BeautifulSoup(html_content, "lxml")
        text = soup.get_text(separator="\n", strip=True)
        if text.strip():
            full_text_parts.append(text.strip())

    combined = "\n\n".join(full_text_parts)
    chunks = _split_into_chunks(combined, CHUNK_SIZE)

    # Extract title from metadata or fall back to filename
    title_meta = book.get_metadata("DC", "title")
    title = title_meta[0][0] if title_meta else path.stem

    logger.info("Parsed EPUB '%s': %d pages (%d chars)", title, len(chunks), len(combined))

    return BookInfo(
        title=str(title),
        file_path=path,
        chunks=chunks,
        total_pages=len(chunks),
    )


def _split_into_chunks(text: str, size: int) -> list[str]:
    """Split text into chunks, preferring sentence boundaries.

    Tries to cut at ". " (sentence end), falls back to " " (word),
    falls back to hard cut at size limit.
    """
    chunks: list[str] = []
    while text:
        text = text.lstrip()
        if not text:
            break
        if len(text) <= size:
            chunks.append(text)
            break

        # Find the best cut point within the chunk size
        cut = -1
        # Prefer sentence boundary
        for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
            pos = text[:size].rfind(sep)
            if pos > size // 3:  # don't cut too early
                cut = max(cut, pos + 1)  # include the punctuation

        # Fall back to word boundary
        if cut == -1:
            cut = text[:size].rfind(" ")

        # Hard cut if nothing else works
        if cut == -1 or cut < size // 4:
            cut = size

        chunks.append(text[:cut].rstrip())
        text = text[cut:]

    return [c for c in chunks if c]


async def book_reading_task(
    book: BookInfo,
    start_page: int,
    tts,  # TTSEngine
    audio_player,  # AudioPlayer
    event_queue: asyncio.Queue,
    cancel_event: asyncio.Event,
    update_bookmark: Callable[[int], None],
    progress_interval: int | None = None,
) -> int:
    """Background task: reads a book aloud from start_page.

    For each chunk:
    1. Synthesize via TTS (edge-tts — no LLM)
    2. Enqueue audio to audio_player
    3. Wait for audio playback to finish
    4. Update bookmark in DB
    5. Every progress_interval pages: emit progress event

    Returns the last completed page number.
    Stops early if cancel_event is set.
    """
    interval = progress_interval or config.BOOK_PROGRESS_INTERVAL
    current_page = start_page

    for i in range(start_page, book.total_pages):
        # Check for cancellation before each chunk
        if cancel_event.is_set():
            logger.info("Book reading cancelled at page %d/%d", i, book.total_pages)
            break

        chunk = book.chunks[i]
        logger.debug("Reading page %d/%d (%d chars)", i + 1, book.total_pages, len(chunk))

        # Synthesize (edge-tts, no LLM)
        path = await tts.synthesize(chunk)
        if path:
            audio_player.enqueue(path)

            # Wait for this audio to finish playing
            # Poll every 0.5s — allows cancellation check
            while audio_player.playing or not audio_player.audio_queue.empty():
                if cancel_event.is_set():
                    break
                await asyncio.sleep(0.5)
        else:
            # TTS failed for this chunk — skip but don't stop
            logger.warning("TTS failed for page %d", i + 1)

        if cancel_event.is_set():
            break

        current_page = i + 1
        update_bookmark(current_page)

        # Emit progress event at intervals
        if current_page % interval == 0 or current_page == book.total_pages:
            await event_queue.put({
                "type": "book_progress",
                "title": book.title,
                "page": current_page,
                "total": book.total_pages,
            })

    # Book finished?
    if current_page >= book.total_pages:
        await event_queue.put({
            "type": "book_finished",
            "title": book.title,
            "total": book.total_pages,
        })
        logger.info("Book '%s' finished (%d pages)", book.title, book.total_pages)

    return current_page
