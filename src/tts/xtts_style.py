"""XTTS-style voice programs (an XTTS server, CHIM's Pocket TTS add-on server, AudioUtilTTS, some Chatterbox servers), added as a voice setup with no code.

A request is POST {address}/tts_to_audio/ with the line, the voice clip and the language: the same request Mantella's own XTTS option sends.
The built-in XTTS option is not touched: this is separate code.
"""
from __future__ import annotations

import os
import threading
from typing import Any

import requests

from src.tts.clip_service import ClipVoiceService
from src.tts.registry import VoiceServiceInfo
from src.tts.voice_picker import VoicePick
from src.tts.voice_setups import CLIP_PATH
from src.voice_clips.clip_store import voice_key
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VOICES)


class XTTSStyleService(ClipVoiceService):
    def __init__(self, config, info: VoiceServiceInfo) -> None:
        super().__init__(config, info)
        self._setup: dict[str, Any] = info.setup or {}
        self._url = self._setup.get("url", "").rstrip("/")
        self._program_voices: set[str] | None = None
        self._voices_lock = threading.Lock()

    def language(self) -> str:
        return "zh-cn" if self._language == "zh" else self._language

    def built_in_voice(self, gender: int | None) -> str:
        voice = self._setup.get("female_voice") if gender == 1 else self._setup.get("male_voice")
        return voice or ("femalenord" if gender == 1 else "malenord")

    def program_voices(self) -> set[str]:
        """The voices the program has itself, from /speakers_list"""
        with self._voices_lock:
            if self._program_voices is None:
                try:
                    data = requests.get(f"{self._url}/speakers_list", timeout=5).json()
                    if isinstance(data, dict): # Mantella's XTTS server lists voices per language
                        data = data.get(self.language(), data.get("en", {}))
                        data = data.get("speakers", []) if isinstance(data, dict) else data
                    self._program_voices = {voice_key(v) for v in data if isinstance(v, str)}
                except (requests.RequestException, ValueError) as e:
                    log.warning(f"{self._url}: could not list its voices: {e}")
                    return set()
            return self._program_voices

    def has_voice(self, name: str) -> bool:
        return voice_key(name) in self.program_voices()

    def prepare_voice(self, pick: VoicePick) -> str:
        if pick.clip_path and self._setup.get("clip_mode") == CLIP_PATH:
            return os.path.abspath(pick.clip_path)
        return pick.key

    def speak(self, text: str, voice: str, out_path: str) -> None:
        body = {"text": text, "speaker_wav": voice, "language": self.language(), **(self._setup.get("extra_fields") or {})}
        response = requests.post(f"{self._url}/tts_to_audio/", json=body, timeout=self.request_timeout)
        if response.status_code != 200:
            raise RuntimeError(f"{self._url} answered {response.status_code}: {response.text[:300]}")
        with open(out_path, "wb") as f:
            f.write(response.content)

    def check(self) -> tuple[bool, str]:
        try:
            response = requests.get(f"{self._url}/speakers_list", timeout=5)
            return response.status_code < 500, f"{self._url} answered {response.status_code}."
        except requests.RequestException:
            return False, f"Nothing answers at {self._url}. Is the program running?"
