"""Iodine (iodine.dev), online. Each voice is cloned once on the player's account from its chosen clips joined into one sound of up to
30 seconds, together with the words spoken in them, as Iodine recommends: Mantella hears those once with the Speech-to-Text choice
(transcripts.py). Without them Iodine hears the clips itself. Up to 50 cloned voices per account; cloning works on the free plan.
$8 per million characters (2026-11-21).

Takes cues in curly brackets: moods such as {angry}, directions such as {quietly, trying not to laugh}, sounds such as {laughs} or
{pause}, and word styles that wrap a few words, such as {whisper}just between us{/whisper}. The AI writes them in square brackets,
because curly brackets are how Mantella's prompts write their variables; IodineCues turns them into Iodine's.
How voices sound (voice_style.py): Temperature, kept per model and per voice.

Lines longer than 275 characters get a "502 Bad Gateway" from Iodine (measured on 2026-11-21; its docs say 5,000). That is taken as
Iodine's bug: such a line fails like any other, and the Backup Voice Service speaks it."""
from __future__ import annotations


import requests

from src.tts.model_catalog import IODINE_MODELS
from src.tts.online_service import BadKey, CantSpeak, CloneFailed, CloneRefused, OnlineCloneService, PlanFull, VoiceGone, joined_clips
from src.tts.registry import IODINE_LANGUAGES
from src.tts.voice_picker import VoicePick
from src.tts.voice_style import IODINE_TEMPERATURE
from src.tts.voice_tags import RESET, SOUND_WORDS, TAG_RE, ServiceCues, VoiceTags, tag_key, tidy
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VOICES)

NAME = "Iodine"
KEY_FILE = "IODINE_SECRET_KEY.txt"
API_URL = "https://api.iodine.dev/v1"
WEBSITE = "https://iodine.dev/console"
KEYS_PAGE = "https://iodine.dev/console"
BILLING_PAGE = "https://iodine.dev/console/billing"
SIGN_UP_PAGE = "https://iodine.dev/console"
MODELS = IODINE_MODELS # Mantella's list (model_catalog.py): Iodine v2
VOICE_LIMIT = 50
MALE_VOICE = "zander" # Iodine's own voices
FEMALE_VOICE = "mia"
AUDIO_TAGS_SETTING = "iodine_audio_tags"
DIRECTIONS_SETTING = "iodine_directions"
CLONE_LANGUAGE = "auto" # Iodine hears the clips' language itself
LONGEST_NAME = 64
LONGEST_TRANSCRIPT = 2000
LANGUAGES = IODINE_LANGUAGES # the languages Iodine speaks (registry.py)
# Mantella's word for a sound -> how Iodine writes it
SOUND_NAMES = {"laugh": "laughs", "giggle": "giggles", "sigh": "sighs", "gasp": "gasps", "sniffle": "sniffles", "sob": "sobs",
               "cough": "coughs", "clear throat": "clears throat", "shaky breath": "shaky breath", "pause": "pause", "long pause": "long pause"}
# Iodine's emotions. One changes how the rest of its sentence is said
MOODS = ("happy", "sad", "angry", "scared", "excited", "calm", "nervous", "tired", "playful", "serious", "warm", "sarcastic")
# Iodine's word styles: [whisper]...[/whisper] says only the words between them that way
WORD_STYLES = ("whisper", "soft", "loud", "emphasis", "fast", "slow")
SOUNDS = tuple(SOUND_NAMES)
SOUND_WORDS_NOT_TAKEN = SOUND_WORDS - set(SOUND_NAMES) # sounds other services make: never sent to Iodine as a direction
# The last word of a sound the AI makes up ([small laugh], [deep breath]): the sounds Mantella knows, and a few nouns for them
SOUND_ROOTS = frozenset({phrase.split()[-1] for phrase in SOUND_WORDS} | {"breath", "laughter", "cry", "sniff"})
MADE_UP_SOUND_WORDS = 2


def voice_tags(config) -> VoiceTags:
    """What Iodine takes with the current settings: sounds, moods and word styles with Audio tags on, directions
    with Directions on"""
    audio_tags = MODELS.switched_on(config, AUDIO_TAGS_SETTING)
    directions = MODELS.switched_on(config, DIRECTIONS_SETTING)
    if not audio_tags:
        return VoiceTags(directions=directions)
    return VoiceTags(SOUNDS, directions, moods=MOODS, styles=WORD_STYLES)


def spoken_language(language: str | None) -> str | None:
    """The language code Iodine is told a line is in, when it speaks it. None: Iodine hears it from the words"""
    code = str(language or "").lower().split("-")[0]
    return code if code in LANGUAGES else None


