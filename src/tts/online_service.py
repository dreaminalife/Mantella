"""The shared part of the online voice services that clone voices on the player's account (ElevenLabs, Inworld).

Each voice is cloned once from its chosen clips (as many as the service takes); the voice the service makes is remembered
(online_voices.py) and every line uses it. Other clips chosen mean cloning again, and the old voice is deleted on the service. When the service refuses a new voice (its plan's limit, or a key that can't
clone), Mantella says why, the backup service speaks that line, and NPCs without a cloned voice use the service's built-in voices.
A service file only answers what differs: how to clone, delete, speak and check the key.
"""
from __future__ import annotations

import io
import logging
import re
import threading
import time
from abc import abstractmethod
from typing import Any, Callable

import numpy as np
import requests
import soundfile as sf

from src import utils
from src.tts.audio_cleanup import decode
from src.config.config_loader import ConfigLoader
from src.tts.clip_service import ClipVoiceService, NoRetry, clips_hash
from src.tts.online_voices import VoiceKeeper, cloned_voices, key_fingerprint
from src.tts.registry import VoiceServiceInfo
from src.tts.voice_notices import add_notice
from src.tts.voice_picker import BUILT_IN, VoicePick, VoiceSpeaker
from src.tts.voice_setups import key_saves, read_key_file
from src.tts.model_catalog import ServiceModel, ServiceModels, with_model_limits
from src.tts.voice_style import EXTRA_REQUEST_LABEL, ServiceStyle, StyleSetting

RETRY_MINUTES = 5 # after a refusal, cloning is tried again: the player may have made room on their account meanwhile

CLONING = "cloning"
FAILED_VOICE = "failed voice"
SPEAKING = "speaking"

# By service: why it can't clone voices or speak right now, for its part of the Text-to-Speech page: (message, kind, when)
_problems: dict[str, tuple[str, str, float]] = {}
_problems_lock = threading.Lock()


def problem(service_name: str) -> str:
    """Only a recent one: Mantella tries again after RETRY_MINUTES, and sets it again if it still fails, so an older one may be fixed"""
    with _problems_lock:
        found = _problems.get(service_name)
        if found is None or time.time() - found[2] > RETRY_MINUTES * 60:
            return ""
        return found[0]


def set_problem(service_name: str, message: str | None, kind: str | None = None) -> None:
    """*message* None clears it: any problem, or only one of *kind*"""
    with _problems_lock:
        if message:
            _problems[service_name] = (message, kind or CLONING, time.time())
        elif kind is None or (service_name in _problems and _problems[service_name][1] == kind):
            _problems.pop(service_name, None)


class CloneRefused(Exception):
    """The service won't make another voice: the plan's limit, or a plan or key that can't clone voices"""


class CloneFailed(Exception):
    """The service couldn't make this one voice from its clips (it rejected the sound, or no words were heard in it). Only this voice
    uses a built-in voice; the others keep cloning, and these clips aren't tried again this session"""


# Voices a service couldn't make from these clips this session, by (service, voice, clips): why
_failed: dict[tuple[str, str, str], str] = {}
_failed_lock = threading.Lock()


class PlanFull(CloneRefused):
    """The plan holds no more voices. Deleting the voice's old copy first may make room"""


_VOICE_COUNT = re.compile(r"voice[_ ]limit|voice[_ ]slots?|(maximum|max|limit)\s+(number\s+)?of\s+(cloned\s+|custom\s+)?voices|too\s+many\s+(cloned\s+|custom\s+)?voices")


def says_plan_full(*texts: str) -> bool:
    """A refusal about how many voices the plan holds, not another limit (characters, sound length, requests)"""
    return any(_VOICE_COUNT.search(str(text or "").lower()) for text in texts)


class CantSpeak(Exception):
    """The service won't speak any line: no characters or credit left, or a key it no longer accepts"""


class ModelRefused(CantSpeak):
    """The service refused lines on a model Mantella doesn't know, or with the player's Settings Mantella doesn't have yet"""


