"""Inworld, online. Each voice is cloned once on the player's workspace (free on every plan) from its chosen clips, joined into one
sound of up to 30 seconds (Inworld cuts anything longer), then every line uses it.
Models on Inworld's on-demand price list (2026-11-17): Realtime TTS-2 at $25 per million characters, Realtime TTS-2 Flash (faster) at $15.
Both take sound symbols in a line."""
from __future__ import annotations

import base64

import requests

from src.tts.model_catalog import INWORLD_MODELS
from src.tts.online_service import BadKey, CantSpeak, CloneRefused, OnlineCloneService, PlanFull, VoiceGone, joined_clips, says_plan_full
from src.tts.voice_picker import VoicePick
from src.tts.voice_style import INWORLD_DELIVERY
from src.tts.voice_tags import VoiceTags

NAME = "Inworld"
KEY_FILE = "INWORLD_SECRET_KEY.txt"
API_URL = "https://api.inworld.ai"
KEYS_PAGE = "https://platform.inworld.ai/"
WEBSITE = "https://platform.inworld.ai/"
SIGN_UP_PAGE = "https://platform.inworld.ai/signup"
MODELS = INWORLD_MODELS # Mantella's list (model_catalog.py): TTS-2, and TTS-2 Flash, which has no Delivery and no directions
MALE_VOICE = "Alex" # Inworld's own voices
FEMALE_VOICE = "Ashley"
LOWEST_RATE = 22050 # Inworld wants at least 22 kHz
AUDIO_TAGS_SETTING = "inworld_audio_tags"
DIRECTIONS_SETTING = "inworld_directions" # a model that skips it (Flash) ignores directions, but makes sounds
# The Delivery setting's choice (voice_style.py; not on a model that skips it) -> Inworld's deliveryMode
DELIVERY = {"Stable": "STABLE", "Balanced": "BALANCED", "Creative": "CREATIVE"}
# The sounds offered to the AI: the ones of Inworld's list that fit Skyrim's people
SOUNDS = ("laugh", "chuckle", "sigh", "gasp", "groan", "grunt", "scoff", "sob", "sniffle", "cough", "clear throat", "breathe", "yawn", "growl")


def voice_tags(config) -> VoiceTags:
    """The sounds and directions Inworld takes with the current settings"""
    return VoiceTags(SOUNDS if MODELS.switched_on(config, AUDIO_TAGS_SETTING) else (), MODELS.switched_on(config, DIRECTIONS_SETTING))


def _headers(api_key: str) -> dict[str, str]:
    # The key from Inworld's portal is already the Base64 credentials, used as it is
    return {"Authorization": f"Basic {api_key}", "Content-Type": "application/json"}


def _message(answer: requests.Response) -> str:
    try:
        data = answer.json()
    except ValueError:
        return answer.text[:300]
    error = data.get("error") if isinstance(data.get("error"), dict) else data
    return str(error.get("message") or data)[:300]


