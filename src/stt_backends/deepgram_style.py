"""The Deepgram-style format: the recording as the whole request body, the settings in the address, and each name as its own `keyterm`.
Names joined with commas in one keyterm are accepted without an error and then ignored, so each goes separately"""
from __future__ import annotations

from typing import Any

from src.stt_backends.cleanup import names_within
from src.stt_backends.service_format import ServiceFormat


class DeepgramStyleFormat(ServiceFormat):
    ENDING = "/listen"

    def _request(self, sound: bytes, hint: str, names: list[str], language: str) -> dict[str, Any]:
        params: list[tuple[str, str]] = [] # a list, so keyterm can repeat
        if self.model:
            params.append(("model", self.model))
        if language:
            params.append(("language", language))
        params.append(("punctuate", "true")) # without it, one long string with no full stops or question marks
        # filler_words is left off (Deepgram's default), so "um" and "uh" are dropped as with every other option
        if self.model.startswith("nova-3"): # only Nova-3 takes words to listen for
            params.extend(("keyterm", name) for name in names_within(names))
        headers = {"Content-Type": "audio/wav"}
        if self.key:
            headers["Authorization"] = f"Token {self.key}"
        return {"params": params, "data": sound, "headers": headers}

    def _text(self, body: Any) -> str:
        return body["results"]["channels"][0]["alternatives"][0]["transcript"]