class IodineCues(ServiceCues):
    """The AI's square-bracket tags as Iodine's curly ones, each where the AI put it. A closing tag with no opening one is dropped; an
    opening one with no closing tag covers the rest of the sentence, as Iodine does it. An emotion and a direction with no words between
    them never both reach Iodine, also with a sound or word style between them: the second is dropped. A short sound the AI makes up,
    such as [small laugh], is a sound, not a direction. Curly brackets in the words themselves are taken out, so Iodine never reads them
    as cues"""

    MOOD, DIRECTION, OTHER = "mood", "direction", "other"

    def __init__(self, directions: bool) -> None:
        self.__directions = directions

    def text(self, line: str) -> str:
        line = line.replace("{", "").replace("}", "")
        if "[" not in line:
            return line
        parts: list[str] = []
        open_styles: list[str] = []
        position = 0
        feeling_before = False # a mood or direction was written, with no words after it yet
        for match in TAG_RE.finditer(line):
            between = line[position:match.start()]
            if between.strip():
                feeling_before = False
            parts.append(between)
            position = match.end()
            cue, kind = self.__cue(match.group(1).strip(), open_styles)
            if cue is None:
                continue
            if kind in (self.MOOD, self.DIRECTION):
                if feeling_before:
                    continue
                feeling_before = True
            parts.append("{" + cue + "}")
        parts.append(line[position:])
        return tidy("".join(parts))

    def __cue(self, written: str, open_styles: list[str]) -> tuple[str | None, str]:
        """Iodine's cue for one tag (without its brackets) and its kind. None: left out"""
        key = tag_key(written)
        if not key or key == RESET:
            return None, self.OTHER
        if key.startswith("/"):
            style = key[1:].strip()
            if style in open_styles:
                open_styles.remove(style)
                return f"/{style}", self.OTHER
            return None, self.OTHER
        if key in WORD_STYLES:
            if key in open_styles:
                return None, self.OTHER
            open_styles.append(key)
            return key, self.OTHER
        if key in SOUND_NAMES:
            return SOUND_NAMES[key], self.OTHER
        if key in MOODS:
            return key, self.MOOD
        if not self.__directions or key in SOUND_WORDS_NOT_TAKEN:
            return None, self.OTHER
        return written, (self.OTHER if made_up_sound(key) else self.DIRECTION)


def made_up_sound(key: str) -> bool:
    """A short sound the AI made up, such as "small laugh" or "deep breath": one or two words, the last one a sound. A longer tag
    ("trying not to laugh") is a direction"""
    words = key.split()
    if not words or len(words) > MADE_UP_SOUND_WORDS:
        return False
    last = words[-1]
    return any(form in SOUND_ROOTS for form in (last, last[:-1] if last.endswith("s") else "", last[:-2] if last.endswith("es") else ""))


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _error(answer: requests.Response) -> tuple[str, str]:
    """Iodine's (code, message). A page that isn't Iodine's own answer (Cloudflare's 502) gives its status in words"""
    try:
        error = answer.json().get("error")
    except (ValueError, AttributeError):
        return "", (answer.reason or answer.text[:100].strip() or str(answer.status_code))
    if not isinstance(error, dict):
        return "", str(error or answer.text[:300])
    return str(error.get("code") or ""), str(error.get("message") or error)[:300].rstrip(" .")


def _no_credit(message: str) -> str:
    return f"Iodine has no credit left ({message}). Add some at {BILLING_PAGE}."