class Inworld(OnlineCloneService):
    NAME = NAME
    KEY_FILE = KEY_FILE
    API_URL = API_URL
    WEBSITE = WEBSITE
    MODELS = MODELS

    def built_in_voice(self, gender: int | None) -> str:
        return FEMALE_VOICE if gender == 1 else MALE_VOICE

    def format_ipa(self, word: str, ipa: str) -> str:
        """Inworld reads sound symbols written between slashes: /ˈdoʊvɑːkiːn/"""
        return f"/{ipa.strip().strip('/')}/"

    def clone(self, name: str, clip_paths: list[str], language: str, pick: VoicePick | None = None) -> str:
        sample = base64.b64encode(joined_clips(clip_paths, LOWEST_RATE)).decode("ascii")
        body = {"displayName": name, "description": "Cloned by Mantella from Skyrim voice lines", "voiceSamples": [{"audioData": sample}]}
        if language:
            body["languageCode"] = language # a bare code such as "en" picks its usual accent
        answer = requests.post(f"{type(self).API_URL}/voices/v1/voices:clone", headers=_headers(self._api_key), json=body, timeout=60)
        if answer.status_code == 401:
            raise BadKey(_message(answer))
        if answer.status_code in (403, 429) or (answer.status_code == 400 and "limit" in answer.text.lower()):
            refused = PlanFull if says_plan_full(_message(answer)) else CloneRefused
            raise refused(f"Inworld won't clone another voice: {_message(answer)}. Your key may not be allowed to make voices "
                               f"(make one with write access), or your workspace's voices are all used: delete some at {WEBSITE}. "
                               "Until then NPCs without a cloned voice use Inworld's built-in voices.")
        if answer.status_code != 200:
            raise RuntimeError(f"Inworld answered {answer.status_code} when cloning: {_message(answer)}")
        return answer.json()["voice"]["voiceId"]

    @classmethod
    def delete_voice(cls, api_key: str, voice_id: str) -> None:
        answer = requests.delete(f"{cls.API_URL}/voices/v1/voices/{voice_id}", headers=_headers(api_key), timeout=30)
        if answer.status_code not in (200, 204, 404):
            raise RuntimeError(f"Inworld answered {answer.status_code}: {_message(answer)}")

    def speak_with(self, text: str, voice_id: str, out_path: str, pick: VoicePick | None = None) -> None:
        model = self.model
        body = {"text": text, "voiceId": voice_id, "modelId": model.id, "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 48000}}
        if model.takes(INWORLD_DELIVERY.name): # TTS-2 Flash has no Delivery
            body["deliveryMode"] = DELIVERY[self.style(pick, INWORLD_DELIVERY)]
        answer = requests.post(f"{type(self).API_URL}/tts/v1/voice", headers=_headers(self._api_key), json=self._request_body(body),
                               timeout=self.line_wait(text))
        if voice_id not in (MALE_VOICE, FEMALE_VOICE) and (answer.status_code == 404 or "not found" in answer.text.lower()):
            raise VoiceGone(_message(answer))
        text_lower = answer.text.lower()
        if answer.status_code == 401:
            raise CantSpeak(f"Inworld doesn't accept your key ({_message(answer)}). Copy the Basic key from {KEYS_PAGE} again and press Save key.")
        if answer.status_code == 403:
            raise CantSpeak(f"Your Inworld key isn't allowed to speak lines ({_message(answer)}). Make one with access to TTS at {KEYS_PAGE}.")
        if answer.status_code == 402 or (answer.status_code == 429 and any(w in text_lower for w in ("quota", "credit", "balance", "billing"))):
            raise CantSpeak(f"Inworld can't speak: your account has run out of credit ({_message(answer)}). Add some at {WEBSITE}.")
        self._check_model_refusal(answer.status_code, _message(answer))
        if answer.status_code != 200:
            raise RuntimeError(f"Inworld answered {answer.status_code}: {_message(answer)}")
        sound = base64.b64decode(answer.json().get("audioContent") or "")
        if not sound:
            raise RuntimeError("Inworld sent back no sound")
        with open(out_path, "wb") as file:
            file.write(sound) # LINEAR16 comes with a WAV header

    @classmethod
    def voice_exists(cls, api_key: str, voice_id: str) -> bool | None:
        try:
            answer = requests.get(f"{cls.API_URL}/voices/v1/voices/{voice_id}", headers=_headers(api_key), timeout=15)
        except requests.RequestException:
            return None
        if answer.status_code == 200:
            return True
        return False if answer.status_code == 404 else None

    @classmethod
    def check_key(cls, api_key: str, save_folder: str) -> str:
        from src.tts.online_voices import cloned_voices
        if not api_key:
            return "Paste your key first."
        try:
            answer = requests.get(f"{cls.API_URL}/voices/v1/voices", headers=_headers(api_key), params={"pageSize": 2000}, timeout=15)
        except requests.RequestException:
            return "Inworld didn't answer. Check your internet connection and try again."
        if answer.status_code == 401:
            return f"Inworld doesn't accept this key: {_message(answer)}. Copy the Basic key from {KEYS_PAGE} again."
        if answer.status_code != 200:
            return f"Inworld answered {answer.status_code}: {_message(answer)}"
        voices = answer.json().get("voices") or []
        cloned = sum(1 for v in voices if v.get("source") == "IVC")
        mine = cloned_voices(save_folder, NAME).count()
        return f"Your key works: Inworld can clone voices. Your workspace has {cloned} cloned voices; Mantella's NPCs use {mine}."
