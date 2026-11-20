"""The ElevenLabs-style format: a multipart POST with the recording, `model_id`, `language_code` and each name as its own `keyterms` field"""
from __future__ import annotations

from typing import Any

from src.stt_backends.cleanup import names_within
from src.stt_backends.service_format import ServiceFormat

MAX_TERM_CHARACTERS = 50
MAX_TERM_WORDS = 5
MAX_TERMS = 100 # more than 100 bills every line as at least 20 seconds
NOT_ALLOWED = set("<>{}[]\\")


def keyterms(names: list[str]) -> list[str]:
    """The names ElevenLabs accepts as words to listen for, most likely first"""
    usable = [name for name in names_within(names)
              if len(name) <= MAX_TERM_CHARACTERS and len(name.split()) <= MAX_TERM_WORDS and not (set(name) & NOT_ALLOWED)]
    return usable[:MAX_TERMS]


class ElevenLabsStyleFormat(ServiceFormat):
    ENDING = "/speech-to-text"

    def _request(self, sound: bytes, hint: str, names: list[str], language: str) -> dict[str, Any]:
        data: list[tuple[str, str]] = [("model_id", self.model)] if self.model else []
        if language:
            data.append(("language_code", language))
        data.append(("tag_audio_events", "false")) # no "(laughter)" in the player's line
        data.extend(("keyterms", name) for name in keyterms(names))
        headers = {"xi-api-key": self.key} if self.key else {}
        return {"files": {"file": ("audio.wav", sound, "audio/wav")}, "data": data, "headers": headers}

    def _text(self, body: Any) -> str:
        return body["text"]
