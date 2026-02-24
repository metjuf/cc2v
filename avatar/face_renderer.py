"""Holly AI Assistant — Layered PNG face renderer.

Loads transparent PNG layers from a face directory and composites
them onto a Pygame surface in the correct order.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pygame

logger = logging.getLogger(__name__)

# Layer render order (bottom to top)
LAYER_ORDER = ["base", "eyes", "eyebrows", "mouth"]


class FaceRenderer:
    """Composites PNG layers into a face on a Pygame surface."""

    def __init__(self, face_dir: str | Path, window_size: tuple[int, int]):
        self.face_dir = Path(face_dir)
        self.window_size = window_size
        self.layers: dict[str, pygame.Surface] = {}
        self._load_layers()

    def _load_layers(self) -> None:
        """Load all PNG files from the face directory."""
        if not self.face_dir.exists():
            logger.warning("Face directory not found: %s", self.face_dir)
            return

        for png_file in sorted(self.face_dir.glob("*.png")):
            name = png_file.stem  # e.g., "eyes_open", "mouth_smile"
            try:
                surface = pygame.image.load(str(png_file)).convert_alpha()
                # Scale to fit window while preserving aspect ratio
                surface = self._scale_to_window(surface)
                self.layers[name] = surface
            except Exception as e:
                logger.warning("Failed to load layer %s: %s", png_file.name, e)

        logger.info("Loaded %d face layers from %s", len(self.layers), self.face_dir)

    def _scale_to_window(self, surface: pygame.Surface) -> pygame.Surface:
        """Scale surface to fill ~85% of window height, centered."""
        sw, sh = surface.get_size()
        ww, wh = self.window_size
        target_h = int(wh * 0.85)
        scale = target_h / sh
        new_w = int(sw * scale)
        new_h = int(sh * scale)
        return pygame.transform.smoothscale(surface, (new_w, new_h))

    def render(
        self,
        target: pygame.Surface,
        eyes: str = "open",
        mouth: str = "closed",
        eyebrows: str = "neutral",
        x_offset: float = 0.0,
        y_offset: float = 0.0,
    ) -> None:
        """Render composited face onto target surface.

        Args:
            target: Pygame surface to draw on.
            eyes: One of "open", "half", "closed".
            mouth: One of "closed", "open_1", "open_2", "open_3",
                   "smile", "sad", "surprised", "smirk".
            eyebrows: One of "neutral", "raised", "frown".
            x_offset: Horizontal offset in pixels (for eye drift).
            y_offset: Vertical offset in pixels (for breathing).
        """
        ww, wh = self.window_size

        layer_names = {
            "base": "base",
            "eyes": f"eyes_{eyes}",
            "eyebrows": f"eyebrows_{eyebrows}",
            "mouth": f"mouth_{mouth}",
        }

        for layer_key in LAYER_ORDER:
            name = layer_names.get(layer_key, "")
            surface = self.layers.get(name)
            if surface is None:
                continue

            sw, sh = surface.get_size()
            x = (ww - sw) // 2 + int(x_offset)
            y = (wh - sh) // 2 + int(y_offset)
            target.blit(surface, (x, y))

    @property
    def available_layers(self) -> list[str]:
        return sorted(self.layers.keys())