class ModelCantClone(CloneRefused):
    """The picked model can't speak with cloned voices"""


class BadKey(Exception):
    """The service doesn't accept the key"""


class VoiceGone(Exception):
    """The service no longer has this voice (deleted on its website)"""


def _sample_rate(path: str) -> int:
    try:
        return sf.info(path).samplerate
    except Exception:
        return decode(path)[1]


def joined_clips(clip_paths: list[str], lowest_rate: int = 0) -> bytes:
    """Several clips as one WAV file, with a short silence between them, at the highest rate among them and at least *lowest_rate*,
    for a service that clones from one sound. One clip at a rate the service takes is sent as it is"""
    from src.voice_clips.clip_store import JOIN_GAP_SECONDS
    if len(clip_paths) == 1 and _sample_rate(clip_paths[0]) >= lowest_rate:
        with open(clip_paths[0], "rb") as clip:
            return clip.read()
    decoded = [decode(path) for path in clip_paths]
    rate = max([lowest_rate, *(r for _, r in decoded)])
    parts: list[np.ndarray] = []
    for samples, own_rate in decoded:
        if own_rate != rate and samples.size:
            length = int(round(samples.size * rate / own_rate))
            samples = np.interp(np.linspace(0, samples.size - 1, length), np.arange(samples.size), samples).astype(np.float32)
        if parts:
            parts.append(np.zeros(int(rate * JOIN_GAP_SECONDS), dtype=np.float32))
        parts.append(samples)
    out = io.BytesIO()
    sf.write(out, np.clip(np.concatenate(parts), -1.0, 1.0), rate, format="WAV", subtype="PCM_16")
    return out.getvalue()


def download(url: str, out_path: str, timeout: float, service_name: str) -> None:
    """A finished line a service hands back as a link (HeyGen, Qwen)"""
    answer = requests.get(url, timeout=timeout)
    if answer.status_code != 200 or not answer.content:
        raise RuntimeError(f"{service_name} made the line, but its sound could not be downloaded ({answer.status_code})")
    with open(out_path, "wb") as file:
        file.write(answer.content)


CLONE_WAIT_SECONDS = 30.0 # how long a line waits for a voice a service makes in the background
POLL_SECONDS = 2.5 # how often the service is asked meanwhile (HeyGen allows 30 requests a minute)


class StillCloning(RuntimeError):
    """The service is still making the voice: the next line asks about the same voice instead of cloning again"""


def wait_until_ready(is_ready: Callable[[], bool], service_name: str, seconds: float | None = None) -> None:
    """A voice a service makes in the background (HeyGen, Qwen): asked until it is ready. *is_ready* raises when making it failed"""
    deadline = time.monotonic() + (CLONE_WAIT_SECONDS if seconds is None else seconds)
    while True:
        try:
            if is_ready():
                return
        except requests.RequestException:
            pass # no answer this time: the voice may still be fine
        if time.monotonic() >= deadline:
            raise StillCloning(f"{service_name} is still cloning the voice, so the backup service speaks this line. A later line uses it once it is ready.")
        time.sleep(POLL_SECONDS)


# Voices a service is still making, by (service, account, voice name, clips): asked about again instead of cloned again
_cloning: dict[tuple[str, str, str, str], str] = {}
_cloning_lock = threading.Lock()


