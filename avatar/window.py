"""Eigy AI Assistant — Pygame avatar window.

Runs in the MAIN thread (macOS requirement for SDL2/Pygame).
Receives events from the chat thread via avatar_queue.
"""

from __future__ import annotations

import logging
import math
import queue
import random

import pygame

import config
from avatar.animator import Animator
from avatar.face_renderer import FaceRenderer

logger = logging.getLogger(__name__)


# ── Cached surfaces ──────────────────────────────────────────────

_bg_surface: pygame.Surface | None = None
_vignette_surface: pygame.Surface | None = None
_glow_cache: dict[str, pygame.Surface] = {}
_particle_dot: pygame.Surface | None = None


# ── Emotion → glow color mapping ─────────────────────────────────

GLOW_COLORS: dict[str, tuple[int, int, int]] = {
    "neutral":   (60, 120, 200),
    "amused":    (80, 180, 100),
    "happy":     (220, 180, 60),
    "concerned": (200, 80, 60),
    "surprised": (160, 80, 200),
    "thinking":  (220, 150, 50),
    "speaking":  (60, 120, 200),
}


# ── Particles ────────────────────────────────────────────────────

_PARTICLE_COUNT = 20


class _Particle:
    __slots__ = ("x", "y", "radius", "alpha", "speed", "drift_freq", "drift_amp")

    def __init__(self, w: int, h: int):
        self.x = random.uniform(0, w)
        self.y = random.uniform(0, h)
        self.radius = random.uniform(1.0, 2.5)
        self.alpha = random.randint(15, 40)
        self.speed = random.uniform(5.0, 15.0)
        self.drift_freq = random.uniform(0.3, 0.8)
        self.drift_amp = random.uniform(8.0, 20.0)


_particles: list[_Particle] = []


# ── Background (radial gradient, cached) ─────────────────────────


def _build_background(size: tuple[int, int]) -> pygame.Surface:
    """Pre-render a subtle radial gradient background."""
    w, h = size
    surface = pygame.Surface(size)
    surface.fill((26, 26, 26))

    cx, cy = w // 2, h // 2
    max_radius = int((cx**2 + cy**2) ** 0.5)

    # Draw from outside-in: edge (38,38,42) → center (26,26,26)
    for r in range(max_radius, 0, -3):
        t = r / max_radius
        cr = int(26 + 12 * t)
        cg = int(26 + 12 * t)
        cb = int(26 + 16 * t)
        pygame.draw.circle(surface, (cr, cg, cb), (cx, cy), r)

    return surface


# ── Floating particles ───────────────────────────────────────────


