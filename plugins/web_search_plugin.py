"""Web search plugin — auto-detects search and crypto queries, injects results."""

from __future__ import annotations

import logging

import display
from plugins.base import Plugin, PluginContext, PreResponseResult
from web_search import (
    detect_search_request,
    search as web_search,
    format_results as format_search_results,
    detect_crypto_request,
    detect_crypto_request_llm,
    _find_crypto_mention,
    fetch_crypto_price,
    format_crypto_price,
)

logger = logging.getLogger(__name__)


class WebSearchPlugin(Plugin):
    name = "web_search"
    priority = 50

    async def pre_response(
        self, ctx: PluginContext, user_input: str
    ) -> PreResponseResult:
        """Detect crypto/search requests and inject context for LLM."""
        result = PreResponseResult()

        # 1. Crypto check — two-tier detection:
        #    Tier 1: regex (instant, zero latency)
        #    Tier 2: LLM fallback (only if regex fails but crypto name is mentioned)
        crypto_context = None
        crypto_id = detect_crypto_request(user_input)
        detection_method = "regex"

        if not crypto_id and _find_crypto_mention(user_input):
            # Crypto name found but regex didn't match intent → ask LLM
            logger.debug("Crypto regex miss, trying LLM fallback for: %s", user_input[:80])
            crypto_id = await detect_crypto_request_llm(user_input)
            detection_method = "llm"

        if crypto_id:
            logger.info("Crypto detected (%s): %s → %s", detection_method, user_input[:50], crypto_id)
            display.show_system(f"Načítám cenu: {crypto_id}...")
            ctx.avatar_queue.put({"type": "thinking_start"})
            result.show_thinking = True
            price_data = await fetch_crypto_price(crypto_id)
            if price_data:
                crypto_context = format_crypto_price(crypto_id, price_data)
            if ctx.slog:
                ctx.slog.log_crypto(crypto_id, price_data)

        # 2. Web search (skip if crypto data already found)
        search_context = None
        search_query = detect_search_request(user_input)
        if search_query and not crypto_context:
            display.show_system(f"Hledám: {search_query}...")
            if not crypto_id:
                ctx.avatar_queue.put({"type": "thinking_start"})
                result.show_thinking = True
            results = await web_search(search_query)
            if results:
                search_context = format_search_results(results)
            if ctx.slog:
                ctx.slog.log_search(
                    search_query, len(results) if results else 0
                )

        # 3. Build context messages
        if crypto_context:
            result.context_messages.append({
                "role": "system",
                "content": (
                    f"{crypto_context}\n\n"
                    "INSTRUKCE: Toto jsou ŽIVÁ tržní data z CoinGecko API. "
                    "Použij PŘESNĚ tyto hodnoty ve své odpovědi. "
                    "NEVYMÝŠLEJ jiné ceny."
                ),
            })

        if search_context:
            result.context_messages.append({
                "role": "system",
                "content": (
                    f'VÝSLEDKY VYHLEDÁVÁNÍ pro "{search_query}":\n\n'
                    f"{search_context}\n\n"
                    "INSTRUKCE: Využij výše uvedené výsledky a obsah "
                    "stránek k sestavení přesné a informativní odpovědi. "
                    "Uváděj konkrétní fakta z obsahu. Na konci uveď "
                    "zdroje STRUČNĚ jen názvem domény (např. 'Zdroje: "
                    "mobilmania.cz, itmix.cz') — NIKDY nevypisuj celé "
                    "URL adresy. Pokud výsledky nejsou relevantní, "
                    "řekni to a odpověz z vlastních znalostí."
                ),
            })

        return result

    def get_help_entries(self) -> list[tuple[str, str]]:
        return [
            ('"vyhledej X"', "Automatický web search (DuckDuckGo)"),
            ('"cena bitcoinu"', "Živá cena kryptoměny (CoinGecko)"),
        ]


def create_plugin() -> Plugin:
    return WebSearchPlugin()
