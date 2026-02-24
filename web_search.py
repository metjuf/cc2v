"""Eigy AI Assistant — Web search module.

DuckDuckGo-based web search with automatic detection of search
requests from user text (Czech + English patterns).
Fetches page content from top results for richer LLM context.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date

import httpx
from lxml import html as lxml_html

from ddgs import DDGS

logger = logging.getLogger(__name__)

# Max chars of page content to extract per result
_MAX_PAGE_CONTENT = 1500
# How many top results to fetch full content for
_FETCH_TOP_N = 3
# Timeout for page fetch (seconds)
_FETCH_TIMEOUT = 5.0
# Skip URLs matching these patterns (binary/non-text content)
_SKIP_URL_RE = re.compile(r"\.(pdf|jpg|jpeg|png|gif|mp4|mp3|zip|rar|exe)$", re.IGNORECASE)


# ── False-positive exclusions ─────────────────────────────────────
# Short phrases / conversational patterns that should NOT trigger search

_EXCLUDE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^jak se (?:máš|máte|daří|vede)",
        r"^(?:řekni|pověz)\s+(?:mi\s+)?(?:vtip|joke|příběh|pohádku)",
        r"^co\s+(?:děláš|umíš|zvládneš|dokážeš)",
        r"^pomoz\s+mi\s+(?:s\s+)?(?:kódem|programem|úkolem|prací)",
        r"^(?:nastav|zapni|vypni|přepni|změň)",
        r"^jak\s+se\s+(?:jmenuješ|máš)",
    ]
]


# ── Search request detection ──────────────────────────────────────

# More specific patterns FIRST, then broader ones.
# Each pattern must have exactly one capture group for the query.
_SEARCH_PATTERNS = [
    # ─── Czech: explicit search commands ───
    r"(?:najdi|vyhledej|hledej|podívej\s+se)\s+(?:mi\s+)?na\s+(?:internetu|netu|webu|googlu)\s+(?:na\s+)?(.+)",
    r"(?:najdi|vyhledej|hledej)\s+(?:mi\s+)?(?:něco|neco|informace|info|nějaké?\s+info)\s+o\s+(.+)",
    r"(?:dej|dáš)\s+mi\s+(?:informace|info|nějaké?\s+info)\s+o\s+(.+)",
    r"(?:vyhledej|hledej|zahledej)\s+(?:mi\s+)?(.+)",
    r"najdi\s+mi\s+(.+)",

    # ─── Czech: knowledge requests ───
    r"zjisti\s+(?:mi\s+)?(.+)",
    r"(?:řekni|pověz|povídej)\s+(?:mi\s+)?(?:něco\s+)?o\s+(.+)",
    r"co\s+(?:víš|vís|věděl|věděla)\s+o\s+(.+)",
    r"zajímá\s+(?:mě|mne|mně)\s+(.+)",
    r"potřebuju?\s+(?:vědět|zjistit|najít)\s+(.+)",

    # ─── Czech: recommendation / advice ───
    r"(?:doporuč|doporuc|poraď|porad)\s+(?:mi\s+)?(.+)",
    r"ukaž\s+(?:mi\s+)?(.+)",
    r"pomoz\s+(?:mi\s+)?najít\s+(.+)",

    # ─── Czech: tutorial / how-to ───
    r"(?:tutorial|návod|navod|kurz|tutoriál)\s+(?:na|pro|k|o)\s+(.+)",
    r"jak\s+na\s+(.+)",
    r"jak\s+(?:se\s+)?(?:dělá|dela|funguje|fungují|fungujou|vytvořit|vytvorit|napsat|udělat|udelat|nainstalovat|nastavit|spustit|používá|používají)\s+(.+)",

    # ─── Czech: factual questions ───
    r"co\s+(?:je\s+to|to\s+je|je|jsou|znamená|znamenaji)\s+(.+)",
    r"kdo\s+(?:je|byl|byla|byli|jsou)\s+(.+)",
    r"kdy\s+(?:je|byl|byla|bylo|bude|jsou|budou)\s+(.+)",
    r"kde\s+(?:je|jsou|se\s+nachází|se\s+nacházi|najdu|můžu\s+najít|se\s+dá\s+najít|se\s+dá\s+koupit|koupím)\s+(.+)",
    r"kolik\s+(?:je|stojí|stoji|má|má|stál|stála|stálo|obyvatel\s+má)\s+(.+)",
    r"jak(?:ý|á|é|ej|ý)\s+(?:je|jsou|bylo|bude|byl|byla)\s+(.+?)(?:\?|$)",
    r"proč\s+(?:je|jsou|se|byl|byla|bylo|nemůžu|nemohu|nejde)\s+(.+)",
    r"existuj[eí]\s+(.+)",
    r"(?:jaký|jaky)\s+je\s+rozdíl\s+(?:mezi\s+)?(.+)",
    r"(?:porovnej|srovnej)\s+(.+)",

    # ─── Czech: current events / news ───
    r"(?:novinky|zprávy|zpravy|aktuality|news)\s+(?:o|ohledně|kolem|z)\s+(.+)",
    r"co\s+(?:se\s+děje|je\s+nového|se\s+stalo|se\s+dělo)\s+(?:s|o|v|kolem|ohledně)\s+(.+)",

    # ─── English: explicit search ───
    r"(?:search\s+(?:for|about)?|google|look\s+up|find(?:\s+me)?)\s+(.+)",

    # ─── English: questions ───
    r"what\s+(?:is|are|was|were|does|do|did)\s+(.+)",
    r"who\s+(?:is|are|was|were)\s+(.+)",
    r"where\s+(?:is|are|was|were|can\s+I\s+find)\s+(.+)",
    r"when\s+(?:is|was|were|will|did)\s+(.+)",
    r"why\s+(?:is|are|was|were|does|do|did)\s+(.+)",
    r"how\s+(?:does|do|did|to|can\s+I|much|many|long|far|old)\s+(.+)",

    # ─── English: other ───
    r"(?:tell\s+me\s+about|explain|describe)\s+(.+)",
    r"(?:recommend|suggest)\s+(.+)",
    r"(?:tutorial|guide)\s+(?:for|on|about)\s+(.+)",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _SEARCH_PATTERNS]

# Filler words to strip from the extracted query
_FILLER = re.compile(
    r"^(?:mi\s+|na\s+(?:internetu|netu|webu|googlu)\s+|"
    r"něco\s+o\s+|neco\s+o\s+|informace\s+o\s+|info\s+o\s+|"
    r"prosím\s+|please\s+|třeba\s+|vlastně\s+|"
    r"nějaké?\s+|nejaký?\s+|nějakou\s+)+",
    re.IGNORECASE,
)

# Trailing filler to strip
_TRAILING_FILLER = re.compile(
    r"\s+(?:prosím|please|díky|děkuji|dekuji)$",
    re.IGNORECASE,
)


def detect_search_request(text: str) -> str | None:
    """Detect a search request from user text.

    Returns the cleaned search query string, or None if no search detected.
    """
    text = text.strip().rstrip("?").rstrip(".").rstrip("!").strip()

    # Check exclusions first
    for excl in _EXCLUDE_PATTERNS:
        if excl.search(text):
            return None

    for pattern in _COMPILED_PATTERNS:
        match = pattern.search(text)
        if match:
            query = match.group(1).strip()
            # Strip leading filler words
            query = _FILLER.sub("", query).strip()
            # Strip trailing filler words
            query = _TRAILING_FILLER.sub("", query).strip()
            if len(query) >= 2:
                return query

    return None


# ── Time-aware query enhancement ──────────────────────────────────

_TIME_WORDS = re.compile(
    r"\b(?:dnes|dneska|today|včera|yesterday|aktuálně|aktuální|"
    r"teď|nyní|právě|nové|nejnovější|latest|current|recent|"
    r"novinky|novinka|zprávy|zpráva|aktuality|news|"
    r"tento\s+týden|this\s+week|letos)\b",
    re.IGNORECASE,
)

_MONTHS_CZ = [
    "", "ledna", "února", "března", "dubna", "května", "června",
    "července", "srpna", "září", "října", "listopadu", "prosince",
]


def _enhance_query(query: str) -> str:
    """Add today's date to time-sensitive queries for better results."""
    if _TIME_WORDS.search(query):
        today = date.today()
        month_name = _MONTHS_CZ[today.month]
        return f"{query} {month_name} {today.year}"
    return query


