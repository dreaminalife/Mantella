"""Mantella works out what is at an address: which request style it answers, and which box takes the voice clip.
Only the typed address is asked; no other ports are searched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

from src.tts.voice_setups import CLIP_BASE64, CLIP_NAME, CLIP_PATH, OPENAI, XTTS

TIMEOUT = 4


@dataclass
class AddressCheck:
    style: str | None # OPENAI, XTTS or None when nothing usable answered
    message: str
    filled: dict[str, Any] = field(default_factory=dict) # setup boxes Mantella could fill in


def _get(url: str) -> requests.Response | None:
    try:
        return requests.get(url, timeout=TIMEOUT)
    except requests.RequestException:
        return None


def check_address(url: str) -> AddressCheck:
    url = (url or "").strip().rstrip("/")
    if not url.lower().startswith(("http://", "https://")):
        return AddressCheck(None, "Type an address that starts with http:// or https://, for example http://127.0.0.1:8080.")

    models = _get(f"{url}/v1/models")
    if models is not None and models.status_code == 200:
        text = models.text.lower()
        filled: dict[str, Any] = {"style": OPENAI}
        program = "an OpenAI-style program"
        if "audio.cpp" in text or "audio-cpp" in text:
            filled.update(clip_field="voice_ref", clip_mode=CLIP_PATH)
            program = "audio.cpp, which takes the clip's place on this PC"
        elif "llama" in text or "gguf" in text:
            filled.update(clip_field="speaker_ref_b64", clip_mode=CLIP_BASE64)
            program = "llama.cpp, which takes the clip itself with every line"
        try:
            ids = [m.get("id") for m in models.json().get("data", []) if isinstance(m, dict) and m.get("id")]
            if ids:
                filled["model"] = ids[0]
        except ValueError:
            pass
        return AddressCheck(OPENAI, f"{url} answers OpenAI-style requests: {program}.", filled)

    speakers = _get(f"{url}/speakers_list")
    if speakers is not None and speakers.status_code == 200:
        return AddressCheck(XTTS, f"{url} answers XTTS-style requests.", {"style": XTTS, "clip_mode": CLIP_NAME})

    health = _get(f"{url}/health")
    if health is not None and health.status_code == 200 and "healthy" in health.text.lower():
        return AddressCheck(None, f"{url} is Kyutai's own Pocket TTS program. Pick Pocket TTS as the TTS Service instead of adding a setup.")

    if models is None and speakers is None and health is None:
        return AddressCheck(None, f"Nothing answers at {url}. Is the program running?")
    return AddressCheck(None, f"{url} answers, but not in a request style Mantella knows. Fill in the boxes by hand.")