def _update_and_draw_particles(
    screen: pygame.Surface,
    size: tuple[int, int],
    dt: float,
    time: float,
) -> None:
    """Update and draw floating ambient particles."""
    global _particles, _particle_dot
    w, h = size

    if not _particles:
        _particles = [_Particle(w, h) for _ in range(_PARTICLE_COUNT)]

    if _particle_dot is None:
        _particle_dot = pygame.Surface((6, 6), pygame.SRCALPHA)
        pygame.draw.circle(_particle_dot, (255, 255, 255, 255), (3, 3), 3)

    for p in _particles:
        p.y -= p.speed * dt
        x_draw = p.x + p.drift_amp * math.sin(time * p.drift_freq * 2 * math.pi)

        if p.y < -10:
            p.y = h + 10
            p.x = random.uniform(0, w)

        diameter = max(2, int(p.radius * 2))
        dot = pygame.transform.smoothscale(_particle_dot, (diameter, diameter))
        dot.set_alpha(p.alpha)
        screen.blit(dot, (int(x_draw) - diameter // 2, int(p.y) - diameter // 2))


# ── Glow aura ────────────────────────────────────────────────────


def _get_glow_surface(
    emotion: str,
    size: tuple[int, int],
) -> pygame.Surface:
    """Get or build a cached glow surface for the given emotion."""
    if emotion in _glow_cache:
        return _glow_cache[emotion]

    color = GLOW_COLORS.get(emotion, GLOW_COLORS["neutral"])
    w, h = size

    glow_w = int(w * 0.75)
    glow_h = int(h * 0.70)

    surface = pygame.Surface((glow_w, glow_h), pygame.SRCALPHA)
    cx, cy = glow_w // 2, glow_h // 2

    steps = 40
    for i in range(steps):
        t = i / steps
        rx = int(cx * (1.0 - t * 0.6))
        ry = int(cy * (1.0 - t * 0.6))
        alpha = int(18 * math.exp(-((t - 0.3) ** 2) / 0.08))
        if alpha < 1:
            continue
        r, g, b = color
        pygame.draw.ellipse(
            surface, (r, g, b, alpha),
            (cx - rx, cy - ry, rx * 2, ry * 2),
        )

    _glow_cache[emotion] = surface
    return surface


def _draw_glow(
    screen: pygame.Surface,
    size: tuple[int, int],
    emotion: str,
    breath_phase: float,
    y_offset: float,
) -> None:
    """Draw the emotion-colored glow aura behind the face."""
    glow = _get_glow_surface(emotion, size)
    gw, gh = glow.get_size()
    w, h = size

    x = (w - gw) // 2
    y = (h - gh) // 2 + int(y_offset)

    pulse = 0.85 + 0.15 * math.sin(breath_phase * 2 * math.pi)

    glow_frame = glow.copy()
    glow_frame.set_alpha(int(255 * pulse))
    screen.blit(glow_frame, (x, y))


# ── Status indicator ─────────────────────────────────────────────


def _draw_status(
    screen: pygame.Surface,
    size: tuple[int, int],
    state: str,
    time: float,
    amplitude: float,
    font: pygame.font.Font,
) -> None:
    """Draw the status indicator at the bottom of the window."""
    w, h = size
    center_x = w // 2
    base_y = h - 35

    # Name
    name_surface = font.render(config.ASSISTANT_NAME, True, (120, 120, 130))
    name_rect = name_surface.get_rect(center=(center_x, base_y))
    screen.blit(name_surface, name_rect)

    # State animation
    indicator_y = base_y + 18

    if state == "thinking":
        _draw_thinking_dots(screen, center_x, indicator_y, time)
    elif state == "speaking":
        _draw_sound_waves(screen, center_x, indicator_y, time, amplitude)


def _draw_thinking_dots(
    screen: pygame.Surface,
    cx: int,
    cy: int,
    time: float,
) -> None:
    """Draw animated thinking dots with staggered bounce."""
    dot_spacing = 12
    dot_radius = 3

    for i in range(3):
        phase = (time * 2.0 - i * 0.3) % 1.0
        bounce = max(0, math.sin(phase * math.pi)) * 6

        x = cx + (i - 1) * dot_spacing
        y = int(cy - bounce)

        alpha = int(100 + 100 * (bounce / 6.0))

        dot_surf = pygame.Surface((dot_radius * 2, dot_radius * 2), pygame.SRCALPHA)
        pygame.draw.circle(
            dot_surf, (180, 150, 60, alpha),
            (dot_radius, dot_radius), dot_radius,
        )
        screen.blit(dot_surf, (x - dot_radius, y - dot_radius))


def _draw_sound_waves(
    screen: pygame.Surface,
    cx: int,
    cy: int,
    time: float,
    amplitude: float,
) -> None:
    """Draw animated sound wave bars during speaking."""
    bar_count = 5
    bar_width = 3
    bar_spacing = 6
    max_height = 12

    total_width = bar_count * bar_width + (bar_count - 1) * bar_spacing
    start_x = cx - total_width // 2

    for i in range(bar_count):
        phase = time * (3.0 + i * 0.7) + i * 0.5
        wave = (math.sin(phase) + 1.0) / 2.0

        height = max(2, int(max_height * wave * max(0.3, amplitude)))

        x = start_x + i * (bar_width + bar_spacing)
        y = cy - height // 2

        alpha = int(120 + 80 * wave)
        bar_surf = pygame.Surface((bar_width, height), pygame.SRCALPHA)
        bar_surf.fill((80, 140, 220, alpha))
        screen.blit(bar_surf, (x, y))


# ── Vignette overlay (cached) ────────────────────────────────────


def _draw_vignette(screen: pygame.Surface, size: tuple[int, int]) -> None:
    """Draw a simple dark vignette overlay (cached)."""
    global _vignette_surface
    if _vignette_surface is None:
        _vignette_surface = pygame.Surface(size, pygame.SRCALPHA)
        w, h = size
        cx, cy = w // 2, h // 2
        max_dist = (cx**2 + cy**2) ** 0.5
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


# ── Main avatar loop ─────────────────────────────────────────────


def avatar_main(
    avatar_queue: queue.Queue,
    audio_player,
) -> None:
    """Main avatar window entry point. MUST run in the main thread."""
    global _bg_surface

    pygame.init()

    size = (config.AVATAR_WINDOW_WIDTH, config.AVATAR_WINDOW_HEIGHT)
    screen = pygame.display.set_mode(size)
    pygame.display.set_caption(config.ASSISTANT_NAME)

    clock = pygame.time.Clock()
    animator = Animator()
    renderer = FaceRenderer(config.DEFAULT_FACE_DIR, size)

    # Status indicator font
    try:
        status_font = pygame.font.SysFont("Helvetica Neue", 16)
    except Exception:
        status_font = pygame.font.SysFont(None, 18)

    # Give audio player the mixer reference
    audio_player.set_audio_manager(None)

    # Pre-build background
    _bg_surface = _build_background(size)

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

        # 5. Render pipeline
        # 5a. Gradient background
        screen.blit(_bg_surface, (0, 0))

        # 5b. Floating particles
        _update_and_draw_particles(screen, size, dt, animator.time)

        # 5c. Glow aura (behind face)
        _draw_glow(
            screen, size,
            state.get("emotion", "neutral"),
            state.get("breath_phase", 0.0),
            state.get("y_offset", 0.0),
        )

        # 5d. Face layers
        renderer.render(screen, state)

        # 5e. Vignette overlay
        _draw_vignette(screen, size)

        # 5f. Status indicator
        _draw_status(
            screen, size,
            state.get("state", "idle"),
            animator.time,
            state.get("amplitude", 0.0),
            status_font,
        )

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
