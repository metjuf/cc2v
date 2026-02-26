"""Eigy AI Assistant — Pygame avatar window (spectrum visualizer).

Runs in the MAIN thread (macOS requirement for SDL2/Pygame).
Receives events from the chat thread via avatar_queue.
Displays a horizontal audio spectrum instead of an animated face.
"""

from __future__ import annotations

import logging
import math
import queue
import random

import pygame

import config
from avatar.animator import Animator

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


# ── Spectrum constants ───────────────────────────────────────────

_BAR_COUNT = 48
_BAR_WIDTH = 4
_BAR_GAP = 3
_BAR_MAX_HEIGHT = 180
_BAR_MIN_HEIGHT = 4


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
    cache_key = f"{emotion}_{size[0]}_{size[1]}"
    if cache_key in _glow_cache:
        return _glow_cache[cache_key]

    color = GLOW_COLORS.get(emotion, GLOW_COLORS["neutral"])
    w, h = size

    glow_w = int(w * 0.85)
    glow_h = int(h * 0.50)

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

    _glow_cache[cache_key] = surface
    return surface


def _draw_glow(
    screen: pygame.Surface,
    size: tuple[int, int],
    emotion: str,
    breath_phase: float,
) -> None:
    """Draw the emotion-colored glow aura behind the spectrum."""
    glow = _get_glow_surface(emotion, size)
    gw, gh = glow.get_size()
    w, h = size

    x = (w - gw) // 2
    y = (h - gh) // 2

    pulse = 0.85 + 0.15 * math.sin(breath_phase * 2 * math.pi)

    glow_frame = glow.copy()
    glow_frame.set_alpha(int(255 * pulse))
    screen.blit(glow_frame, (x, y))


# ── Spectrum visualizer ──────────────────────────────────────────


def _draw_spectrum(
    screen: pygame.Surface,
    size: tuple[int, int],
    time: float,
    state: str,
    amplitude: float,
    emotion: str,
    breath_phase: float,
) -> None:
    """Draw the horizontal audio spectrum bars.

    - Idle: minimal height, gentle sine wave (breathing)
    - Thinking: traveling wave pattern
    - Speaking: bars react to audio amplitude with pseudo-FFT distribution
    Center bars are taller, edge bars shorter → abstractly resembles a mouth.
    """
    w, h = size
    color = GLOW_COLORS.get(emotion, GLOW_COLORS["neutral"])

    total_width = _BAR_COUNT * _BAR_WIDTH + (_BAR_COUNT - 1) * _BAR_GAP
    start_x = (w - total_width) // 2
    center_y = h // 2

    for i in range(_BAR_COUNT):
        # Center factor: bars in the center are taller (0.0 at edges, 1.0 at center)
        t = i / (_BAR_COUNT - 1)  # 0..1
        center_factor = 1.0 - abs(t - 0.5) * 2.0  # 0 at edges, 1 at center
        center_factor = 0.3 + 0.7 * center_factor  # remap to 0.3..1.0

        if state == "speaking":
            # Pseudo-FFT: multiple sine waves per bar driven by amplitude
            s1 = math.sin(time * 5.0 + i * 0.4) * 0.5 + 0.5
            s2 = math.sin(time * 8.3 + i * 0.7) * 0.3 + 0.5
            s3 = math.sin(time * 12.1 + i * 1.1) * 0.2 + 0.5
            wave = (s1 + s2 + s3) / 3.0
            bar_h = _BAR_MIN_HEIGHT + (_BAR_MAX_HEIGHT - _BAR_MIN_HEIGHT) * amplitude * center_factor * wave
        elif state == "thinking":
            # Traveling wave
            wave = math.sin(time * 2.5 - i * 0.25) * 0.5 + 0.5
            bar_h = _BAR_MIN_HEIGHT + 40 * center_factor * wave
        else:
            # Idle: gentle breathing wave
            wave = math.sin(breath_phase * 2 * math.pi + i * 0.15) * 0.5 + 0.5
            bar_h = _BAR_MIN_HEIGHT + 8 * center_factor * wave

        bar_h = max(_BAR_MIN_HEIGHT, int(bar_h))
        x = start_x + i * (_BAR_WIDTH + _BAR_GAP)
        y = center_y - bar_h // 2

        # Per-bar color: brighter in center
        brightness = 0.6 + 0.4 * center_factor
        r = min(255, int(color[0] * brightness))
        g = min(255, int(color[1] * brightness))
        b = min(255, int(color[2] * brightness))

        # Alpha: base + dynamic
        alpha = int(140 + 115 * center_factor * (bar_h / max(_BAR_MAX_HEIGHT, 1)))

        bar_surf = pygame.Surface((_BAR_WIDTH, bar_h), pygame.SRCALPHA)
        # Gradient fill: brighter at center of bar
        for row in range(bar_h):
            row_t = abs(row / max(bar_h - 1, 1) - 0.5) * 2.0  # 0 at center, 1 at edges
            row_alpha = int(alpha * (0.7 + 0.3 * (1.0 - row_t)))
            bar_surf.fill((r, g, b, row_alpha), (0, row, _BAR_WIDTH, 1))

        screen.blit(bar_surf, (x, y))


# ── Status indicator ─────────────────────────────────────────────


def _draw_status(
    screen: pygame.Surface,
    size: tuple[int, int],
    state: str,
    time: float,
    font: pygame.font.Font,
) -> None:
    """Draw the assistant name at the bottom of the window."""
    w, h = size
    center_x = w // 2
    base_y = h - 25

    name_surface = font.render(config.ASSISTANT_NAME, True, (120, 120, 130))
    name_rect = name_surface.get_rect(center=(center_x, base_y))
    screen.blit(name_surface, name_rect)


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

        # 5c. Glow aura (behind spectrum)
        _draw_glow(
            screen, size,
            state.get("emotion", "neutral"),
            state.get("breath_phase", 0.0),
        )

        # 5d. Spectrum visualizer
        _draw_spectrum(
            screen, size,
            animator.time,
            state.get("state", "idle"),
            state.get("amplitude", 0.0),
            state.get("emotion", "neutral"),
            state.get("breath_phase", 0.0),
        )

        # 5e. Vignette overlay
        _draw_vignette(screen, size)

        # 5f. Status indicator
        _draw_status(
            screen, size,
            state.get("state", "idle"),
            animator.time,
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
