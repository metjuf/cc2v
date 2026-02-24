"""Eigy AI Assistant — Face image generation via DALL-E 3.

Generates photorealistic portraits and saves them to assets/generated/.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

import config

logger = logging.getLogger(__name__)

FACE_PROMPT_TEMPLATE = (
    "Photorealistic front-facing portrait photograph of a woman, "
    "{description}, centered face filling 80% of frame, plain dark charcoal background, "
    "professional studio lighting with soft key light from upper left and subtle fill, "
    "high detail skin texture with visible pores, realistic eye reflections with catchlights, "
    "natural hair with individual strands visible, subtle natural makeup, sharp focus on eyes, "
    "shot on Canon EOS R5 85mm f/1.4, shallow DOF, 8K, hyperrealistic, "
    "indistinguishable from photograph"
)

DEFAULT_DESCRIPTION = (
    "beautiful woman in her early 30s, straight gaze into camera, "
    "flawless skin with natural subtle texture, warm brown eyes with realistic light reflections, "
    "soft honey-blonde hair framing face, neutral expression with hint of knowing smile"
)


async def generate_face(description: str = "") -> str | None:
    """Generate a photorealistic face via DALL-E 3.

    Args:
        description: Optional description override (e.g., "young woman with black hair, green eyes").

    Returns:
        Path to the saved image, or None on failure.
    """
    if not config.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — cannot generate faces")
        return None

    desc = description.strip() if description.strip() else DEFAULT_DESCRIPTION
    prompt = FACE_PROMPT_TEMPLATE.format(description=desc)

    try:
        async with httpx.AsyncClient() as client:
            # Request image generation
            response = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "dall-e-3",
                    "prompt": prompt,
                    "n": 1,
                    "size": "1024x1024",
                    "quality": "hd",
                    "style": "natural",
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            image_url = data["data"][0]["url"]

            # Download the image
            img_response = await client.get(image_url, timeout=60.0)
            img_response.raise_for_status()

            # Save to assets/generated/
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            face_dir = config.GENERATED_FACE_DIR / f"face_{timestamp}"
            face_dir.mkdir(parents=True, exist_ok=True)

            source_path = face_dir / "source.png"
            source_path.write_bytes(img_response.content)

            logger.info("Generated face saved to %s", source_path)
            return str(source_path)

    except httpx.HTTPStatusError as e:
        logger.error("DALL-E 3 API error: %s", e.response.text[:200])
        return None
    except Exception as e:
        logger.error("Face generation failed: %s", e)
        return None
