"""ElevenLabs, online. Each voice is cloned once as an instant voice clone on the player's account, from its chosen clips (up to
2 minutes, sent as separate files), then every line uses that voice.
Model (Mantella's list, model_catalog.py) Eleven v4 Turbo: real-time, 90+ languages, $0.04 per 1,000 characters on ElevenLabs' price list (2026-11-17)."""
from __future__ import annotations

import os
from contextlib import ExitStack

import requests

from src.tts.model_catalog import ELEVENLABS_MODELS
from src.tts.online_service import BadKey, CantSpeak, CloneRefused, OnlineCloneService, PlanFull, VoiceGone, says_plan_full
from src.tts.voice_picker import VoicePick

NAME = "ElevenLabs"
KEY_FILE = "ELEVENLABS_SECRET_KEY.txt"
API_URL = "https://api.elevenlabs.io"
KEYS_PAGE = "https://elevenlabs.io/app/settings/api-keys"
WEBSITE = "https://elevenlabs.io/app/voice-lab"
SIGN_UP_PAGE = "https://elevenlabs.io/sign-up"
MODELS = ELEVENLABS_MODELS # Mantella's list (model_catalog.py): Eleven v4 Turbo
MALE_VOICE = "JBFqnCBsd6RMkjVDRZzb" # George, one of ElevenLabs' own voices every account has
FEMALE_VOICE = "EXAVITQu4vr4xnZQbJtg" # Sarah


def _detail(answer: requests.Response) -> str:
    try:
        detail = answer.json().get("detail")
    except ValueError:
        return answer.text[:300]
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("status") or detail)
    return str(detail or answer.text[:300])


def _status(answer: requests.Response) -> str:
    """ElevenLabs' own reason, such as quota_exceeded, missing_permissions or voice_not_found"""
    try:
        detail = answer.json().get("detail")
    except (ValueError, AttributeError):
        return ""
    return str(detail.get("status") or "").lower() if isinstance(detail, dict) else ""


def _refusal(answer: requests.Response) -> bool:
    text = answer.text.lower()
    return answer.status_code in (402, 403) or (answer.status_code == 400 and any(w in text for w in ("limit", "can_not_use", "subscription", "upgrade")))