def clone_in_background(service_name: str, api_key: str, name: str, clip_paths: list[str], start: Callable[[], str],
                        is_ready: Callable[[str], bool], delete: Callable[[str], None], settings: str = "") -> str:
    """Clones a voice a service makes in the background (HeyGen, Qwen) and waits for it.

    *start* asks the service for the voice and returns its ID; *is_ready(voice_id)* says whether it is ready and raises when the service
    failed to make it; *delete(voice_id)* deletes it on the service. A voice not ready in time is kept for the next line, so a slow service
    never gets the same voice cloned again and again. A voice the service failed to make is deleted there.
    *settings*: what else the voice is cloned with (HeyGen's Similarity), so a clone started with other settings isn't taken for it"""
    key = (service_name, key_fingerprint(api_key), name, clips_hash(clip_paths) + (f"|{settings}" if settings else ""))
    with _cloning_lock:
        voice_id = _cloning.get(key)
    waited_before = voice_id is not None # a line already waited for it: asked once, so the retry doesn't keep the NPC silent longer
    if voice_id is None:
        voice_id = start()
        with _cloning_lock:
            _cloning[key] = voice_id
    try:
        wait_until_ready(lambda: is_ready(voice_id), service_name, 0.0 if waited_before else None)
    except StillCloning:
        raise
    except Exception:
        with _cloning_lock:
            _cloning.pop(key, None)
        try:
            delete(voice_id)
        except Exception as e:
            logging.warning(f"{service_name}: could not delete a voice it failed to clone ({voice_id}). You can delete it on its website: {e}")
        raise
    with _cloning_lock:
        _cloning.pop(key, None)
    return voice_id


def merged(body: dict, extra: dict) -> dict:
    """*body* with *extra*'s fields: extra's win, and two fields that both hold fields are merged the same way"""
    found = dict(body)
    for name, value in extra.items():
        if isinstance(value, dict) and isinstance(found.get(name), dict):
            found[name] = merged(found[name], value)
        else:
            found[name] = value
    return found


