"""Talking speed (Step 13): a finished line made faster or slower, without changing its pitch.

It is applied in the shared clean-up (audio_cleanup.clean_up), before the lip file is made, so the lips follow the new length and the
game waits for it. Every newer service has it (voice_style.SPEEDS, SETUP_SPEED); Piper, XTTS and xVASynth don't.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import numpy as np

UNCHANGED = 1.0
CLOSE_ENOUGH = 0.005 # a speed this close to 1 leaves the line as it is


class SpeedChange(ABC):
    """Makes a line faster or slower"""

    @abstractmethod
    def changed(self, samples: np.ndarray, rate: int, speed: float) -> np.ndarray:
        """The line at *speed* (1.25 is a quarter faster), at the same pitch and sample rate. Raises when it can't"""

    def apply(self, samples: np.ndarray, rate: int, speed: float) -> np.ndarray:
        """The line at *speed*. A speed of 1, or a change that fails, leaves it as it is"""
        if samples.size == 0 or abs(speed - UNCHANGED) < CLOSE_ENOUGH:
            return samples
        try:
            return self.changed(samples, rate, speed)
        except Exception as e:
            logging.warning(f"Could not change the line's Talking speed: {e}. It plays at its own speed.")
            return samples


class FilterSpeedChange(SpeedChange):
    """ffmpeg's atempo filter, run inside Mantella (voice_effects.run_filters): the length changes, the pitch doesn't"""

    def changed(self, samples: np.ndarray, rate: int, speed: float) -> np.ndarray:
        from src.tts.voice_effects import run_filters
        found = run_filters(samples, rate, [("atempo", f"{speed:.4f}")])
        if found.size == 0:
            raise ValueError("no sound came back")
        return found


SPEED_CHANGE: SpeedChange = FilterSpeedChange()
