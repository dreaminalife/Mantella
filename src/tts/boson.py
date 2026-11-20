"""Boson AI (Higgs TTS 3), online. Each voice is cloned once on the player's account from its chosen clips joined into one sound of up
to 30 seconds, together with the words spoken in them: Mantella hears those once with the Speech-to-Text choice (transcripts.py).
Up to 200 cloned voices per account. $15 per million characters, with a $10 trial credit to claim once (2026-11-18).
Takes sounds such as <|sfx:laughter|> and moods such as <|emotion:anger|>; any tag it doesn't know makes it refuse the line.
How voices sound (voice_style.py): Expressiveness, a tag at the start of each line."""
from __future__ import annotations

import base64

import requests

from src.tts.model_catalog import BOSON_MODELS
from src.tts.online_service import BadKey, CantSpeak, CloneFailed, CloneRefused, OnlineCloneService, PlanFull, VoiceGone, joined_clips
from src.tts.voice_picker import VoicePick
from src.tts.voice_style import BOSON_EXPRESSIVENESS
from src.tts.voice_tags import VoiceTags, rewrite_tags

NAME = "Boson AI"
KEY_FILE = "BOSON_SECRET_KEY.txt"
API_URL = "https://api.boson.ai/v1"
KEYS_PAGE = "https://www.boson.ai/workspace/api-key"
WEBSITE = "https://www.boson.ai/workspace"
SIGN_UP_PAGE = "https://www.boson.ai/workspace"
MODELS = BOSON_MODELS # Mantella's list (model_catalog.py): Higgs TTS 3
VOICE_LIMIT = 200
MALE_VOICE = "jake" # Boson's own voices
FEMALE_VOICE = "chloe"
AUDIO_TAGS_SETTING = "boson_audio_tags"
# Mantella's word for a sound -> how Boson writes it
SOUND_NAMES = {"laugh": "<|sfx:laughter|>", "sigh": "<|sfx:sigh|>", "cough": "<|sfx:cough|>", "sob": "<|sfx:crying|>", "sniffle": "<|sfx:sniff|>"}
# Mantella's word for a mood -> how Boson writes it. A mood changes how the rest of the line is said
MOOD_NAMES = {"angry": "<|emotion:anger|>", "sad": "<|emotion:sadness|>", "scared": "<|emotion:fear|>", "happy": "<|emotion:elation|>",
              "excited": "<|emotion:enthusiasm|>", "calm": "<|emotion:contentment|>", "surprised": "<|emotion:surprise|>",
              "disgusted": "<|emotion:disgust|>", "proud": "<|emotion:pride|>", "tender": "<|emotion:affection|>", "whisper": "<|style:whispering|>",
              "shout": "<|style:shouting|>", "bitter": "<|emotion:bitterness|>", "ashamed": "<|emotion:shame|>"}
SOUNDS = tuple(SOUND_NAMES) # with Audio tags on: the sounds, and the moods
MOODS = tuple(MOOD_NAMES)
# The Expressiveness setting's choice -> the tag at the start of each line
EXPRESSIVENESS = {"Flat": "<|prosody:expressive_low|>", "Normal": "", "Lively": "<|prosody:expressive_high|>"}


def boson_text(text: str, expressiveness: str) -> str:
    """A line as split() gives it, the way Boson takes it: sounds and moods in Boson's form, and the Expressiveness tag at the start.
    A mood starts a new group of tags that replaces the one before, so the Expressiveness tag follows each mood too"""
    tag = EXPRESSIVENESS.get(expressiveness, "")
    names = {**SOUND_NAMES, **{word: mood + tag for word, mood in MOOD_NAMES.items()}}
    text = rewrite_tags(text, names)[0]
    if tag and not text.startswith(tuple(MOOD_NAMES.values())):
        text = tag + text
    return text


def voice_tags(config) -> VoiceTags:
    """The sounds and moods Boson takes with the current settings. Directions: Boson only takes its own list of moods, so none"""
    return VoiceTags(SOUNDS, moods=MOODS) if MODELS.switched_on(config, AUDIO_TAGS_SETTING) else VoiceTags()


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _error(answer: requests.Response) -> tuple[str, str]:
    """Boson's (code, message)"""
    try:
        error = answer.json().get("error")
    except (ValueError, AttributeError):
        return "", answer.text[:300]
    if not isinstance(error, dict):
        return "", answer.text[:300]
    return str(error.get("code") or error.get("type") or ""), str(error.get("message") or error)[:300].rstrip(" .")


def _no_credit(code: str, message: str) -> str:
    if code == "no_billing_account":
        return f"Boson AI has no credit to use yet ({message}). Claim your $10 trial credit at {KEYS_PAGE}."
    if code == "monthly_cap_reached":
        return f"Boson AI stopped at the monthly limit you set ({message}). Raise it at {WEBSITE}."
    return f"Boson AI has no credit left ({message}). Add some at {WEBSITE}."


