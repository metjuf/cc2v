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
    is_vague_query,
    refine_search_query,
    summarize_search_results,
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
        original_query = None
        search_query = detect_search_request(user_input)
        if search_query and not crypto_context:
            # Refine vague queries using LLM + conversation context
            original_query = search_query
            if is_vague_query(search_query):
                search_query = await refine_search_query(
                    search_query, ctx.current_messages,
                )
                if search_query != original_query:
                    logger.info(
                        "Query refined: '%s' → '%s'",
                        original_query, search_query,
                    )

            display.show_system(f"Hledám: {search_query}...")
            if not crypto_id:
                ctx.avatar_queue.put({"type": "thinking_start"})
                result.show_thinking = True
            results = await web_search(search_query)
            if results:
                raw_results = format_search_results(results)
                # Summarize raw results via aux model
                search_context = await summarize_search_results(
                    search_query, raw_results,
                )
            if ctx.slog:
                ctx.slog.log_search(
                    search_query, len(results) if results else 0,
                    original_query=original_query if original_query != search_query else None,
                    summarized=search_context is not None and results is not None,
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
                    f"Informace z webu k dotazu \"{search_query}\":\n\n"
                    f"{search_context}\n\n"
                    "INSTRUKCE: Odpověz PŘIROZENĚ vlastními slovy na "
                    "základě těchto informací. NEKOPÍRUJ formát výsledků. "
                    "Pokud informace nejsou relevantní, odpověz z "
                    "vlastních znalostí."
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