class Iodine(OnlineCloneService):
    NAME = NAME
    KEY_FILE = KEY_FILE
    API_URL = API_URL
    WEBSITE = WEBSITE
    MODELS = MODELS

    def built_in_voice(self, gender: int | None) -> str:
        return FEMALE_VOICE if gender == 1 else MALE_VOICE

    def clone(self, name: str, clip_paths: list[str], language: str, pick: VoicePick | None = None) -> str:
        clip_paths, transcript = self.__transcript(clip_paths)
        sound = joined_clips(clip_paths)
        answer = self.__send_clone(name, sound, transcript)
        code, message = _error(answer)
        if transcript and code == "invalid_transcript":
            log.info(f"Iodine didn't take the words heard in the clips for {name} ({message}), so it hears them itself")
            answer = self.__send_clone(name, sound, "")
            code, message = _error(answer)
        if answer.status_code == 401:
            raise BadKey(message)
        if code == "too_many_voices":
            raise PlanFull(f"Iodine won't clone another voice: your account keeps {VOICE_LIMIT} and they are all used ({message}). "
                           f"Delete some at {WEBSITE}. Until then NPCs without a cloned voice use Iodine's built-in voices.")
        if answer.status_code == 402:
            raise CloneRefused(_no_credit(message))
        if code.startswith("audio_") or code in ("invalid_audio", "missing_audio"):
            raise CloneFailed(message)
        if answer.status_code not in (200, 201):
            raise RuntimeError(f"Iodine answered {answer.status_code} when cloning: {message}")
        try:
            found = answer.json()
        except ValueError:
            found = None
        voice = found.get("voice") if isinstance(found, dict) else None
        voice_id = str(voice.get("id") or "") if isinstance(voice, dict) else ""
        if not voice_id:
            raise RuntimeError("Iodine didn't say which voice it made")
        return voice_id

    def __transcript(self, clip_paths: list[str]) -> tuple[list[str], str]:
        """The clips to send and the words spoken in them, in order. A clip with no words heard is left out, so the words match the
        sound. When Speech-to-Text can't hear them, or they are too long for Iodine, every clip is sent without words"""
        from src.voice_clips.transcripts import words_for
        try:
            heard = words_for(clip_paths, self._config)
        except Exception as e:
            log.warning(f"Speech-to-Text couldn't hear the clips' words ({e}), so Iodine hears them itself")
            return clip_paths, ""
        kept = [(path, words.strip()) for path, words in zip(clip_paths, heard) if words and words.strip()]
        transcript = " ".join(words for _, words in kept)
        if not kept or len(transcript) > LONGEST_TRANSCRIPT:
            return clip_paths, ""
        return [path for path, _ in kept], transcript

    def __send_clone(self, name: str, sound: bytes, transcript: str) -> requests.Response:
        fields = {"name": name[:LONGEST_NAME], "consent": "true", "language": CLONE_LANGUAGE}
        if transcript:
            fields["transcript"] = transcript
        return requests.post(f"{type(self).API_URL}/voices", headers=_headers(self._api_key), data=fields,
                             files={"file": ("voice.wav", sound, "audio/wav")}, timeout=60)

    @classmethod
    def delete_voice(cls, api_key: str, voice_id: str) -> None:
        answer = requests.delete(f"{cls.API_URL}/voices/{voice_id}", headers=_headers(api_key), timeout=30)
        if answer.status_code not in (200, 204, 404) and _error(answer)[0] != "unknown_voice":
            raise RuntimeError(f"Iodine answered {answer.status_code}: {_error(answer)[1]}")

    def speak_with(self, text: str, voice_id: str, out_path: str, pick: VoicePick | None = None) -> None:
        model = self.model
        body = {"text": IodineCues(voice_tags(self._config).directions).text(text), "voice": voice_id, "model": model.id, "format": "wav",
                "stream": False}
        language = spoken_language(getattr(self._config, "language", ""))
        if language:
            body["language"] = language
        if model.takes(IODINE_TEMPERATURE.name):
            body["temperature"] = self.style(pick, IODINE_TEMPERATURE)
        answer = requests.post(f"{type(self).API_URL}/tts", headers={**_headers(self._api_key), "Content-Type": "application/json"},
                               json=self._request_body(body), timeout=self.line_wait(text))
        if answer.status_code != 200:
            code, message = _error(answer)
            if voice_id not in (MALE_VOICE, FEMALE_VOICE) and (answer.status_code == 404 or code == "unknown_voice"):
                raise VoiceGone(message)
            if answer.status_code == 401:
                raise CantSpeak(f"Iodine doesn't accept your key ({message}). Make a new one at {KEYS_PAGE} and press Save key.")
            if answer.status_code == 402:
                raise CantSpeak(_no_credit(message))
            if code == "invalid_language":
                raise CantSpeak(f"Iodine doesn't speak your Language setting ({message}). Pick another voice service for this language.")
            self._check_model_refusal(answer.status_code, message)
            raise RuntimeError(f"Iodine answered {answer.status_code}: {message}")
        if not answer.content:
            raise RuntimeError("Iodine sent back no sound")
        with open(out_path, "wb") as file:
            file.write(answer.content)

    @classmethod
    def __voices(cls, api_key: str) -> list | None:
        try:
            answer = requests.get(f"{cls.API_URL}/voices", headers=_headers(api_key), timeout=15)
            found = answer.json() if answer.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            return None
        voices = found.get("voices") if isinstance(found, dict) else None
        return voices if isinstance(voices, list) else None

    @staticmethod
    def __cloned(voices: list) -> list[dict]:
        """The account's cloned voices, not Iodine's own"""
        return [v for v in voices if isinstance(v, dict) and (v.get("custom") is True or str(v.get("id", "")).startswith("vc_"))]

    @classmethod
    def plan_is_full(cls, api_key: str) -> bool | None:
        voices = cls.__voices(api_key)
        return None if voices is None else len(cls.__cloned(voices)) >= VOICE_LIMIT

    @classmethod
    def voice_exists(cls, api_key: str, voice_id: str) -> bool | None:
        voices = cls.__voices(api_key) # Iodine has no call for one voice
        if voices is None:
            return None
        return any(isinstance(v, dict) and v.get("id") == voice_id for v in voices)

    @classmethod
    def check_key(cls, api_key: str, save_folder: str) -> str:
        from src.tts.online_voices import cloned_voices
        if not api_key:
            return "Paste your key first."
        try:
            answer = requests.get(f"{cls.API_URL}/voices", headers=_headers(api_key), timeout=15)
        except requests.RequestException:
            return "Iodine didn't answer. Check your internet connection and try again."
        code, message = _error(answer)
        if answer.status_code == 401:
            return f"Iodine doesn't accept this key: {message}. Make a new one at {KEYS_PAGE}."
        if answer.status_code == 402:
            return _no_credit(message)
        if answer.status_code != 200:
            return f"Iodine answered {answer.status_code}: {message}"
        found = answer.json()
        voices = found.get("voices") if isinstance(found, dict) and isinstance(found.get("voices"), list) else []
        mine = cloned_voices(save_folder, NAME).count()
        return (f"Your key works: Iodine can clone voices. Your account has {len(cls.__cloned(voices))} of {VOICE_LIMIT} cloned voices; "
                f"Mantella's NPCs use {mine}.")