class ElevenLabs(OnlineCloneService):
    NAME = NAME
    KEY_FILE = KEY_FILE
    API_URL = API_URL
    WEBSITE = WEBSITE
    MODELS = MODELS

    def built_in_voice(self, gender: int | None) -> str:
        return FEMALE_VOICE if gender == 1 else MALE_VOICE

    def clone(self, name: str, clip_paths: list[str], language: str, pick: VoicePick | None = None) -> str:
        with ExitStack() as stack:
            files = [("files", (os.path.basename(path), stack.enter_context(open(path, "rb")), "audio/wav")) for path in clip_paths]
            answer = requests.post(f"{type(self).API_URL}/v1/voices/add", headers={"xi-api-key": self._api_key},
                                   data={"name": name, "description": "Cloned by Mantella from Skyrim voice lines", "remove_background_noise": "false"},
                                   files=files, timeout=max(self.request_timeout, 60.0))
        status = _status(answer)
        if status == "quota_exceeded":
            raise CloneRefused(f"ElevenLabs won't clone another voice: your account has no characters left this month ({_detail(answer)}). "
                               "Add some, or wait for the next month. Until then the Backup Voice Service speaks.")
        if status == "missing_permissions":
            raise CloneRefused(f"Your ElevenLabs key isn't allowed to clone voices ({_detail(answer)}). Give it Voices (write) access at "
                               f"{KEYS_PAGE}, then press Save key again.")
        if answer.status_code == 401:
            raise BadKey(_detail(answer))
        if _refusal(answer):
            refused = PlanFull if says_plan_full(status, _detail(answer)) else CloneRefused
            raise refused(f"ElevenLabs won't clone another voice: {_detail(answer)}. Your plan may not include voice cloning, or its "
                               f"voices are all used: delete some at {WEBSITE} or upgrade your plan. Until then NPCs without a cloned voice "
                               "use ElevenLabs' built-in voices.")
        if answer.status_code != 200:
            raise RuntimeError(f"ElevenLabs answered {answer.status_code} when cloning: {_detail(answer)}")
        return answer.json()["voice_id"]

    @classmethod
    def delete_voice(cls, api_key: str, voice_id: str) -> None:
        answer = requests.delete(f"{cls.API_URL}/v1/voices/{voice_id}", headers={"xi-api-key": api_key}, timeout=30)
        if answer.status_code not in (200, 204, 404):
            raise RuntimeError(f"ElevenLabs answered {answer.status_code}: {_detail(answer)}")

    def speak_with(self, text: str, voice_id: str, out_path: str, pick: VoicePick | None = None) -> None:
        answer = requests.post(f"{type(self).API_URL}/v1/text-to-speech/{voice_id}", params={"output_format": "mp3_44100_128"},
                               headers={"xi-api-key": self._api_key}, json=self._request_body({"text": text, "model_id": self.model.id}),
                               timeout=self.line_wait(text))
        status = _status(answer)
        if voice_id not in (MALE_VOICE, FEMALE_VOICE) and (answer.status_code == 404 or status == "voice_not_found"):
            raise VoiceGone(_detail(answer))
        if status == "quota_exceeded":
            raise CantSpeak(f"ElevenLabs can't speak: your account has no characters left ({_detail(answer)}). Add some at {WEBSITE}, "
                            "or wait for next month.")
        if status == "missing_permissions":
            raise CantSpeak(f"Your ElevenLabs key isn't allowed to speak lines ({_detail(answer)}). Give it Text to Speech access at "
                            f"{KEYS_PAGE}, then press Save key again.")
        if answer.status_code == 402 or status == "payment_required":
            raise CantSpeak(f"ElevenLabs wants a payment before it speaks again ({_detail(answer)}). See your account at {WEBSITE}.")
        if answer.status_code == 401:
            raise CantSpeak(f"ElevenLabs doesn't accept your key ({_detail(answer)}). Make a new one at {KEYS_PAGE} and press Save key.")
        self._check_model_refusal(answer.status_code, _detail(answer))
        if answer.status_code != 200:
            raise RuntimeError(f"ElevenLabs answered {answer.status_code}: {_detail(answer)}")
        if not answer.content:
            raise RuntimeError("ElevenLabs sent back no sound")
        with open(out_path, "wb") as file:
            file.write(answer.content)

    @classmethod
    def plan_is_full(cls, api_key: str) -> bool | None:
        try:
            answer = requests.get(f"{cls.API_URL}/v1/user/subscription", headers={"xi-api-key": api_key}, timeout=15)
            plan = answer.json() if answer.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            return None
        limit, used = plan.get("voice_limit"), plan.get("voice_slots_used")
        if not isinstance(limit, int) or not isinstance(used, int):
            return None
        return used >= limit

    @classmethod
    def voice_exists(cls, api_key: str, voice_id: str) -> bool | None:
        try:
            answer = requests.get(f"{cls.API_URL}/v1/voices/{voice_id}", headers={"xi-api-key": api_key}, timeout=15)
        except requests.RequestException:
            return None
        if answer.status_code == 200:
            return True
        if answer.status_code == 404 or _status(answer) == "voice_not_found":
            return False
        return None

    @classmethod
    def check_key(cls, api_key: str, save_folder: str) -> str:
        from src.tts.online_voices import cloned_voices
        if not api_key:
            return "Paste your key first."
        try:
            answer = requests.get(f"{cls.API_URL}/v1/user/subscription", headers={"xi-api-key": api_key}, timeout=15)
        except requests.RequestException:
            return "ElevenLabs didn't answer. Check your internet connection and try again."
        if answer.status_code == 401 and "permission" in answer.text.lower():
            return (f"Your key works, but it isn't allowed to read your plan: {_detail(answer)}. Give it access to Text to Speech, "
                    f"Voices (write) and User (read) at {KEYS_PAGE}.")
        if answer.status_code == 401:
            return f"ElevenLabs doesn't accept this key: {_detail(answer)}. Make a new one at {KEYS_PAGE}."
        if answer.status_code != 200:
            return f"ElevenLabs answered {answer.status_code}: {_detail(answer)}"
        plan = answer.json()
        mine = cloned_voices(save_folder, NAME).count()
        if plan.get("can_use_instant_voice_cloning") is False:
            return ("Your key works, but your ElevenLabs plan can't clone voices, so every NPC would use ElevenLabs' built-in voices. "
                    "Voice cloning comes with the paid plans.")
        limit, used = plan.get("voice_limit"), plan.get("voice_slots_used")
        room = f" Your plan keeps {limit} voices and {used} are in use; Mantella's NPCs use {mine}." if limit is not None and used is not None else ""
        return f"Your key works: ElevenLabs can clone voices.{room}"