# ── Crypto price detection & fetching (CoinGecko) ────────────────

_CRYPTO_ALIASES: dict[str, str] = {
    "bitcoin": "bitcoin", "btc": "bitcoin", "bitcoinu": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum", "etheru": "ethereum", "ether": "ethereum",
    "solana": "solana", "sol": "solana", "solany": "solana",
    "cardano": "cardano", "ada": "cardano", "cardana": "cardano",
    "dogecoin": "dogecoin", "doge": "dogecoin", "dogecoinu": "dogecoin",
    "ripple": "ripple", "xrp": "ripple",
    "litecoin": "litecoin", "ltc": "litecoin", "litecoinu": "litecoin",
    "polkadot": "polkadot", "dot": "polkadot",
    "chainlink": "chainlink", "link": "chainlink",
    "avalanche": "avalanche-2", "avax": "avalanche-2",
    "polygon": "matic-network", "matic": "matic-network",
    "tron": "tron", "trx": "tron",
    "shiba": "shiba-inu", "shib": "shiba-inu",
}

_CRYPTO_PRICE_RE = re.compile(
    r"(?:cen[auěy]|price|kurz[ue]?|hodnot[auěy]|kolik\s+(?:stojí|stoji|je))\s+"
    r"(?:(?:za\s+)?(?:jeden\s+)?)?(\w+)",
    re.IGNORECASE,
)

