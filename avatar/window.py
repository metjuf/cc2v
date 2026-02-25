"""Eigy AI Assistant — Pygame avatar window (dual split-screen).

Runs in the MAIN thread (macOS requirement for SDL2/Pygame).
Receives events from the chat thread via avatar_queue.
Left panel = Eigy, Right panel = Delan.
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


# ── AvatarPanel — encapsulates one avatar's state ────────────────


class AvatarPanel:
    """One avatar panel (Eigy or Delan). Owns its own animator, renderer, and cached surfaces."""

    def __init__(self, assistant_id: str, panel_size: tuple[int, int]):
        self.assistant_id = assistant_id
        self.panel_size = panel_size
        cfg = config.ASSISTANTS[assistant_id]
        self.name = cfg["name"]

        face_dir = config.ASSETS_DIR / cfg["face_dir"]
        self.animator = Animator()
        self.renderer = FaceRenderer(face_dir, panel_size)

        # Cached surfaces (per-panel)
        self._bg_surface: pygame.Surface | None = None
        self._vignette_surface: pygame.Surface | None = None
        self._glow_cache: dict[str, pygame.Surface] = {}
        self._particle_dot: pygame.Surface | None = None
        self._particles: list[_Particle] = []

        # Status font (set after pygame.init)
        self._status_font: pygame.font.Font | None = None

    def init_font(self) -> None:
        """Initialize font (must be called after pygame.init)."""
        try:
            self._status_font = pygame.font.SysFont("Helvetica Neue", 16)
        except Exception:
            self._status_font = pygame.font.SysFont(None, 18)

    def handle_event(self, event: dict) -> None:
        """Handle an avatar queue event targeted at this panel."""
        evt_type = event.get("type")

        if evt_type == "thinking_start":
            self.animator.set_state("thinking")
        elif evt_type == "thinking_end":
            self.animator.set_state("idle")
        elif evt_type == "speaking_start":
            self.animator.set_state("speaking")
        elif evt_type == "speaking_end":
            pass
        elif evt_type == "audio_amplitude":
            self.animator.set_amplitude(event.get("value", 0.0))
        elif evt_type == "audio_start":
            self.animator.set_state("speaking")
        elif evt_type == "audio_end":
            self.animator.set_state("idle")
        elif evt_type == "emotion":
            self.animator.set_emotion(event.get("value", "neutral"))

    def render(self, screen: pygame.Surface, x_origin: int, dt: float) -> None:
        """Render this panel onto screen at x_origin offset."""
        w, h = self.panel_size
        self.animator.update(dt)
        state = self.animator.get_render_state()

        # Create a temporary surface for this panel
        panel_surf = pygame.Surface(self.panel_size, pygame.SRCALPHA)

        # 1. Gradient background
        if self._bg_surface is None:
            self._bg_surface = self._build_background()
        panel_surf.blit(self._bg_surface, (0, 0))

        # 2. Floating particles
        self._update_and_draw_particles(panel_surf, dt)

        # 3. Glow aura
        self._draw_glow(
            panel_surf,
            state.get("emotion", "neutral"),
            state.get("breath_phase", 0.0),
            state.get("y_offset", 0.0),
        )

        # 4. Face layers
        self.renderer.render(panel_surf, state)

        # 5. Vignette
        self._draw_vignette(panel_surf)

        # 6. Status indicator
        if self._status_font:
            self._draw_status(
                panel_surf,
                state.get("state", "idle"),
                self.animator.time,
                state.get("amplitude", 0.0),
            )

        # Blit panel onto main screen
        screen.blit(panel_surf, (x_origin, 0))

    # ── Private: cached surface builders ──────────────────────────

    def _build_background(self) -> pygame.Surface:
        w, h = self.panel_size
        surface = pygame.Surface(self.panel_size)
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

    def _update_and_draw_particles(self, surface: pygame.Surface, dt: float) -> None:
        w, h = self.panel_size
        if not self._particles:
            self._particles = [_Particle(w, h) for _ in range(_PARTICLE_COUNT)]
        if self._particle_dot is None:
            self._particle_dot = pygame.Surface((6, 6), pygame.SRCALPHA)
            pygame.draw.circle(self._particle_dot, (255, 255, 255, 255), (3, 3), 3)

        time = self.animator.time
        for p in self._particles:
            p.y -= p.speed * dt
            x_draw = p.x + p.drift_amp * math.sin(time * p.drift_freq * 2 * math.pi)
            if p.y < -10:
                p.y = h + 10
                p.x = random.uniform(0, w)
            diameter = max(2, int(p.radius * 2))
            dot = pygame.transform.smoothscale(self._particle_dot, (diameter, diameter))
            dot.set_alpha(p.alpha)
            surface.blit(dot, (int(x_draw) - diameter // 2, int(p.y) - diameter // 2))

    def _get_glow_surface(self, emotion: str) -> pygame.Surface:
        if emotion in self._glow_cache:
            return self._glow_cache[emotion]
        color = GLOW_COLORS.get(emotion, GLOW_COLORS["neutral"])
        w, h = self.panel_size
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
            pygame.draw.ellipse(surface, (r, g, b, alpha), (cx - rx, cy - ry, rx * 2, ry * 2))
        self._glow_cache[emotion] = surface
        return surface

    def _draw_glow(
        self,
        surface: pygame.Surface,
        emotion: str,
        breath_phase: float,
        y_offset: float,
    ) -> None:
        glow = self._get_glow_surface(emotion)
        gw, gh = glow.get_size()
        w, h = self.panel_size
        x = (w - gw) // 2
        y = (h - gh) // 2 + int(y_offset)
        pulse = 0.85 + 0.15 * math.sin(breath_phase * 2 * math.pi)
        glow_frame = glow.copy()
        glow_frame.set_alpha(int(255 * pulse))
        surface.blit(glow_frame, (x, y))

    def _draw_vignette(self, surface: pygame.Surface) -> None:
        if self._vignette_surface is None:
            w, h = self.panel_size
            self._vignette_surface = pygame.Surface(self.panel_size, pygame.SRCALPHA)
            cx, cy = w // 2, h // 2
            max_dist = (cx**2 + cy**2) ** 0.5
            for radius in range(0, int(max_dist), 4):
                alpha = int(min(80, (radius / max_dist) ** 2 * 120))
                if alpha < 2:
                    continue
                rect = pygame.Rect(cx - radius, cy - radius, radius * 2, radius * 2)
                pygame.draw.rect(self._vignette_surface, (0, 0, 0, alpha), rect, 4)
        surface.blit(self._vignette_surface, (0, 0))

    def _draw_status(
        self,
        surface: pygame.Surface,
        state: str,
        time: float,
        amplitude: float,
    ) -> None:
        w, h = self.panel_size
        center_x = w // 2
        base_y = h - 35

        name_surface = self._status_font.render(self.name, True, (120, 120, 130))
        name_rect = name_surface.get_rect(center=(center_x, base_y))
        surface.blit(name_surface, name_rect)

        indicator_y = base_y + 18
        if state == "thinking":
            self._draw_thinking_dots(surface, center_x, indicator_y, time)
        elif state == "speaking":
            self._draw_sound_waves(surface, center_x, indicator_y, time, amplitude)

    @staticmethod
    def _draw_thinking_dots(surface: pygame.Surface, cx: int, cy: int, time: float) -> None:
        dot_spacing = 12
        dot_radius = 3
        for i in range(3):
            phase = (time * 2.0 - i * 0.3) % 1.0
            bounce = max(0, math.sin(phase * math.pi)) * 6
            x = cx + (i - 1) * dot_spacing
            y = int(cy - bounce)
            alpha = int(100 + 100 * (bounce / 6.0))
            dot_surf = pygame.Surface((dot_radius * 2, dot_radius * 2), pygame.SRCALPHA)
            pygame.draw.circle(dot_surf, (180, 150, 60, alpha), (dot_radius, dot_radius), dot_radius)
            surface.blit(dot_surf, (x - dot_radius, y - dot_radius))

    @staticmethod
    def _draw_sound_waves(
        surface: pygame.Surface, cx: int, cy: int, time: float, amplitude: float
    ) -> None:
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
            surface.blit(bar_surf, (x, y))


# ── Separator ─────────────────────────────────────────────────────


def _draw_separator(screen: pygame.Surface, x: int, h: int) -> None:
    """Draw a subtle vertical separator line."""
    sep_surf = pygame.Surface((2, h), pygame.SRCALPHA)
    for y in range(h):
        alpha = int(30 + 20 * math.sin(y / h * math.pi))
        sep_surf.set_at((0, y), (80, 80, 90, alpha))
        sep_surf.set_at((1, y), (80, 80, 90, alpha // 2))
    screen.blit(sep_surf, (x, 0))


# ── Main avatar loop ─────────────────────────────────────────────


def avatar_main(
    avatar_queue: queue.Queue,
    audio_player,
) -> None:
    """Main avatar window entry point. MUST run in the main thread.

    Renders two panels side by side: Eigy (left) + Delan (right).
    """
    pygame.init()

    full_w = config.AVATAR_WINDOW_WIDTH
    full_h = config.AVATAR_WINDOW_HEIGHT
    screen = pygame.display.set_mode((full_w, full_h))
    pygame.display.set_caption("Eigy & Delan")

    clock = pygame.time.Clock()

    # Panel dimensions (half width each)
    panel_w = full_w // 2
    panel_size = (panel_w, full_h)

    # Create panels
    panels: dict[str, AvatarPanel] = {}
    for aid in ("eigy", "delan"):
        panel = AvatarPanel(aid, panel_size)
        panel.init_font()
        panels[aid] = panel

    # Initialize audio mixer
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

            evt_type = evt.get("type")
            if evt_type == "quit":
                running = False
                break

            if evt_type == "toggle_avatar":
                pygame.display.iconify()
                continue

            # Route event to target panel, or broadcast to all
            target = evt.get("target")
            if target and target in panels:
                panels[target].handle_event(evt)
            else:
                for panel in panels.values():
                    panel.handle_event(evt)

        # 3. Update audio player
        audio_player.update()

        # 4. Render panels
        screen.fill((26, 26, 26))
        panels["eigy"].render(screen, 0, dt)
        panels["delan"].render(screen, panel_w, dt)

        # 5. Separator line
        _draw_separator(screen, panel_w - 1, full_h)

        pygame.display.flip()

    pygame.quit()
