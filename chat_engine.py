"""Holly AI Assistant — LLM communication engine.

Anthropic API (primary) with OpenRouter fallback.
Streaming responses via SSE.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

import httpx

import config

logger = logging.getLogger(__name__)


async def stream_anthropic(
    messages: list[dict], model: str | None = None, api_key: str | None = None
) -> AsyncGenerator[str, None]:
    """Stream response from Anthropic Messages API.

    System messages are extracted from the messages list and passed
    via the dedicated ``system`` field (Anthropic format requirement).
    """
    api_key = api_key or config.ANTHROPIC_API_KEY
    model = model or config.ANTHROPIC_MODEL

    # Separate system messages from conversation
    system_content = "\n\n".join(
        m["content"] for m in messages if m["role"] == "system"
    )
    conversation = [m for m in messages if m["role"] != "system"]

    body: dict = {
        "model": model,
        "max_tokens": 4096,
        "messages": conversation,
        "stream": True,
    }
    if system_content:
        body["system"] = system_content

    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=60.0,
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                raise httpx.HTTPStatusError(
                    f"Anthropic API error: {response.status_code}",
                    request=response.request,
                    response=response,
                )
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                if data["type"] == "content_block_delta":
                    yield data["delta"]["text"]
                elif data["type"] == "message_stop":
                    break


async def stream_openrouter(
    messages: list[dict], model: str | None = None, api_key: str | None = None
) -> AsyncGenerator[str, None]:
    """Stream response from OpenRouter (OpenAI-compatible API)."""
    api_key = api_key or config.OPENROUTER_API_KEY
    model = model or config.OPENROUTER_MODEL

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
            timeout=60.0,
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                raise httpx.HTTPStatusError(
                    f"OpenRouter API error: {response.status_code}",
                    request=response.request,
                    response=response,
                )
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                delta = chunk["choices"][0]["delta"]
                if "content" in delta and delta["content"]:
                    yield delta["content"]


async def get_response(messages: list[dict]) -> AsyncGenerator[str, None]:
    """Unified streaming response with automatic failover.

    1. Try Anthropic API (if key configured)
    2. Fallback to OpenRouter (if key configured)
    3. Yield error message if both fail
    """
    # Try Anthropic first
    if config.ANTHROPIC_API_KEY:
        try:
            async for token in stream_anthropic(messages):
                yield token
            return
        except Exception as e:
            logger.warning("Anthropic API failed: %s — falling back to OpenRouter", e)

    # Fallback to OpenRouter
    if config.OPENROUTER_API_KEY:
        try:
            async for token in stream_openrouter(messages):
                yield token
            return
        except Exception as e:
            logger.warning("OpenRouter API failed: %s", e)

    # Both failed
    yield "Promiň, moje obvody jsou trochu popletené. Zkontroluj API klíče a zkus to znovu."


async def get_auxiliary_response(
    messages: list[dict], model: str | None = None
) -> str:
    """Non-streaming response via OpenRouter for cheap auxiliary tasks.

    Used for: conversation summaries, profile extraction, emotion detection.
    """
    model = model or config.AUX_MODEL

    if not config.OPENROUTER_API_KEY:
        # Fall back to Anthropic if no OpenRouter key
        if config.ANTHROPIC_API_KEY:
            result = []
            async for token in stream_anthropic(messages, model=config.ANTHROPIC_MODEL):
                result.append(token)
            return "".join(result)
        return ""

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/holly-ai",
            },
            json={
                "model": model,
                "messages": messages,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
