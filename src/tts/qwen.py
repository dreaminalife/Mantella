"""Qwen, online, on Alibaba Cloud Model Studio (the Singapore region). Each voice is cloned once on the player's account (free) from its
chosen clips joined into one sound of up to 30 seconds, then every line uses it. A voice works only with the model it was cloned for,
so another model clones each voice once more (clone_settings). Qwen's own voices go with the model too: Mantella's list gives them.
Model Qwen Audio 3.1 TTS Plus: $0.30 per 10,000 characters in Singapore (2026-11-18). Takes sounds such as [laughing], and a direction
as the request's instruction."""
from __future__ import annotations

import base64
import logging

import requests

from src.tts.model_catalog import FEMALE, MALE, QWEN_MODELS
from src.tts.online_service import (BadKey, CantSpeak, CloneFailed, CloneRefused, ModelCantClone, OnlineCloneService, PlanFull, VoiceGone,
                                    clone_in_background, download, joined_clips)
from src.tts.voice_notices import add_notice
from src.tts.voice_picker import VoicePick
from src.tts.voice_tags import VoiceTags, rewrite_tags

NAME = "Qwen"
KEY_FILE = "QWEN_SECRET_KEY.txt"
# Each Model Studio workspace has its own address. Keys only work in the region they were made in
API_URL = "https://{workspace}.ap-southeast-1.maas.aliyuncs.com/api/v1"
KEYS_PAGE = "https://modelstudio.console.alibabacloud.com/ap-southeast-1/model/settings/api-key"
WEBSITE = "https://modelstudio.console.alibabacloud.com/ap-southeast-1/"
SIGN_UP_PAGE = "https://www.alibabacloud.com/en/product/modelstudio"
WORKSPACE_SETTING = "qwen_workspace_id"
MODELS = QWEN_MODELS
MALE_VOICE = "xunanchuan_v3.1" # Qwen's own voices for Qwen Audio 3.1, when Mantella's list gives none for the model
FEMALE_VOICE = "longanhuan_v3.1"
LOWEST_RATE = 24000 # Qwen wants clips of at least 24 kHz
VOICE_PREFIX = "mantella" # up to 10 letters or digits, part of every voice's name
AUDIO_TAGS_SETTING = "qwen_audio_tags"
DIRECTIONS_SETTING = "qwen_directions"
DIRECTIONS_LANGUAGE = "en" # Qwen wants the instruction in the line's language, and the AI writes directions in English
# Mantella's word for a sound -> how Qwen writes it
SOUND_NAMES = {"laugh": "[laughing]", "sigh": "[sighing]", "gasp": "[gasp]", "cough": "[cough]", "clear throat": "[clears throat]"}
SOUNDS = tuple(SOUND_NAMES)

_workspace_id = ""


def use_workspace(workspace_id: str | None) -> None:
    """The Workspace ID set on the Text-to-Speech page, for the requests made without the settings (deleting voices, Check my key)"""
    global _workspace_id
    _workspace_id = (workspace_id or "").strip()


def has_workspace() -> bool:
    return bool(_workspace_id)


def voice_tags(config) -> VoiceTags:
    """The sounds and directions Qwen takes with the current settings. Directions only when Mantella speaks English"""
    directions = MODELS.switched_on(config, DIRECTIONS_SETTING) and getattr(config, "language", "") == DIRECTIONS_LANGUAGE
    return VoiceTags(SOUNDS if MODELS.switched_on(config, AUDIO_TAGS_SETTING) else (), directions)


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _error(answer: requests.Response) -> tuple[str, str]:
    """Model Studio's (code, message)"""
    try:
        data = answer.json()
    except ValueError:
        return "", answer.text[:300]
    return str(data.get("code") or ""), str(data.get("message") or data)[:300].rstrip(" .")


