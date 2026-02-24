"""Eigy AI Assistant — Layered PNG face renderer.

Loads transparent PNG layers from a face directory and composites
them onto a Pygame surface with alpha blending support for
smooth transitions between states.
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
            name = png_file.stem
            try:
                surface = pygame.image.load(str(png_file)).convert_alpha()
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

    def _blit_layer(
        self,
        target: pygame.Surface,
        name: str,
        x_offset: float,
        y_offset: float,
        alpha: int = 255,
    ) -> None:
        """Blit a named layer with optional alpha onto target."""
        surface = self.layers.get(name)
        if surface is None:
            return
        ww, wh = self.window_size
        sw, sh = surface.get_size()
        x = (ww - sw) // 2 + int(x_offset)
        y = (wh - sh) // 2 + int(y_offset)

        if alpha >= 255:
            target.blit(surface, (x, y))
        else:
            tmp = surface.copy()
            tmp.set_alpha(alpha)
            target.blit(tmp, (x, y))

    def render(
        self,
        target: pygame.Surface,
        state: dict,
    ) -> None:
        """Render composited face onto target surface.

        state dict keys:
            eyes: str — layer name suffix ("open", "half", "closed")
            eyes_blend: float — 0.0 = primary, 1.0 = fully secondary layer
            eyes_secondary: str — second eye layer to blend toward
            mouth: str — primary mouth layer suffix
            mouth_blend: float — blend toward mouth_secondary
            mouth_secondary: str — second mouth layer to blend toward
            eyebrows: str — layer name suffix
            x_offset, y_offset: float — pixel offsets
        """
        xo = state.get("x_offset", 0.0)
        yo = state.get("y_offset", 0.0)

        # 1. Base (always full alpha)
        self._blit_layer(target, "base", xo, yo)

        # 2. Eyes — crossfade between two layers
        eyes_primary = f"eyes_{state.get('eyes', 'open')}"
        eyes_blend = state.get("eyes_blend", 0.0)
        eyes_secondary = state.get("eyes_secondary")

        if eyes_blend > 0.01 and eyes_secondary:
            sec_name = f"eyes_{eyes_secondary}"
            primary_alpha = int(255 * (1.0 - eyes_blend))
            secondary_alpha = int(255 * eyes_blend)
            self._blit_layer(target, eyes_primary, xo, yo, primary_alpha)
            self._blit_layer(target, sec_name, xo, yo, secondary_alpha)
        else:
            self._blit_layer(target, eyes_primary, xo, yo)

        # 3. Eyebrows
        eyebrows = f"eyebrows_{state.get('eyebrows', 'neutral')}"
        self._blit_layer(target, eyebrows, xo, yo)

        # 4. Mouth — crossfade between two layers
        mouth_primary = f"mouth_{state.get('mouth', 'closed')}"
        mouth_blend = state.get("mouth_blend", 0.0)
        mouth_secondary = state.get("mouth_secondary")

        if mouth_blend > 0.01 and mouth_secondary:
            sec_name = f"mouth_{mouth_secondary}"
            primary_alpha = int(255 * (1.0 - mouth_blend))
            secondary_alpha = int(255 * mouth_blend)
            self._blit_layer(target, mouth_primary, xo, yo, primary_alpha)
            self._blit_layer(target, sec_name, xo, yo, secondary_alpha)
        else:
            self._blit_layer(target, mouth_primary, xo, yo)

    @property
    def available_layers(self) -> list[str]:
        return sorted(self.layers.keys())