_CRYPTO_MENTION_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _CRYPTO_ALIASES) + r")\b",
    re.IGNORECASE,
)


def detect_crypto_request(text: str) -> str | None:
    """Detect a crypto price request. Returns CoinGecko coin ID or None."""
    text_lower = text.lower()

    # Check explicit price patterns first: "cena bitcoinu", "kurz etheru"
    m = _CRYPTO_PRICE_RE.search(text_lower)
    if m:
        token = m.group(1).lower()
        if token in _CRYPTO_ALIASES:
            return _CRYPTO_ALIASES[token]

    # Check if text mentions a crypto AND a price-related word
    price_words = {"cen", "cenu", "cena", "ceny", "ceně", "price", "kurz",
                   "hodnot", "stojí", "stoji", "kolik", "aktuáln", "aktualn"}
    has_price_word = any(w in text_lower for w in price_words)
    if has_price_word:
        cm = _CRYPTO_MENTION_RE.search(text_lower)
        if cm:
            return _CRYPTO_ALIASES[cm.group(1).lower()]

    return None


async def fetch_crypto_price(coin_id: str) -> dict | None:
    """Fetch current price from CoinGecko API. Returns price data or None."""
    url = (
        f"https://api.coingecko.com/api/v3/simple/price"
        f"?ids={coin_id}&vs_currencies=usd,czk,eur"
        f"&include_24hr_change=true&include_market_cap=true"
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if coin_id not in data:
                return None
            return data[coin_id]
    except Exception:
        return None


def format_crypto_price(coin_id: str, data: dict) -> str:
    """Format crypto price data as text for LLM context injection."""
    name = coin_id.replace("-", " ").title()
    usd = data.get("usd", 0)
    czk = data.get("czk", 0)
    eur = data.get("eur", 0)
    change_24h = data.get("usd_24h_change", 0)
    mcap = data.get("usd_market_cap", 0)

    lines = [
        f"AKTUÁLNÍ TRŽNÍ DATA pro {name} (živá data z CoinGecko API):",
        f"  Cena: ${usd:,.2f} USD | {czk:,.0f} CZK | €{eur:,.2f} EUR",
        f"  24h změna: {change_24h:+.2f}%",
    ]
    if mcap:
        if mcap >= 1e12:
            lines.append(f"  Market cap: ${mcap/1e12:,.2f}T USD")
        elif mcap >= 1e9:
            lines.append(f"  Market cap: ${mcap/1e9:,.2f}B USD")
        else:
            lines.append(f"  Market cap: ${mcap/1e6:,.0f}M USD")

    lines.append("  Zdroj: CoinGecko (real-time)")
    return "\n".join(lines)


# ── Page content extraction ───────────────────────────────────────


def _extract_text_from_html(raw_html: str) -> str:
    """Extract clean text content from HTML using lxml."""
    try:
        doc = lxml_html.fromstring(raw_html)
    except Exception:
        return ""

    # Remove script, style, nav, footer, header, aside elements
    for tag in doc.iter("script", "style", "nav", "footer", "header", "aside", "noscript"):
        tag.getparent().remove(tag)

    # Try to find main content area first
    main = doc.find(".//main")
    if main is None:
        main = doc.find(".//article")
    if main is not None:
        text = main.text_content()
    else:
        body = doc.find(".//body")
        text = body.text_content() if body is not None else doc.text_content()

    # Clean up whitespace
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


async def _fetch_page_content(url: str) -> str:
    """Fetch and extract text content from a URL."""
    if _SKIP_URL_RE.search(url):
        return ""

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=_FETCH_TIMEOUT,
        ) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; EigyBot/1.0)"},
            )
            if resp.status_code != 200:
                return ""
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type:
                return ""
            text = _extract_text_from_html(resp.text)
            return text[:_MAX_PAGE_CONTENT]
    except Exception:
        return ""


