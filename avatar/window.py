"""Holly AI Assistant — Pygame avatar window.

Runs in the MAIN thread (macOS requirement for SDL2/Pygame).
Receives events from the chat thread via avatar_queue.
"""

from __future__ import annotations

import logging
import queue

import pygame

import config
from avatar.animator import Animator
from avatar.face_renderer import FaceRenderer

logger = logging.getLogger(__name__)

# Dark charcoal background
BG_COLOR = (26, 26, 26)


def avatar_main(
    avatar_queue: queue.Queue,
    audio_player,
) -> None:
    """Main avatar window entry point. MUST run in the main thread."""
    pygame.init()

    size = (config.AVATAR_WINDOW_WIDTH, config.AVATAR_WINDOW_HEIGHT)
    screen = pygame.display.set_mode(size)
    pygame.display.set_caption("Holly")

    clock = pygame.time.Clock()
    animator = Animator()
    renderer = FaceRenderer(config.DEFAULT_FACE_DIR, size)

    # Give audio player the mixer reference
    audio_player.set_audio_manager(None)

    running = True
    while running:
        dt = clock.tick(config.AVATAR_FPS) / 1000.0

        # 1. Handle Pygame events
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        # 2. Process avatar queue events
        while True:
            try:
                evt = avatar_queue.get_nowait()
            except queue.Empty:
                break
            if not _handle_event(evt, animator):
                running = False
                break

        # 3. Update audio player (handles playback queue + amplitude)
        audio_player.update()

        # 4. Update animator
        animator.update(dt)
        state = animator.get_render_state()

        # 5. Render
        screen.fill(BG_COLOR)
        renderer.render(
            screen,
            eyes=state["eyes"],
            mouth=state["mouth"],
            eyebrows=state["eyebrows"],
            x_offset=state["x_offset"],
            y_offset=state["y_offset"],
        )

        # 6. Vignette overlay
        _draw_vignette(screen, size)

        pygame.display.flip()

    pygame.quit()


def _handle_event(event: dict, animator: Animator) -> bool:
    """Handle an event from the avatar queue. Returns False to quit."""
    evt_type = event.get("type")

    if evt_type == "quit":
        return False

    elif evt_type == "thinking_start":
        animator.set_state("thinking")

    elif evt_type == "thinking_end":
        animator.set_state("idle")

    elif evt_type == "speaking_start":
        animator.set_state("speaking")

    elif evt_type == "speaking_end":
        # Don't reset — audio may still be playing.
        # audio_end will handle cleanup.
        pass

    elif evt_type == "audio_amplitude":
        amp = event.get("value", 0.0)
        animator.set_amplitude(amp)

    elif evt_type == "audio_start":
        animator.set_state("speaking")

    elif evt_type == "audio_end":
        animator.set_state("idle")

    elif evt_type == "emotion":
        animator.set_emotion(event.get("value", "neutral"))

    elif evt_type == "toggle_avatar":
        pygame.display.iconify()

    return True


# ── Vignette overlay ──────────────────────────────────────────────

_vignette_surface: pygame.Surface | None = None


def _draw_vignette(screen: pygame.Surface, size: tuple[int, int]) -> None:
    """Draw a simple dark vignette overlay (cached)."""
    global _vignette_surface
    if _vignette_surface is None:
        _vignette_surface = pygame.Surface(size, pygame.SRCALPHA)
        w, h = size
        cx, cy = w // 2, h // 2
        max_dist = (cx**2 + cy**2) ** 0.5
        # Draw concentric rectangles with increasing alpha at edges
        for radius in range(0, int(max_dist), 4):
            alpha = int(min(80, (radius / max_dist) ** 2 * 120))
            if alpha < 2:
                continue
            rect = pygame.Rect(
                cx - radius, cy - radius,
                radius * 2, radius * 2,
            )
            pygame.draw.rect(_vignette_surface, (0, 0, 0, alpha), rect, 4)

    screen.blit(_vignette_surface, (0, 0))