class Qwen(OnlineCloneService):
    NAME = NAME
    KEY_FILE = KEY_FILE
    API_URL = API_URL
    WEBSITE = WEBSITE
    MODELS = MODELS

    def __init__(self, config, info) -> None:
        use_workspace(getattr(config, WORKSPACE_SETTING, ""))
        super().__init__(config, info)

    @classmethod
    def _url(cls, path: str) -> str:
        return cls.API_URL.format(workspace=_workspace_id) + path

    @classmethod
    def _voice_request(cls, api_key: str, action: str, **fields) -> requests.Response:
        body = {"model": "voice-enrollment", "input": {"action": action, **fields}}
        return requests.post(cls._url("/services/audio/tts/customization"), headers=_headers(api_key), json=body, timeout=60)

    # ---- the Workspace ID counts like the key ----

    @property
    def can_copy_voices(self) -> bool:
        return bool(_workspace_id) and super().can_copy_voices

    @property
    def why_not(self) -> str:
        if self._api_key and not _workspace_id:
            return "Qwen has no Workspace ID. Paste it under Qwen on the Text-to-Speech page."
        return super().why_not

    def serve_in_game(self) -> None:
        """Without a Workspace ID every line goes to the backup service: said once, like a missing key, so Qwen never goes quiet unnoticed"""
        super().serve_in_game()
        if self._api_key and not _workspace_id:
            logging.warning(self.why_not)
            add_notice("Qwen has no Workspace ID, so the backup voice service speaks. See Mantella's Text-to-Speech page.")

    def is_ready(self) -> bool:
        """Asked before every line: a Workspace ID changed on the page is used from the next line"""
        use_workspace(getattr(self._config, WORKSPACE_SETTING, ""))
        return bool(_workspace_id) and super().is_ready()

    # ---- what Qwen answers ----

    def built_in_voice(self, gender: int | None) -> str:
        voices = self.model.voices
        if gender == 1:
            return voices.get(FEMALE) or FEMALE_VOICE
        return voices.get(MALE) or MALE_VOICE

    def _built_in_voices(self) -> tuple[str, ...]:
        return (MALE_VOICE, FEMALE_VOICE, *self.model.voices.values())

    def clone_settings(self, pick: VoicePick) -> str:
        """The model: a Qwen voice only works with the model it was cloned for, so another model clones it again"""
        return self.model.id

    def clone(self, name: str, clip_paths: list[str], language: str, pick: VoicePick | None = None) -> str:
        """Qwen makes the voice in the background: a voice not ready in time is asked about again by the next line"""
        model_id = self.model.id
        return clone_in_background(NAME, self._api_key, name, clip_paths, lambda: self.__start_clone(name, clip_paths, language, model_id),
                                   self.__is_ready, lambda voice_id: self.delete_voice(self._api_key, voice_id), model_id)

    def __start_clone(self, name: str, clip_paths: list[str], language: str, model_id: str) -> str:
        sample = base64.b64encode(joined_clips(clip_paths, LOWEST_RATE)).decode("ascii")
        fields = {"target_model": model_id, "prefix": VOICE_PREFIX, "audio": f"data:audio/wav;base64,{sample}", "max_prompt_audio_length": 30}
        if language:
            fields["language_hints"] = [language]
        answer = self._voice_request(self._api_key, "create_voice", **fields)
        code, message = _error(answer)
        if answer.status_code == 401 or code in ("InvalidApiKey", "WorkSpaceNotFound"):
            raise BadKey(message)
        if code == "Throttling.AllocationQuota":
            raise PlanFull(f"Qwen won't clone another voice: your account's voices are all used ({message}). Delete some, or wait. "
                           "Until then NPCs without a cloned voice use Qwen's built-in voices.")
        if answer.status_code in (403, 429):
            raise CloneRefused(f"Qwen won't clone another voice right now: {message}.")
        if answer.status_code == 400 and ("target_model" in message.lower() or "model" in code.lower()):
            raise ModelCantClone(f"{self.model.name} can't speak with cloned voices ({message}). Pick another Model on the Text-to-Speech page. "
                                 "Until then NPCs use Qwen's built-in voices.")
        if answer.status_code != 200:
            raise RuntimeError(f"Qwen answered {answer.status_code} when cloning: {message}")
        voice_id = str((answer.json().get("output") or {}).get("voice_id") or "")
        if not voice_id:
            raise RuntimeError("Qwen didn't say which voice it made")
        return voice_id

    def __is_ready(self, voice_id: str) -> bool:
        answer = self._voice_request(self._api_key, "query_voice", voice_id=voice_id)
        if answer.status_code != 200:
            return False
        status = (answer.json().get("output") or {}).get("status")
        if status == "UNDEPLOYED":
            raise CloneFailed("Qwen turned the clips down at its review")
        return status == "OK"

    @classmethod
    def delete_voice(cls, api_key: str, voice_id: str) -> None:
        answer = cls._voice_request(api_key, "delete_voice", voice_id=voice_id)
        code, message = _error(answer)
        if answer.status_code != 200 and code != "BadRequest.VoiceNotFound":
            raise RuntimeError(f"Qwen answered {answer.status_code}: {message}")

    def speak_with(self, text: str, voice_id: str, out_path: str, pick: VoicePick | None = None) -> None:
        wait = self.line_wait(text)
        text, direction = rewrite_tags(text, SOUND_NAMES)
        fields = {"text": text, "voice": voice_id, "format": "wav", "sample_rate": 44100}
        if self._language:
            fields["language_hints"] = [self._language]
        if direction and voice_tags(self._config).directions:
            fields["instruction"] = direction # Qwen takes one for the whole request
        answer = requests.post(self._url("/services/audio/tts/SpeechSynthesizer"), headers=_headers(self._api_key),
                               json=self._request_body({"model": self.model.id, "input": fields}), timeout=wait)
        code, message = _error(answer)
        if voice_id not in self._built_in_voices() and code == "BadRequest.VoiceNotFound":
            raise VoiceGone(message)
        if answer.status_code == 401 or code in ("InvalidApiKey", "WorkSpaceNotFound"):
            raise CantSpeak(f"Qwen doesn't accept your key or Workspace ID ({message}). Both must come from the Singapore console: "
                            f"{KEYS_PAGE}. Then press Save key again.")
        if code in ("Arrearage", "AllocationQuota.FreeTierOnly"):
            raise CantSpeak(f"Qwen can't speak: your account has no free characters or credit left ({message}). Add some at {WEBSITE}.")
        self._check_model_refusal(answer.status_code, message)
        if answer.status_code != 200:
            raise RuntimeError(f"Qwen answered {answer.status_code}: {message}")
        url = ((answer.json().get("output") or {}).get("audio") or {}).get("url")
        if not url:
            raise RuntimeError("Qwen sent back no sound")
        download(str(url), out_path, wait, NAME)

    @classmethod
    def voice_exists(cls, api_key: str, voice_id: str) -> bool | None:
        try:
            answer = cls._voice_request(api_key, "query_voice", voice_id=voice_id)
        except requests.RequestException:
            return None
        if answer.status_code == 200:
            return True
        return False if _error(answer)[0] == "BadRequest.VoiceNotFound" else None

    @classmethod
    def check_key(cls, api_key: str, save_folder: str) -> str:
        from src.tts.online_voices import cloned_voices
        if not api_key:
            return "Paste your key first."
        if not _workspace_id:
            return "Paste your Workspace ID under Qwen first: it is on the Workspace page of the Singapore console."
        try:
            answer = cls._voice_request(api_key, "list_voice", page_size=1, page_index=0)
        except requests.RequestException:
            return "Qwen didn't answer. Check your internet connection and the Workspace ID, and try again."
        code, message = _error(answer)
        if answer.status_code == 401 or code in ("InvalidApiKey", "WorkSpaceNotFound"):
            return (f"Qwen doesn't accept this key with this Workspace ID: {message}. Both must come from the same workspace in the "
                    f"Singapore console ({KEYS_PAGE}).")
        if answer.status_code != 200:
            return f"Qwen answered {answer.status_code}: {message}"
        mine = cloned_voices(save_folder, NAME).count()
        return f"Your key works: Qwen can clone voices. Mantella's NPCs use {mine} of the 1,000 your account can keep."
