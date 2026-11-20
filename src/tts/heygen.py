"""HeyGen, online. Each voice is cloned once as an instant voice on the player's workspace (free while HeyGen calls it a preview; free
workspaces get 20 a day) from its chosen clips joined into one sound, then every line uses it.
Models (Mantella's list, model_catalog.py): HeyGen Voice Turbo, and Turbo Free until 2026-11-30. HeyGen's price for Turbo wasn't on a
page we could read (2026-11-18), so no cost is shown for it. Takes sounds such as [laughs] and moods such as [angry] in a line, and sound
symbols for a cloned voice on a model the list marks for them (Turbo; Turbo Free and HeyGen's own voices refuse them, so those get the
word's spelling).
How voices sound (voice_style.py): Similarity when a voice is cloned (changing it clones the voice again), Expressiveness on each line."""
from __future__ import annotations

import base64
import re
from typing import Any

import requests

from src.tts.model_catalog import HEYGEN_MODELS
from src.tts.online_service import (BadKey, CantSpeak, CloneFailed, CloneRefused, OnlineCloneService, PlanFull, VoiceGone, clone_in_background,
                                    download, joined_clips)
from src.tts.voice_picker import VoicePick
from src.tts.voice_style import HEYGEN_EXPRESSIVENESS, HEYGEN_SIMILARITY
from src.tts.voice_tags import VoiceTags, rewrite_tags

NAME = "HeyGen"
KEY_FILE = "HEYGEN_SECRET_KEY.txt"
API_URL = "https://api.heygen.com"
KEYS_PAGE = "https://platform.heygen.com/api-keys"
WEBSITE = "https://platform.heygen.com/"
SIGN_UP_PAGE = "https://platform.heygen.com/"
MODELS = HEYGEN_MODELS
TURBO_FREE = "heygen-voice-1-turbo-free" # free until HeyGen ends it on 2026-11-30
FEMALE_VOICE = "83548de556df48ba8c09c42a57c51d85" # Nadine, one of HeyGen's own voices
MALE_VOICE = "male" # one of HeyGen's own English male voices, looked up once
AUDIO_TAGS_SETTING = "heygen_audio_tags"
# Mantella's word for a sound -> how HeyGen writes it
SOUND_NAMES = {"laugh": "[laughs]", "chuckle": "[chuckles]", "sigh": "[sighs]", "gasp": "[gasps]", "groan": "[groans]", "grunt": "[grunts]",
               "scoff": "[scoffs]", "sob": "[sobs]", "sniffle": "[sniffles]", "cough": "[coughs]", "clear throat": "[clears throat]",
               "yawn": "[yawns]"}
# Mantella's word for a mood -> how HeyGen writes it. A mood changes how the rest of the line is said
MOOD_NAMES = {"angry": "[angry]", "sad": "[sad]", "scared": "[scared]", "happy": "[happy]", "excited": "[excited]", "calm": "[calm]",
              "surprised": "[surprised]", "disgusted": "[disgusted]", "proud": "[proud]", "tender": "[tender]", "whisper": "[whispers]",
              "shout": "[shouts]", "nervous": "[nervous]", "cold": "[cold]", "sarcastic": "[sarcastic]"}
SOUNDS = tuple(SOUND_NAMES) # with Audio tags on: the sounds, and the moods
MOODS = tuple(MOOD_NAMES)
TAG_NAMES = {**SOUND_NAMES, **MOOD_NAMES}
# The Similarity setting's choice -> HeyGen's value
SIMILARITY = {"Medium": "medium", "High": "high", "Very high": "extra_high", "Highest": "max"}
PRON = re.compile(r'<pron ipa="[^"]*">(.*?)</pron>') # format_ipa's tag around a word


def voice_tags(config) -> VoiceTags:
    """The sounds and moods HeyGen takes with the current settings. Directions: HeyGen only takes its own list of moods, so none"""
    return VoiceTags(SOUNDS, moods=MOODS) if MODELS.switched_on(config, AUDIO_TAGS_SETTING) else VoiceTags()


def _headers(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key, "Content-Type": "application/json"}


def _error(answer: requests.Response) -> tuple[str, str, str]:
    """HeyGen's (code, message, param)"""
    try:
        error = answer.json().get("error")
    except (ValueError, AttributeError):
        return "", answer.text[:300], ""
    if not isinstance(error, dict):
        return "", answer.text[:300], ""
    return str(error.get("code") or ""), str(error.get("message") or error)[:300].rstrip(" ."), str(error.get("param") or "")