class OnlineCloneService(ClipVoiceService):
    NAME = ""
    KEY_FILE = ""
    API_URL = ""
    WEBSITE = "" # where the player deletes voices or upgrades their plan
    MODELS: ServiceModels | None = None # the Model setting and Mantella's list of models (model_catalog.py)
    request_timeout = 30.0
    SHORT_LINE_WAIT = 12.0 # seconds an NPC waits for a short line before the Backup Voice Service speaks it
    WAIT_PER_CHARACTER = 0.04 # a longer line takes the service longer to make
    LONGEST_LINE_WAIT = 30.0
    LATE_LIMIT = 2 # lines in a row not answered in time before speaking is off for a while: one slow line can happen
    _tidied: set[str] = set() # services whose voices left over from trying clips were deleted this run

    def __init__(self, config: ConfigLoader, info: VoiceServiceInfo) -> None:
        self.__hashes: dict[tuple, str] = {}
        super().__init__(config, info)
        self.__key_lock = threading.Lock()
        self.__key_saves = key_saves(self.KEY_FILE)
        self.__key = read_key_file(self.KEY_FILE)
        self._voices = cloned_voices(config.save_folder, self.NAME)
        self.__refused: str | None = None # why cloning is off for a while
        self.__refused_at = 0.0
        self.__paused: str | None = None # why speaking is off for a while: the Backup Voice Service speaks meanwhile
        self.__paused_at = 0.0
        self.__late = 0 # lines in a row the service didn't answer in time
        self.__told: set[str] = set() # the error sound and the in-game notice, once for cloning and once for speaking
        self.__genders: dict[str, int | None] = {} # by voice, for the NPCs speaking now: their built-in voice when cloning is refused
        self.__language = config.language
        self._style = ServiceStyle(config, self.NAME)
        self.__use_key()

    @property
    def _api_key(self) -> str:
        """The saved key. A key saved with Save key while the service runs is used from the next line on, and by Hear it, without
        waiting for the game to start the voice services again"""
        if key_saves(self.KEY_FILE) != self.__key_saves:
            with self.__key_lock:
                saves = key_saves(self.KEY_FILE)
                if saves != self.__key_saves:
                    self.__key = read_key_file(self.KEY_FILE)
                    self.__key_saves = saves
                    logging.info(f"{self.NAME}: a new key was saved, so it is used from now on")
                    # What went wrong was about the old key: cloning and speaking are tried again with the new one
                    self.__refused = self.__paused = None
                    self.__late = 0
                    self.__told.clear()
                    self.forget_prepared_voices() # voice IDs from the old key's account
                    self.__use_key()
        return self.__key

    def __use_key(self) -> None:
        """A key read: voices from another key's account are forgotten, and voices left over from trying clips are deleted, once a run"""
        if not self.__key:
            return
        forgotten = self._voices.use_account(key_fingerprint(self.__key))
        if forgotten:
            logging.info(f"{self.NAME}: a new key, so the {forgotten} voice(s) cloned with the old one are cloned again on this account")
        if self.NAME not in OnlineCloneService._tidied:
            OnlineCloneService._tidied.add(self.NAME)
            self.keeper().remove_old_tries(self._config.save_folder, self.__key)

    # ---- what each online service answers ----

    @abstractmethod
    def clone(self, name: str, clip_paths: list[str], language: str, pick: VoicePick | None = None) -> str:
        """Uploads the clips, in order, and returns the new voice's ID. Raises CloneRefused (PlanFull), BadKey or another error.
        *pick*: the voice being cloned, for the settings it is cloned with (clone_settings)"""

    @classmethod
    @abstractmethod
    def delete_voice(cls, api_key: str, voice_id: str) -> None:
        """Deletes one voice Mantella cloned on the service"""

    @abstractmethod
    def speak_with(self, text: str, voice_id: str, out_path: str, pick: VoicePick | None = None) -> None:
        """One line in one voice into *out_path*. Raises VoiceGone when the service no longer has the voice.
        *pick*: the voice the line is for, for its style settings (style). None: the page's"""

    def line_wait(self, text: str) -> float:
        """Seconds to wait for the service's answer to this line (and for its sound file): a longer line takes longer to make"""
        return min(self.SHORT_LINE_WAIT + self.WAIT_PER_CHARACTER * len(text), self.LONGEST_LINE_WAIT)

    def clone_settings(self, pick: VoicePick) -> str:
        """What else a voice is cloned with besides its clips (HeyGen's Similarity). Changing it clones that voice again"""
        return ""

    def style(self, pick: VoicePick | None, setting: StyleSetting) -> Any:
        """A style setting for a line in this voice, for the picked model: the voice's own from Customization per voice type and per NPC, else the Text-to-Speech page's"""
        return self._style.voice_value(self._clips, pick, setting)

    @property
    def model(self) -> ServiceModel:
        """The model picked for the service now"""
        if self.MODELS is None:
            raise RuntimeError(f"{self.NAME} has no Model setting")
        return self.MODELS.picked(self._config)

    def _limits(self):
        return with_model_limits(self._config, self._info)

    def _request_body(self, body: dict) -> dict:
        """A line's request with the picked model's Settings Mantella doesn't have yet merged in: the player's fields win, and a
        field holding fields (ElevenLabs' voice_settings) gets theirs added"""
        return merged(body, self._style.extra_request())

    def _check_model_refusal(self, status_code: int, message: str) -> None:
        """A line refused (400 or 422) because of the player's own fields, or a model typed in that the service doesn't have: every
        line would be refused the same way, so the service pauses and says why. Other refusals are left to the service"""
        if status_code not in (400, 422):
            return
        refused = self._model_refusal(message)
        if refused is not None:
            raise refused

    def _model_refusal(self, message: str, not_found: bool = False) -> ModelRefused | None:
        """Why lines are refused, when it is the player's own fields (any refusal: they are the likely cause) or a model typed in
        that isn't on Mantella's list (only when the service's words name the model, so one odd line doesn't pause the service).
        *not_found*: the service said "not found", which is only about the model if it names it"""
        model = self.model if self.MODELS is not None else None
        if model is None:
            return None
        about_model = "model" in message.lower() or model.id.lower() in message.lower()
        extra = bool(self._style.extra_request()) and not not_found
        if not extra and (model.known or not about_model):
            return None
        check = (f" Check {EXTRA_REQUEST_LABEL} for {model.name} on the Text-to-Speech page, or pick another model." if extra
                 else " Check the model's name on the Text-to-Speech page, or pick another model.")
        return ModelRefused(f"{self.NAME} refused lines on {model.name}: \"{message}\".{check}")

    @classmethod
    @abstractmethod
    def check_key(cls, api_key: str, save_folder: str) -> str:
        """What the Check my key button says"""

    @classmethod
    @abstractmethod
    def voice_exists(cls, api_key: str, voice_id: str) -> bool | None:
        """Whether the service still has this voice. None when it can't tell"""

    @classmethod
    def plan_is_full(cls, api_key: str) -> bool | None:
        """Whether every voice slot on the account is in use, as the service itself says. None when it can't tell"""
        return None

    @classmethod
    def keeper(cls) -> VoiceKeeper:
        return VoiceKeeper(cls.NAME, cls.KEY_FILE, cls.delete_voice)

    # ---- what the shared part asks ----

    @property
    def can_copy_voices(self) -> bool:
        return bool(self._api_key) and self.__refusal() is None and self.__pause() is None

    @property
    def why_not(self) -> str:
        if not self._api_key:
            return f"{self.NAME} has no key ({self.KEY_FILE} is missing or empty). Paste one under {self.NAME} on the Text-to-Speech page."
        return self.__pause() or self.__refusal() or ""

    def pick_voice(self, speaker: VoiceSpeaker | None, named_voice: str | None = None) -> VoicePick:
        pick = super().pick_voice(speaker, named_voice)
        gender = speaker.gender if speaker else None
        if pick.clip_path:
            self.__genders[pick.key] = gender
            if not self.can_copy_voices and not self.voice_ready(pick) and self._voices.get(pick.key) is None:
                return VoicePick(BUILT_IN, self.built_in_voice(gender), None)
        return pick

    def is_ready(self) -> bool:
        """Not while speaking is off: the line goes straight to the Backup Voice Service, without asking the service twice"""
        return bool(self._api_key) and self.__pause() is None

    def start(self) -> None:
        raise NoRetry(self.why_not)

    def serve_in_game(self) -> None:
        super().serve_in_game()
        if not self._api_key:
            logging.warning(self.why_not)
            add_notice(f"{self.NAME} has no key, so the backup voice service speaks. See Mantella's Text-to-Speech page.")

    def prepare_voice(self, pick: VoicePick) -> Any:
        """A voice is cloned on the service once; other clips chosen mean cloning again, and the old voice is deleted there"""
        if not pick.clip_path:
            return pick.key # one of the service's built-in voices
        clips = self.clips_for(pick)
        clip = self.__clip_hash(pick)
        kept = self._voices.get(pick.key)
        if kept is not None and kept.get("clip") == clip:
            return kept["voice_id"]
        with _failed_lock:
            failed = _failed.get((self.NAME, pick.key, clip))
        if failed is not None: # these clips can't make a voice: not tried again this session
            return kept["voice_id"] if kept is not None and self._api_key else self.__built_in_instead(pick, failed)
        if not self.can_copy_voices:
            if kept is not None and self._api_key:
                return kept["voice_id"] # cloning is refused for now: NPCs keep the voice made from the clips chosen before
            return self.__built_in_instead(pick, self.why_not)
        try:
            try:
                voice_id = self.clone(self.__voice_name(pick), clips, self.__language, pick)
            except PlanFull:
                # Only when the service confirms every slot is in use: a refusal for another limit (a monthly cap on making voices)
                # would fail again, and the voice NPCs use now would be gone for nothing
                if kept is None or self.plan_is_full(self._api_key) is not True:
                    raise
                # The plan is full: this voice's old copy makes room for the new one
                logging.info(f"{self.NAME}: your plan holds no more voices, so the old voice for {pick.key} is deleted first")
                self.delete_voice(self._api_key, kept.get("voice_id", ""))
                self._voices.pop(pick.key)
                kept = None
                voice_id = self.clone(self.__voice_name(pick), clips, self.__language, pick)
        except CloneRefused as e:
            why = self.__refuse(str(e))
            if kept is not None:
                return kept["voice_id"] # the voice made from the clips chosen before, until cloning works again
            return self.__built_in_instead(pick, why)
        except BadKey as e:
            return self.__built_in_instead(pick, self.__refuse(f"{self.NAME} doesn't accept your key, so it can't clone voices: {e}. Paste a new "
                                                               f"one under {self.NAME} on the Text-to-Speech page."))
        except CloneFailed as e:
            why = self.__voice_failed(pick, clip, str(e))
            if kept is not None:
                return kept["voice_id"] # the voice made from the clips chosen before
            return self.__built_in_instead(pick, why)
        except (requests.ConnectionError, requests.Timeout) as e:
            raise NoRetry(self.__unreachable(e)) from e
        set_problem(self.NAME, None, CLONING) # cloning works again
        self.__late = 0
        self._voices.set(pick.key, voice_id, clip)
        if kept is not None and kept.get("voice_id") != voice_id:
            self.keeper().delete_later([kept.get("voice_id", "")], self._api_key) # the voice from the clip that was swapped
        return voice_id

    def voice_ready(self, pick: VoicePick) -> bool:
        if not pick.clip_path:
            return True
        try:
            clip = self.__clip_hash(pick)
        except OSError:
            return False
        kept = self._voices.get(pick.key)
        return kept is not None and kept.get("clip") == clip

    def speak_line(self, text: str, voice: Any, out_path: str, pick: VoicePick | None) -> None:
        self.speak(text, voice, out_path, pick)

    def speak(self, text: str, voice: Any, out_path: str, pick: VoicePick | None = None) -> None:
        try:
            self.__speak_once_more_if_cut(text, str(voice), out_path, pick)
            set_problem(self.NAME, None, SPEAKING) # speaking works again
            self.__late = 0
        except CantSpeak as e:
            raise NoRetry(self.__pause_speaking(str(e))) from e
        except (requests.ConnectionError, requests.Timeout) as e:
            raise NoRetry(self.__unreachable(e)) from e
        except VoiceGone as e:
            refused = self._model_refusal(str(e), not_found=True)
            if refused is not None: # "not found" about a model typed in, not the voice: every line would fail the same way
                raise NoRetry(self.__pause_speaking(str(refused))) from e
            if self.voice_exists(self._api_key, str(voice)) is not False:
                # The service says "not found" about something else (or can't tell): cloning again wouldn't help, and would use up a voice
                raise RuntimeError(f"{self.NAME} could not speak the line: {e}") from e
            # Deleted on the website: forgotten, so the retry clones the clip again
            self._voices.pop_voice_id(str(voice))
            with self._prepare_lock:
                for pick in [p for p, (_, v) in self._prepared.items() if v == voice]:
                    del self._prepared[pick]
            raise RuntimeError(f"{self.NAME} no longer has this voice, so it is cloned again") from e

    # ---- inside ----

    @staticmethod
    def __voice_name(pick: VoicePick) -> str:
        return f"Mantella {pick.key}"

    def __clip_hash(self, pick: VoicePick) -> str:
        """Names the clips the voice is cloned from, read once per version of the files, and what else it is cloned with"""
        version = self._clip_version(pick)
        found = self.__hashes.get(version) if version else None
        if found is None:
            found = clips_hash(self.clips_for(pick))
            if version:
                self.__hashes[version] = found
        settings = self.clone_settings(pick)
        return f"{found}|{settings}" if settings else found

    def __built_in_instead(self, pick: VoicePick, why: str) -> Any:
        """Cloning is refused while an NPC is speaking: this line, and their next ones, use the service's built-in voice instead of
        the Backup Voice Service, so the NPC's voice doesn't jump between services. A clip heard on the Voice Clips page has no NPC
        speaking it, so there the refusal is shown instead"""
        if pick.key not in self.__genders:
            raise RuntimeError(why)
        return self.built_in_voice(self.__genders[pick.key])

    def __refusal(self) -> str | None:
        """Why cloning is off, until RETRY_MINUTES after the refusal"""
        if self.__refused is not None and time.time() - self.__refused_at >= RETRY_MINUTES * 60:
            self.__refused = None
        return self.__refused

    def __pause(self) -> str | None:
        """Why speaking is off, until RETRY_MINUTES after it failed"""
        if self.__paused is not None and time.time() - self.__paused_at >= RETRY_MINUTES * 60:
            self.__paused = None
        return self.__paused

    def __refuse(self, message: str) -> str:
        """Cloning is off for a while. Returns the message"""
        message = f"{message} Mantella tries again in {RETRY_MINUTES} minutes."
        self.__refused, self.__refused_at = message, time.time()
        set_problem(self.NAME, message, CLONING)
        self.__tell(CLONING, message, f"{self.NAME} can't clone voices right now, so NPCs without one use its built-in voices. "
                                      "See Mantella's Text-to-Speech page.")
        return message

    def __pause_speaking(self, message: str) -> str:
        """Speaking is off for a while: every line goes to the Backup Voice Service. Returns the message"""
        message = f"{message} The Backup Voice Service speaks until Mantella tries again in {RETRY_MINUTES} minutes."
        self.__paused, self.__paused_at = message, time.time()
        self.__late = 0 # counted again from the start once the service is asked again
        set_problem(self.NAME, message, SPEAKING)
        self.__tell(SPEAKING, message, f"{self.NAME} can't speak right now, so the backup voice service speaks. See Mantella's Text-to-Speech page.")
        return message

    def __speak_once_more_if_cut(self, text: str, voice_id: str, out_path: str, pick: VoicePick | None) -> None:
        """A service sometimes cuts one request off midway while the next one works: the line is sent once more before the Backup
        Voice Service takes over. Not after no answer in time: the NPC would wait that long again"""
        try:
            self.speak_with(text, voice_id, out_path, pick)
        except requests.ConnectionError as e:
            if isinstance(e, requests.Timeout):
                raise
            logging.debug(f"{self.NAME} cut the connection, so the line is sent once more: {e}")
            self.speak_with(text, voice_id, out_path, pick)

    def __unreachable(self, error: Exception) -> str:
        """No connection, or LATE_LIMIT lines in a row not answered in time: every line goes to the Backup Voice Service for a
        while, so NPCs don't wait on each line, and a new voice isn't cloned again on each line. One line not answered in time
        only goes to the Backup Voice Service itself: one slow line can happen. Returns the message"""
        logging.debug(f"{self.NAME}: {error}")
        if isinstance(error, requests.Timeout):
            self.__late += 1
            if self.__late < self.LATE_LIMIT:
                return f"{self.NAME} didn't answer in time (it is slow or not answering)." # who speaks instead is added by the caller
            return self.__pause_speaking(f"{self.NAME} didn't answer in time {self.LATE_LIMIT} lines in a row (it is slow or not answering).")
        return self.__pause_speaking(f"Mantella lost the connection to {self.NAME} (is the internet down?).")

    def __voice_failed(self, pick: VoicePick, clip: str, reason: str) -> str:
        """One voice couldn't be made from its clips: remembered, so these clips aren't sent again this session. Returns the message"""
        message = (f"{self.NAME} couldn't clone the voice for {pick.key} from its clips: {reason}. It uses {self.NAME}'s built-in voice "
                   "(or the voice cloned before) until you pick other clips for it under Customization per voice type and per NPC on the Text-to-Speech page.")
        with _failed_lock:
            _failed[(self.NAME, pick.key, clip)] = message
        set_problem(self.NAME, message, CLONING)
        self.__tell(FAILED_VOICE, message, f"{self.NAME} couldn't clone some voices, so those NPCs use its built-in voices. "
                                           "See Mantella's Text-to-Speech page.")
        return message

    def __tell(self, kind: str, message: str, notice: str) -> None:
        """The first time: the error sound, the log and a notice in game. After that only the log"""
        if kind in self.__told:
            logging.warning(message)
            return
        self.__told.add(kind)
        utils.play_error_sound()
        logging.error(message)
        add_notice(notice)
