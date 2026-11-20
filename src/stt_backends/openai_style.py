"""The OpenAI-style format: the form OpenAI, Groq, Mistral, OpenRouter, whisper.cpp, Speaches, LocalAI, Together, Fireworks and others accept.
A multipart POST with the recording, the model, the language and the hint, and the text back as JSON"""
from __future__ import annotations

from typing import Any

from src.stt_backends.cleanup import names_within
from src.stt_backends.service_format import ServiceFormat, hide_keys
from src.stt_backends.service_format import endpoint as _endpoint

TRANSCRIPTIONS_PATH = "/audio/transcriptions"

__all__ = ["OpenAIStyleFormat", "endpoint", "hide_keys"]


def endpoint(address: str) -> str:
    """The full address: a base address gets /audio/transcriptions added; a full path such as whisper.cpp's /inference is kept"""
    return _endpoint(address, TRANSCRIPTIONS_PATH)


class OpenAIStyleFormat(ServiceFormat):
    ENDING = TRANSCRIPTIONS_PATH

    def _request(self, sound: bytes, hint: str, names: list[str], language: str) -> dict[str, Any]:
        data: list[tuple[str, str]] = [] # a list, so a field can repeat
        if self.model:
            data.append(("model", self.model))
        if language:
            data.append(("language", language))
        if self.sends_prompt and hint:
            data.append(("prompt", hint))
        if self.names_field:
            data.extend((self.names_field, name) for name in names_within(names))
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        return {"files": {"file": ("audio.wav", sound, "audio/wav")}, "data": data, "headers": headers}

    def _text(self, body: Any) -> str:
        return body.get("text", "") if isinstance(body, dict) else ""