def _data(answer: requests.Response) -> dict:
    found = answer.json()
    return found.get("data", found) if isinstance(found, dict) else {}


class HeyGen(OnlineCloneService):
    NAME = NAME
    KEY_FILE = KEY_FILE
    API_URL = API_URL
    WEBSITE = WEBSITE
    MODELS = MODELS
    _male_voice: str | None = None

    def built_in_voice(self, gender: int | None) -> str:
        return FEMALE_VOICE if gender == 1 else MALE_VOICE

    def format_ipa(self, word: str, ipa: str) -> str:
        """HeyGen reads sound symbols in a pron tag around the word: <pron ipa="/ˈdoʊvɑːkiːn/">Dovahkiin</pron>"""
        return f'<pron ipa="/{ipa.strip().strip("/")}/">{word}</pron>'

    def takes_ipa(self, voice: Any) -> bool:
        """Only a cloned voice on a model that reads them (Turbo): Turbo Free and HeyGen's own voices refuse the whole line over a pron tag"""
        return self.model.sound_symbols and voice not in (MALE_VOICE, FEMALE_VOICE)

    def no_ipa_reason(self, voice: Any) -> str:
        if not self.model.sound_symbols:
            return f"HeyGen reads sound symbols only on some models, such as Turbo, not on {self.model.name}."
        return "HeyGen reads sound symbols only in a cloned voice, and this voice type isn't cloned yet. Try again in a minute."

    def clone_settings(self, pick: VoicePick) -> str:
        """The voice's Similarity: changing it clones the voice again"""
        return SIMILARITY[self.style(pick, HEYGEN_SIMILARITY)]

    def clone(self, name: str, clip_paths: list[str], language: str, pick: VoicePick | None = None) -> str:
        """HeyGen makes the voice in the background: a voice not ready in time is asked about again by the next line"""
        similarity = SIMILARITY[self.style(pick, HEYGEN_SIMILARITY)]
        return clone_in_background(NAME, self._api_key, name, clip_paths, lambda: self.__start_clone(name, clip_paths, language, similarity),
                                   self.__is_active, lambda voice_id: self.delete_voice(self._api_key, voice_id), similarity)

    def __start_clone(self, name: str, clip_paths: list[str], language: str, similarity: str) -> str:
        sample = base64.b64encode(joined_clips(clip_paths)).decode("ascii")
        body = {"mode": "instant", "name": name[:64], "audio": [{"type": "base64", "media_type": "audio/x-wav", "data": sample}],
                "similarity": similarity}
        if language:
            body["language"] = language
        answer = requests.post(f"{type(self).API_URL}/v3/models/audio/voices", headers=_headers(self._api_key), json=body, timeout=60)
        code, message, _ = _error(answer)
        if answer.status_code == 401:
            raise BadKey(message)
        if code == "resource_limit_reached":
            raise PlanFull(f"HeyGen won't clone another voice: your workspace's voices are all used ({message}). Delete some at {WEBSITE}. "
                           "Until then NPCs without a cloned voice use HeyGen's built-in voices.")
        if answer.status_code in (402, 403, 429):
            raise CloneRefused(f"HeyGen won't clone another voice right now: {message}. Free workspaces can clone 20 voices a day.")
        if answer.status_code not in (200, 201, 202):
            raise RuntimeError(f"HeyGen answered {answer.status_code} when cloning: {message}")
        voice_id = str(_data(answer).get("voice_id") or "")
        if not voice_id:
            raise RuntimeError("HeyGen didn't say which voice it made")
        return voice_id

    def __is_active(self, voice_id: str) -> bool:
        answer = requests.get(f"{type(self).API_URL}/v3/models/audio/voices/{voice_id}", headers=_headers(self._api_key), timeout=15)
        if answer.status_code != 200:
            return False
        voice = _data(answer)
        if voice.get("status") == "FAILED":
            raise CloneFailed(f"HeyGen turned the clips down ({voice.get('failure_reason') or 'no reason given'})")
        return voice.get("status") == "ACTIVE"

    @classmethod
    def delete_voice(cls, api_key: str, voice_id: str) -> None:
        answer = requests.delete(f"{cls.API_URL}/v3/models/audio/voices/{voice_id}", headers=_headers(api_key), timeout=30)
        if answer.status_code not in (200, 204, 404):
            raise RuntimeError(f"HeyGen answered {answer.status_code}: {_error(answer)[1]}")

    def __male(self) -> str:
        """One of HeyGen's own English male voices, looked up the first time a man speaks with a built-in voice"""
        if HeyGen._male_voice is None:
            answer = requests.get(f"{type(self).API_URL}/v3/models/audio/voices", headers=_headers(self._api_key),
                                  params={"type": "public", "language": "en", "gender": "male", "limit": 1}, timeout=15)
            voices = _data(answer) if answer.status_code == 200 else []
            voices = voices if isinstance(voices, list) else []
            if not voices or not voices[0].get("voice_id"):
                raise RuntimeError(f"HeyGen didn't list a male voice of its own ({answer.status_code})")
            HeyGen._male_voice = str(voices[0]["voice_id"])
        return HeyGen._male_voice

    def speak_with(self, text: str, voice_id: str, out_path: str, pick: VoicePick | None = None) -> None:
        model = self.model
        wait = self.line_wait(text)
        voice = self.__male() if voice_id == MALE_VOICE else voice_id
        if not self.takes_ipa(voice_id):
            text = PRON.sub(r"\1", text) # the word as written, so the line is still spoken
        # HeyGen leaves Expressiveness out by itself on a line with a sound or mood
        body = {"model": model.id, "voice_id": voice, "text": rewrite_tags(text, TAG_NAMES)[0]}
        if model.takes(HEYGEN_EXPRESSIVENESS.name):
            body["expressiveness_boost"] = float(self.style(pick, HEYGEN_EXPRESSIVENESS))
        if self._language:
            body["language"] = self._language
        answer = requests.post(f"{type(self).API_URL}/v3/models/audio/tts", headers=_headers(self._api_key), json=self._request_body(body),
                               timeout=wait)
        code, message, param = _error(answer)
        if (model.id == TURBO_FREE and answer.status_code in (400, 404, 410) and param != "voice_id"
                and (param == "model" or "model" in message.lower())):
            # Checked before a missing voice: HeyGen ends Turbo Free on Nov 30, 2026, and a refused model is not a deleted voice
            raise CantSpeak(f"HeyGen no longer takes Turbo Free ({message}). Pick Turbo as the Model under HeyGen on the Text-to-Speech page.")
        if voice_id not in (MALE_VOICE, FEMALE_VOICE) and (answer.status_code == 404 or code == "voice_not_found"
                                                            or (answer.status_code == 400 and param == "voice_id")):
            raise VoiceGone(message)
        if answer.status_code == 401:
            raise CantSpeak(f"HeyGen doesn't accept your key ({message}). Make a new one at {KEYS_PAGE} and press Save key.")
        if answer.status_code == 402:
            raise CantSpeak(f"HeyGen can't speak: {message}. Add credit at {WEBSITE}, or pick Turbo Free under HeyGen while it lasts.")
        self._check_model_refusal(answer.status_code, message)
        if answer.status_code != 200:
            raise RuntimeError(f"HeyGen answered {answer.status_code}: {message}")
        url = _data(answer).get("audio_url")
        if not url:
            raise RuntimeError("HeyGen sent back no sound")
        download(str(url), out_path, wait, NAME)

    @classmethod
    def voice_exists(cls, api_key: str, voice_id: str) -> bool | None:
        try:
            answer = requests.get(f"{cls.API_URL}/v3/models/audio/voices/{voice_id}", headers=_headers(api_key), timeout=15)
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
            answer = requests.get(f"{cls.API_URL}/v3/models/audio/voices", headers=_headers(api_key), params={"type": "private", "limit": 100}, timeout=15)
        except requests.RequestException:
            return "HeyGen didn't answer. Check your internet connection and try again."
        if answer.status_code == 401:
            return f"HeyGen doesn't accept this key: {_error(answer)[1]}. Make a new one at {KEYS_PAGE}."
        if answer.status_code != 200:
            return f"HeyGen answered {answer.status_code}: {_error(answer)[1]}"
        found = answer.json()
        voices = _data(answer)
        count = len(voices) if isinstance(voices, list) else 0
        more = "+" if isinstance(found, dict) and found.get("has_more") else ""
        mine = cloned_voices(save_folder, NAME).count()
        return f"Your key works: HeyGen can clone voices. Your workspace has {count}{more} cloned voices; Mantella's NPCs use {mine}."