async def _enrich_results(results: list[dict]) -> list[dict]:
    """Fetch page content for top N results in parallel."""
    to_fetch = results[:_FETCH_TOP_N]
    tasks = [_fetch_page_content(r["url"]) for r in to_fetch]
    contents = await asyncio.gather(*tasks, return_exceptions=True)

    for i, content in enumerate(contents):
        if isinstance(content, str) and content:
            results[i]["content"] = content

    return results


# ── DuckDuckGo search ─────────────────────────────────────────────


def _search_sync(query: str, max_results: int = 7) -> list[dict]:
    """Synchronous DuckDuckGo search."""
    try:
        ddgs = DDGS()
        results = []
        for r in ddgs.text(query, region="cz-cs", max_results=max_results):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })
        return results
    except Exception as e:
        logger.warning("DuckDuckGo search failed: %s", e)
        return []


async def search(query: str, max_results: int = 7) -> list[dict]:
    """Search DuckDuckGo and enrich top results with page content.

    Automatically adds today's date for time-sensitive queries.
    Returns list of {"title", "url", "snippet", "content"(optional)}.
    """
    enhanced_query = _enhance_query(query)
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, _search_sync, enhanced_query, max_results)
    if results:
        results = await _enrich_results(results)
    return results


# ── Result formatting ─────────────────────────────────────────────


def _short_source(url: str) -> str:
    """Extract a short, human-friendly source name from a URL.

    Examples:
        https://mobilmania.zive.cz/clanky/... → mobilmania.zive.cz
        https://www.itmix.cz/novinky/...     → itmix.cz
    """
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        # Strip leading "www."
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return url


def format_results(results: list[dict]) -> str:
    """Format search results as structured text for LLM context injection."""
    if not results:
        return "Vyhledávání nevrátilo žádné výsledky."

    sections = []
    for i, r in enumerate(results, 1):
        source = _short_source(r["url"])
        parts = [f"[{i}] {r['title']} (zdroj: {source})"]
        if r.get("content"):
            # Has full page content — use it
            parts.append(f"    Obsah stránky:\n    {r['content']}")
        elif r["snippet"]:
            # Fallback to snippet
            parts.append(f"    Popis: {r['snippet']}")
        sections.append("\n".join(parts))

    return "\n\n".join(sections)
