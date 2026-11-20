"""What every way of turning the player's recording into text has in common: a speech program on this PC, or a format for sending the recording somewhere"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

SAMPLING_RATE = 16000

# The kinds of problems, each with its own plain message
NO_KEY = "no_key"
KEY_REFUSED = "key_refused"
UNREACHABLE = "unreachable"
UNKNOWN_MODEL = "unknown_model"
LANGUAGE = "language"
NOT_DOWNLOADED = "not_downloaded"
FAILED = "failed"


class SpeechError(Exception):
    """A problem that stops a recording from becoming text, with a message that says what to fix"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class SpeechBackend(ABC):
    """Turns a recording (16 kHz mono, float32) into text"""

    takes_hint: bool = True # False for models that cannot be told the names, e.g. Moonshine

    def load(self) -> None:
        """Gets ready before the first recording, e.g. loads the model. Nothing to do for formats"""

    @abstractmethod
    def transcribe(self, audio: np.ndarray, hint: str, names: list[str], language: str) -> str:
        """The text of the recording. *hint* is the sentence of names, *names* the same names one by one.
        Raises SpeechError when it can't be done"""

    def forget_failed_load(self) -> None:
        """A model that failed to load is not tried again on every line; this lets the next try (e.g. the Test button) load it again"""

    def set_key(self, key: str) -> None:
        """A new key from the Key box. Only formats sending to a service use one"""

    def close(self) -> None:
        """Lets go of the model or connection"""