class BosonAI(OnlineCloneService):
    NAME = NAME
    KEY_FILE = KEY_FILE
    API_URL = API_URL
    WEBSITE = WEBSITE
    MODELS = MODELS

    def built_in_voice(self, gender: int | None) -> str:
        return FEMALE_VOICE if gender == 1 else MALE_VOICE

    def clone(self, name: str, clip_paths: list[str], language: str, pick: VoicePick | None = None) -> str:
        from src.voice_clips.transcripts import words_for
        try:
            heard = words_for(clip_paths, self._config)
        except Exception as e:
            raise CloneRefused(f"Boson AI needs the words of each clip, and Speech-to-Text couldn't hear them: {e}. Check the "
                               "Speech-to-Text tab's Test button.") from e
        # A clip with no words heard (a shout, a grunt) is left out: Boson wants the words of everything it hears
        clip_paths = [path for path, words in zip(clip_paths, heard) if words]
        if not clip_paths:
            raise CloneFailed("Speech-to-Text heard no words in them")
        words = " ".join(words for words in heard if words)
        sample = base64.b64encode(joined_clips(clip_paths)).decode("ascii")
        body = {"ref_audio": sample, "ref_text": words, "title": name}
        answer = requests.post(f"{type(self).API_URL}/audio/voices", headers=_headers(self._api_key), json=body, timeout=60)
        code, message = _error(answer)
        if answer.status_code == 401:
            raise BadKey(message)
        if answer.status_code == 403 and code == "quota_exceeded":
            raise PlanFull(f"Boson AI won't clone another voice: your account keeps {VOICE_LIMIT} and they are all used ({message}). "
                           f"Delete some at {WEBSITE}. Until then NPCs without a cloned voice use Boson's built-in voices.")
        if answer.status_code == 429 and code != "rate_limit_error":
            raise CloneRefused(_no_credit(code, message))
        if answer.status_code != 200:
            raise RuntimeError(f"Boson AI answered {answer.status_code} when cloning: {message}")
        voice_id = str(answer.json().get("voice_id") or "")
        if not voice_id:
            raise RuntimeError("Boson AI didn't say which voice it made")
        return voice_id

    @classmethod
    def delete_voice(cls, api_key: str, voice_id: str) -> None:
        answer = requests.delete(f"{cls.API_URL}/audio/voices/{voice_id}", headers=_headers(api_key), timeout=30)
        if answer.status_code not in (200, 204, 404):
            raise RuntimeError(f"Boson AI answered {answer.status_code}: {_error(answer)[1]}")

    def speak_with(self, text: str, voice_id: str, out_path: str, pick: VoicePick | None = None) -> None:
        model = self.model
        expressiveness = self.style(pick, BOSON_EXPRESSIVENESS) if model.takes(BOSON_EXPRESSIVENESS.name) else "Normal"
        body = {"model": model.id, "input": boson_text(text, expressiveness), "voice": voice_id, "response_format": "wav"}
        answer = requests.post(f"{type(self).API_URL}/audio/speech", headers=_headers(self._api_key), json=self._request_body(body),
                               timeout=self.line_wait(text))
        if answer.status_code != 200:
            code, message = _error(answer)
            if voice_id not in (MALE_VOICE, FEMALE_VOICE) and (answer.status_code == 404 or code == "voice_not_found"):
                raise VoiceGone(message)
            if answer.status_code == 401:
                raise CantSpeak(f"Boson AI doesn't accept your key ({message}). Make a new one at {KEYS_PAGE} and press Save key.")
            if answer.status_code == 429 and code != "rate_limit_error":
                raise CantSpeak(_no_credit(code, message))
            self._check_model_refusal(answer.status_code, message)
            raise RuntimeError(f"Boson AI answered {answer.status_code}: {message}")
        if not answer.content:
            raise RuntimeError("Boson AI sent back no sound")
        with open(out_path, "wb") as file:
            file.write(answer.content)

    @classmethod
    def __voices(cls, api_key: str) -> list | None:
        try:
            answer = requests.get(f"{cls.API_URL}/audio/voices", headers=_headers(api_key), timeout=15)
            found = answer.json() if answer.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            return None
        voices = found.get("data") if isinstance(found, dict) else None
        return voices if isinstance(voices, list) else None

    @classmethod
    def plan_is_full(cls, api_key: str) -> bool | None:
        voices = cls.__voices(api_key)
        return None if voices is None else len(voices) >= VOICE_LIMIT

    @classmethod
    def voice_exists(cls, api_key: str, voice_id: str) -> bool | None:
        try:
            answer = requests.get(f"{cls.API_URL}/audio/voices/{voice_id}", headers=_headers(api_key), timeout=15)
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
            answer = requests.get(f"{cls.API_URL}/audio/voices", headers=_headers(api_key), timeout=15)
        except requests.RequestException:
            return "Boson AI didn't answer. Check your internet connection and try again."
        code, message = _error(answer)
        if answer.status_code == 401:
            return f"Boson AI doesn't accept this key: {message}. Make a new one at {KEYS_PAGE}."
        if answer.status_code == 429 and code != "rate_limit_error":
            return _no_credit(code, message)
        if answer.status_code != 200:
            return f"Boson AI answered {answer.status_code}: {message}"
        voices = answer.json().get("data") or []
        mine = cloned_voices(save_folder, NAME).count()
        return (f"Your key works: Boson AI can clone voices. Your account has {len(voices)} of {VOICE_LIMIT} cloned voices; "
                f"Mantella's NPCs use {mine}. If you haven't yet, claim the $10 trial credit at {KEYS_PAGE}.")
