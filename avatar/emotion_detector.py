"""Eigy AI Assistant — Emotion detection from text.

Analyzes assistant's response text to determine the appropriate
avatar emotion state. Keyword-based (fast) with optional LLM fallback.
"""

from __future__ import annotations

import logging
import re

import config

logger = logging.getLogger(__name__)

# Emotion keyword patterns — scored by specificity
# Czech + English patterns (Eigy speaks Czech but may use some English terms)
_PATTERNS: dict[str, list[str]] = {
    "amused": [
        # Czech
        r"\bheh\b", r"\bha\b", r"\bhaha\b", r"\bvtip", r"\bžert",
        r"\bsarkas", r"\biron", r"\bšikovn", r"\bchytr",
        r"\bgenialn", r"\bzábavn", r"\blegra", r"\bsmích",
        r"\búdajně\b", r"\bprý\b", r"\bjakožto\b",
        r"[!?]{2,}",
        # English fallback
        r"\bjoke\b", r"\bfunny\b", r"\bsarcas", r"\bwitty\b",
    ],
    "happy": [
        # Czech
        r"\brád", r"\bráda\b", r"\bskvěl", r"\búžasn", r"\bvýborn",
        r"\bfajn\b", r"\bsuper\b", r"\bparáda\b", r"\bdobr[áéý]",
        r"\btěší\b", r"\bpotěš", r"\bkrásn", r"\bnádhern",
        r"\bpříjemn", r"\bgratuluj",
        # English fallback
        r"\bgreat\b", r"\bnice\b", r"\bwonderful\b",
    ],
    "concerned": [
        # Czech
        r"\bpromiň\b", r"\bomlouv", r"\bstarost", r"\bobáv",
        r"\bbohužel\b", r"\bpozor\b", r"\bopatrn",
        r"\bnebezpeč", r"\brizik", r"\bvážn",
        r"\bnestoj", r"\bproblem", r"\bpotíž",
        # English fallback
        r"\bsorry\b", r"\bcareful\b",
    ],
    "surprised": [
        # Czech
        r"\bpáni\b", r"\bjé\b", r"\bvážně\?\b", r"\bfakt\?\b",
        r"\bnečekan", r"\bneuvěřiteln", r"\búžas",
        r"\bto snad ne\b", r"\bno teda\b", r"\bnádher",
        # English fallback
        r"\bwow\b", r"\breally\?\b",
    ],
    "thinking": [
        # Czech
        r"\bhmm\b", r"\bzajímav", r"\bpřemýšl",
        r"\bzáleží\b", r"\bna jednu stranu\b", r"\btechnicky\b",
        r"\bpodívejme se\b", r"\bno\b,", r"\bpravda\b,",
        r"\bzvažuj", r"\brozmysl",
        # English fallback
        r"\btechnically\b", r"\bhmm\b",
    ],
}


def detect_emotion(text: str) -> str:
    """Detect emotion from assistant's response text using keyword matching.

    Returns one of: neutral, amused, happy, concerned, surprised, thinking.
    """
    text_lower = text.lower()
    scores: dict[str, int] = {emotion: 0 for emotion in _PATTERNS}

    for emotion, patterns in _PATTERNS.items():
        for pattern in patterns:
            matches = re.findall(pattern, text_lower)
            scores[emotion] += len(matches)

    # Find highest scoring emotion
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "neutral"

    return best


async def detect_emotion_llm(text: str) -> str:
    """Detect emotion using a cheap LLM model (optional, more accurate).

    Falls back to keyword detection on failure.
    """
    try:
        import chat_engine

        prompt_messages = [
            {
                "role": "user",
                "content": (
                    "Classify the primary emotion of this AI assistant response "
                    "into exactly one of: neutral, amused, happy, concerned, surprised, thinking.\n\n"
                    f"Response: {text[:500]}\n\n"
                    "Return ONLY the emotion word, nothing else."
                ),
            }
        ]
        result = await chat_engine.get_auxiliary_response(prompt_messages)
        emotion = result.strip().lower()
        if emotion in ("neutral", "amused", "happy", "concerned", "surprised", "thinking"):
            return emotion
    except Exception as e:
        logger.debug("LLM emotion detection failed: %s", e)

    return detect_emotion(text)
