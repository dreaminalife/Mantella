"""OpenAI-style voice programs and websites (llama.cpp, audio.cpp, LocalAI, ...), added as a voice setup with no code.

A request is POST {address}/v1/audio/speech with the line, a voice and a model. A setup that copies voices names the box that carries the clip.
"""
from __future__ import annotations

import base64
import io
import logging
import os
from typing import Any

import numpy as np
import requests
import soundfile as sf

from src.tts import audio_cleanup
from src.tts.clip_service import ClipVoiceService
from src.tts.registry import VoiceServiceInfo
from src.tts.voice_picker import BUILT_IN, VoicePick, VoiceSpeaker
from src.tts.voice_setups import CLIP_BASE64, CLIP_NAME, CLIP_PATH, read_key_file

DEFAULT_VOICE = "default"


def clip_as_wav_bytes(clip_path: str) -> bytes:
    """The clip as plain WAV, the format these programs read"""
    if clip_path.lower().endswith(".wav"):
        with open(clip_path, "rb") as f:
            return f.read()
    samples, rate = audio_cleanup.decode(clip_path)
    buffer = io.BytesIO()
    sf.write(buffer, np.clip(samples, -1.0, 1.0), rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def clip_fields(setup: dict[str, Any], pick: VoicePick) -> dict[str, Any]:
    """The box that carries the clip, filled the way the setup says"""
    field, mode = setup.get("clip_field", ""), setup.get("clip_mode", "")
    if not field or not mode or not pick.clip_path:
        return {}
    if mode == CLIP_PATH:
        return {field: os.path.abspath(pick.clip_path)}
    if mode == CLIP_BASE64:
        return {field: base64.b64encode(clip_as_wav_bytes(pick.clip_path)).decode("ascii")}
    if mode == CLIP_NAME:
        return {field: pick.key}
    return {}


class OpenAIStyleService(ClipVoiceService):
    def __init__(self, config, info: VoiceServiceInfo) -> None:
        super().__init__(config, info)
        self._setup: dict[str, Any] = info.setup or {}
        self._url = self._setup.get("url", "").rstrip("/")
        self._key = read_key_file(self._setup.get("key_file", ""))

    def built_in_voice(self, gender: int | None) -> str:
        voice = self._setup.get("female_voice") if gender == 1 else self._setup.get("male_voice")
        return voice or self._setup.get("male_voice") or self._setup.get("female_voice") or DEFAULT_VOICE

    def has_voice(self, name: str) -> bool:
        return name.lower() in {self._setup.get("male_voice", "").lower(), self._setup.get("female_voice", "").lower()} - {""}

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"} if self._key else {}

    @property
    def clones_voices(self) -> bool:
        """Only a setup with a box for the clip. One without speaks with its male and female voices, as its Clip field name says"""
        return bool(self._setup.get("clip_field") and self._setup.get("clip_mode"))

    can_copy_voices = clones_voices

    @property
    def why_not(self) -> str:
        return "" if self.clones_voices else f"{self._info.name} has no Clip field name, so it only speaks with its male and female voices."

    def pick_voice(self, speaker: VoiceSpeaker | None, named_voice: str | None = None) -> VoicePick:
        pick = super().pick_voice(speaker, named_voice)
        if pick.clip_path and not self.clones_voices: # a clip another service asked the game for
            return VoicePick(BUILT_IN, self.built_in_voice(speaker.gender if speaker else None), None)
        return pick

    def prepare_voice(self, pick: VoicePick) -> tuple[dict[str, Any], VoicePick]:
        """The clip is read again for each line, so a clip sent as base64 isn't held in memory for every voice"""
        voice = pick.key if not pick.clip_path else self.built_in_voice(None)
        return {"voice": voice}, pick

    def speak(self, text: str, voice: tuple[dict[str, Any], VoicePick], out_path: str) -> None:
        names, pick = voice
        body: dict[str, Any] = {"input": text, "response_format": "wav", **names, **clip_fields(self._setup, pick)}
        if self._setup.get("model"):
            body["model"] = self._setup["model"]
        body.update(self._setup.get("extra_fields") or {})
        response = requests.post(f"{self._url}/v1/audio/speech", json=body, headers=self.headers(), timeout=self.request_timeout)
        if response.status_code != 200:
            raise RuntimeError(f"{self._url} answered {response.status_code}: {response.text[:300]}")
        if not response.content:
            raise RuntimeError(f"{self._url} sent back no sound")
        with open(out_path, "wb") as f:
            f.write(response.content)

    def is_ready(self) -> bool:
        return True # Mantella doesn't start these programs; a line that fails is retried, then goes to the backup service

    def check(self) -> tuple[bool, str]:
        """For the Test button and the address check: does the address answer?"""
        try:
            response = requests.get(f"{self._url}/v1/models", headers=self.headers(), timeout=5)
            return response.status_code < 500, f"{self._url} answered {response.status_code}."
        except requests.RequestException as e:
            logging.debug(f"{self._url} did not answer: {e}")
            return False, f"Nothing answers at {self._url}. Is the program running?"
