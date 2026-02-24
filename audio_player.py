"""Eigy AI Assistant — Audio playback with amplitude extraction.

Queue-based playback via pygame.mixer with RMS amplitude
extraction for lip sync animation.
"""

from __future__ import annotations

import logging
import math
import queue

import numpy as np
import pygame

logger = logging.getLogger(__name__)


class AudioPlayer:
    """Audio playback with amplitude extraction for lip sync.

    Call ``update()`` from the main thread each frame to process
    the playback queue and emit amplitude events.
    """

    def __init__(self, avatar_queue: queue.Queue | None = None):
        self.avatar_queue = avatar_queue
        self.audio_queue: queue.Queue[str | None] = queue.Queue()
        self.amplitude_data: list[float] = []
        self.start_time: int = 0
        self.playing = False
        self.volume = 1.0
        self._mixer_initialized = False

    def set_audio_manager(self, base) -> None:
        """Initialize audio mixer."""
        self._ensure_mixer()

    def _ensure_mixer(self) -> None:
        """Initialize pygame.mixer if not already done."""
        if not self._mixer_initialized:
            try:
                if not pygame.mixer.get_init():
                    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
                pygame.mixer.music.set_volume(self.volume)
                self._mixer_initialized = True
            except Exception as e:
                logger.warning("Failed to initialize pygame.mixer: %s", e)

    def enqueue(self, audio_path: str) -> None:
        """Add an audio file to the playback queue."""
        self.audio_queue.put(audio_path)

    def play(self, audio_path: str) -> None:
        """Play an audio file and extract amplitude data for lip sync."""
        self._ensure_mixer()
        if not self._mixer_initialized:
            return

        # Extract amplitude envelope (optional — needs pydub + ffmpeg)
        self.amplitude_data = []
        try:
            from pydub import AudioSegment

            audio = AudioSegment.from_file(audio_path)
            samples = np.array(audio.get_array_of_samples(), dtype=np.float32)

            if audio.channels == 2:
                samples = samples.reshape((-1, 2)).mean(axis=1)

            frame_size = int(audio.frame_rate * 0.033)
            for i in range(0, len(samples), frame_size):
                chunk = samples[i : i + frame_size]
                if len(chunk) == 0:
                    continue
                rms = np.sqrt(np.mean(chunk**2)) / 32768.0
                self.amplitude_data.append(min(rms * 3.0, 1.0))
        except Exception as e:
            logger.debug("Amplitude extraction skipped: %s", e)

        # Play via pygame.mixer
        try:
            pygame.mixer.music.load(audio_path)
            pygame.mixer.music.set_volume(self.volume)
            pygame.mixer.music.play()
            self.start_time = pygame.time.get_ticks()
            self.playing = True

            if self.avatar_queue:
                self.avatar_queue.put({"type": "audio_start"})
        except Exception as e:
            logger.warning("Failed to play audio %s: %s", audio_path, e)
            self.playing = False

    def update(self) -> None:
        """Called each frame from main thread. Sends amplitude events and manages queue."""
        self._ensure_mixer()
        if not self._mixer_initialized:
            return

        if self.playing and pygame.mixer.music.get_busy():
            # Send current amplitude to avatar
            if self.avatar_queue:
                elapsed = (pygame.time.get_ticks() - self.start_time) / 1000.0
                if self.amplitude_data:
                    idx = int(elapsed * 30)
                    if idx < len(self.amplitude_data):
                        amp = self.amplitude_data[idx]
                    else:
                        amp = 0.0
                else:
                    # Synthetic amplitude fallback (pydub/ffmpeg unavailable)
                    amp = (0.3
                           + 0.2 * math.sin(elapsed * 8.0)
                           + 0.1 * math.sin(elapsed * 13.0)
                           + 0.05 * math.sin(elapsed * 21.0))
                self.avatar_queue.put({
                    "type": "audio_amplitude",
                    "value": max(0.0, min(1.0, amp)),
                })

        elif self.playing and not pygame.mixer.music.get_busy():
            # Current track finished
            self.playing = False
            self.amplitude_data = []
            if self.avatar_queue:
                self.avatar_queue.put({"type": "audio_amplitude", "value": 0.0})

            # Play next in queue
            if not self.audio_queue.empty():
                try:
                    next_path = self.audio_queue.get_nowait()
                    if next_path:
                        self.play(next_path)
                except queue.Empty:
                    pass
            else:
                if self.avatar_queue:
                    self.avatar_queue.put({"type": "audio_end"})

        elif not self.playing:
            # Not playing, check queue
            if not self.audio_queue.empty():
                try:
                    next_path = self.audio_queue.get_nowait()
                    if next_path:
                        self.play(next_path)
                except queue.Empty:
                    pass

    def stop(self) -> None:
        """Stop current playback and clear queue."""
        if self._mixer_initialized:
            pygame.mixer.music.stop()
        self.playing = False
        self.amplitude_data = []
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
        if self.avatar_queue:
            self.avatar_queue.put({"type": "audio_amplitude", "value": 0.0})
            self.avatar_queue.put({"type": "audio_end"})

    def set_volume(self, level: int) -> None:
        """Set volume (0-100)."""
        self.volume = max(0.0, min(1.0, level / 100.0))
        if self._mixer_initialized:
            pygame.mixer.music.set_volume(self.volume)
