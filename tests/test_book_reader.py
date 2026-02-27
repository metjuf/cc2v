"""Tests for book_reader — EPUB parsing, text chunking, and command detection."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from book_reader import find_book, _split_into_chunks, parse_epub
from plugins.book_reader_plugin import detect_book_command


# ── find_book tests ──────────────────────────────────────────────


def test_find_book_existing(tmp_path, monkeypatch):
    """Finds an EPUB file by stem name (case-insensitive)."""
    import config
    monkeypatch.setattr(config, "BOOKS_DIR", tmp_path)

    epub_file = tmp_path / "Slova.epub"
    epub_file.write_bytes(b"")  # dummy

    assert find_book("slova") == epub_file
    assert find_book("Slova") == epub_file
    assert find_book("SLOVA") == epub_file


def test_find_book_not_found(tmp_path, monkeypatch):
    """Returns None for nonexistent book."""
    import config
    monkeypatch.setattr(config, "BOOKS_DIR", tmp_path)

    assert find_book("neexistuje") is None


def test_find_book_prefix_match(tmp_path, monkeypatch):
    """Matches by prefix if exact match not found."""
    import config
    monkeypatch.setattr(config, "BOOKS_DIR", tmp_path)

    epub_file = tmp_path / "slova_druhe_vydani.epub"
    epub_file.write_bytes(b"")

    assert find_book("slova") == epub_file


def test_find_book_ignores_non_epub(tmp_path, monkeypatch):
    """Non-EPUB files are ignored."""
    import config
    monkeypatch.setattr(config, "BOOKS_DIR", tmp_path)

    (tmp_path / "slova.txt").write_text("not epub")
    (tmp_path / "slova.pdf").write_bytes(b"not epub")

    assert find_book("slova") is None


# ── _split_into_chunks tests ────────────────────────────────────


def test_split_short_text():
    """Text shorter than chunk size stays as one chunk."""
    chunks = _split_into_chunks("Krátký text.", 1500)
    assert chunks == ["Krátký text."]


def test_split_preserves_all_text():
    """No text is lost during splitting."""
    text = "Věta jedna. " * 200  # ~2600 chars
    chunks = _split_into_chunks(text, 500)

    # Reconstruct and compare (whitespace may vary slightly)
    reconstructed = " ".join(chunks)
    # All words should be present
    assert reconstructed.count("Věta") == text.count("Věta")
    assert reconstructed.count("jedna") == text.count("jedna")


def test_split_prefers_sentence_boundary():
    """Splits at sentence boundaries when possible."""
    text = "První věta. Druhá věta. Třetí věta. Čtvrtá věta."
    chunks = _split_into_chunks(text, 30)

    # Each chunk should end with a period (sentence boundary)
    for chunk in chunks:
        assert chunk[-1] in ".!?", f"Chunk doesn't end at sentence boundary: '{chunk}'"


def test_split_empty_text():
    """Empty text returns empty list."""
    assert _split_into_chunks("", 1500) == []
    assert _split_into_chunks("   ", 1500) == []


def test_split_exact_size():
    """Text exactly at chunk size is one chunk."""
    text = "x" * 1500
    chunks = _split_into_chunks(text, 1500)
    assert len(chunks) == 1


# ── parse_epub tests ────────────────────────────────────────────


def test_parse_epub_minimal(tmp_path):
    """Parse a minimal EPUB created with ebooklib."""
    from ebooklib import epub

    # Create a minimal EPUB
    book = epub.EpubBook()
    book.set_identifier("test-001")
    book.set_title("Testovací kniha")
    book.set_language("cs")

    # Add a chapter
    chapter = epub.EpubHtml(title="Kapitola 1", file_name="chap_01.xhtml", lang="cs")
    chapter.content = (
        "<html><body>"
        "<h1>Kapitola 1</h1>"
        "<p>Toto je první odstavec testovací knihy. "
        "Obsahuje několik vět, aby byl dostatečně dlouhý.</p>"
        "<p>A toto je druhý odstavec. Také má víc textu pro testování.</p>"
        "</body></html>"
    )
    book.add_item(chapter)

    # Add navigation
    book.toc = [epub.Link("chap_01.xhtml", "Kapitola 1", "chap_01")]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]

    epub_path = tmp_path / "test.epub"
    epub.write_epub(str(epub_path), book)

    # Parse it
    result = parse_epub(epub_path)
    assert result.title == "Testovací kniha"
    assert result.total_pages >= 1
    assert len(result.chunks) == result.total_pages
    assert "Kapitola 1" in result.chunks[0]
    assert "první odstavec" in result.chunks[0]


# ── detect_book_command tests ───────────────────────────────────


def test_detect_read_command():
    """Detects 'čti knihu X' variants."""
    assert detect_book_command("čti knihu slova") == ("read", "slova")
    assert detect_book_command("Čti knihu Duna") == ("read", "Duna")
    assert detect_book_command("přečti knihu test") == ("read", "test")
    assert detect_book_command("čti slova") == ("read", "slova")


def test_detect_resume_command():
    """Detects 'pokračuj v čtení X' variants."""
    assert detect_book_command("pokračuj v čtení slova") == ("read", "slova")
    assert detect_book_command("pokračuj v četbě duna") == ("read", "duna")


def test_detect_stop_command():
    """Detects 'zastav čtení' variants."""
    assert detect_book_command("zastav čtení") == ("stop", "")
    assert detect_book_command("zastav četbu") == ("stop", "")
    assert detect_book_command("stop čtení") == ("stop", "")
    assert detect_book_command("přestaň čtení") == ("stop", "")


def test_detect_delete_bookmark_command():
    """Detects 'vymaž záložku X' variants."""
    assert detect_book_command("vymaž záložku slova") == ("delete", "slova")
    assert detect_book_command("smaž záložku duna") == ("delete", "duna")
    assert detect_book_command("odstraň záložku test") == ("delete", "test")


def test_detect_no_book_command():
    """Normal text doesn't match book commands."""
    assert detect_book_command("ahoj") is None
    assert detect_book_command("kolik je hodin") is None
    assert detect_book_command("co je nového v knihovně") is None
