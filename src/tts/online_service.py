"""The shared part of the online voice services that clone voices on the player's account (ElevenLabs, Inworld).

Each clip is uploaded once; the voice the service makes is remembered (online_voices.py) and every line uses it. A swapped clip is
cloned again and the old voice deleted on the service. When the service refuses a new voice (its plan's limit, or a key that can't
clone), Mantella says why, the backup service speaks that line, and NPCs without a cloned voice use the service's built-in voices.
A service file only answers what differs: how to clone, delete, speak and check the key.
"""
from __future__ import annotations

import logging
import threading
import time
from abc import abstractmethod
from typing import Any

from src import utils
from src.config.config_loader import ConfigLoader
from src.tts.clip_service import ClipVoiceService, clip_hash
from src.tts.online_voices import VoiceKeeper, cloned_voices, key_fingerprint
from src.tts.registry import VoiceServiceInfo
from src.tts.voice_notices import add_notice
from src.tts.voice_picker import BUILT_IN, TRIAL_PREFIX, VoicePick, VoiceSpeaker
from src.tts.voice_setups import read_key_file

LINES_PER_CONVERSATION = 30
CHARACTERS_PER_LINE = 100 # a typical NPC line, for the cost shown on the page
RETRY_MINUTES = 5 # after a refusal, cloning is tried again: the player may have made room on their account meanwhile

CLONING = "cloning"
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


class CantSpeak(Exception):
    """The service won't speak any line: no characters or credit left, or a key it no longer accepts"""


class BadKey(Exception):
    """The service doesn't accept the key"""


class VoiceGone(Exception):
    """The service no longer has this voice (deleted on its website)"""


def cost_per_conversation(price_per_million_chars: float | None) -> str:
    if not price_per_million_chars:
        return ""
    dollars = price_per_million_chars * LINES_PER_CONVERSATION * CHARACTERS_PER_LINE / 1_000_000
    return f"about ${dollars:.2f} per conversation of {LINES_PER_CONVERSATION} lines"


class OnlineCloneService(ClipVoiceService):
    NAME = ""
    KEY_FILE = ""
    API_URL = ""
    WEBSITE = "" # where the player deletes voices or upgrades their plan
    request_timeout = 30.0
    _trials_cleared: set[str] = set() # services whose leftover tried voices were deleted this run

    def __init__(self, config: ConfigLoader, info: VoiceServiceInfo) -> None:
        self.__hashes: dict[tuple, str] = {}
        super().__init__(config, info)
        self._api_key = read_key_file(self.KEY_FILE)
        self._voices = cloned_voices(config.save_folder, self.NAME)
        self.__refused: str | None = None # why cloning is off for a while
        self.__refused_at = 0.0
        self.__paused: str | None = None # why speaking is off for a while: the Backup Voice Service speaks meanwhile
        self.__paused_at = 0.0
        self.__told: set[str] = set() # the error sound and the in-game notice, once for cloning and once for speaking
        self.__genders: dict[str, int | None] = {} # by voice, for the NPCs speaking now: their built-in voice when cloning is refused
        self.__language = config.language
        if self._api_key:
            forgotten = self._voices.use_account(key_fingerprint(self._api_key))
            if forgotten:
                logging.info(f"{self.NAME}: a new key, so the {forgotten} voice(s) cloned with the old one are cloned again on this account")
            self.__clear_leftover_trials(config.save_folder)

    # ---- what each online service answers ----

    @abstractmethod
    def clone(self, name: str, clip_path: str, language: str) -> str:
        """Uploads a clip and returns the new voice's ID. Raises CloneRefused, BadKey or another error"""

    @classmethod
    @abstractmethod
    def delete_voice(cls, api_key: str, voice_id: str) -> None:
        """Deletes one voice Mantella cloned on the service"""

    @abstractmethod
    def speak_with(self, text: str, voice_id: str, out_path: str) -> None:
        """One line in one voice into *out_path*. Raises VoiceGone when the service no longer has the voice"""

    @classmethod
    @abstractmethod
    def check_key(cls, api_key: str, save_folder: str) -> str:
        """What the Check my key button says"""

    @classmethod
    @abstractmethod
    def voice_exists(cls, api_key: str, voice_id: str) -> bool | None:
        """Whether the service still has this voice. None when it can't tell"""

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
            if not self.can_copy_voices and not self.voice_ready(pick):
                return VoicePick(BUILT_IN, self.built_in_voice(gender), None)
        return pick

    def is_ready(self) -> bool:
        """Not while speaking is off: the line goes straight to the Backup Voice Service, without asking the service twice"""
        return bool(self._api_key) and self.__pause() is None

    def start(self) -> None:
        raise RuntimeError(self.why_not)

    def serve_in_game(self) -> None:
        super().serve_in_game()
        if not self._api_key:
            logging.warning(self.why_not)
            add_notice(f"{self.NAME} has no key, so the backup voice service speaks. See Mantella's Text-to-Speech page.")

    def prepare_voice(self, pick: VoicePick) -> Any:
        """A clip is cloned on the service once; a swapped clip is cloned again and the old voice deleted there"""
        if not pick.clip_path:
            return pick.key # one of the service's built-in voices
        clip = self.__clip_hash(pick)
        kept = self._voices.get(pick.key)
        if kept is not None and kept.get("clip") == clip:
            return kept["voice_id"]
        if not self.can_copy_voices:
            return self.__built_in_instead(pick, self.why_not)
        try:
            voice_id = self.clone(self.__voice_name(pick), str(pick.clip_path), self.__language)
        except CloneRefused as e:
            return self.__built_in_instead(pick, self.__refuse(str(e)))
        except BadKey as e:
            return self.__built_in_instead(pick, self.__refuse(f"{self.NAME} doesn't accept your key, so it can't clone voices: {e}. Paste a new "
                                                               f"one under {self.NAME} on the Text-to-Speech page."))
        set_problem(self.NAME, None, CLONING) # cloning works again
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

    def speak(self, text: str, voice: Any, out_path: str) -> None:
        try:
            self.speak_with(text, str(voice), out_path)
            set_problem(self.NAME, None, SPEAKING) # speaking works again
        except CantSpeak as e:
            raise RuntimeError(self.__pause_speaking(str(e))) from e
        except VoiceGone as e:
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

    def __voice_name(self, pick: VoicePick) -> str:
        """The same name for a voice being tried: it may become the voice NPCs use (Clone from this clip), so nothing marks it as spare"""
        key = pick.key[len(TRIAL_PREFIX):] if pick.key.startswith(TRIAL_PREFIX) else pick.key
        return f"Mantella {key}"

    def __clip_hash(self, pick: VoicePick) -> str:
        version = (pick.clip_path, self._clip_version(pick))
        found = self.__hashes.get(version)
        if found is None:
            found = clip_hash(str(pick.clip_path))
            self.__hashes[version] = found
        return found

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
        message = f"{message} Until then the Backup Voice Service speaks. Mantella tries again in {RETRY_MINUTES} minutes."
        self.__paused, self.__paused_at = message, time.time()
        set_problem(self.NAME, message, SPEAKING)
        self.__tell(SPEAKING, message, f"{self.NAME} can't speak right now, so the backup voice service speaks. See Mantella's Text-to-Speech page.")
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

    def __clear_leftover_trials(self, save_folder: str) -> None:
        """Voices made to hear a clip on the Voice Clips page and never used: deleted once per run, so they don't fill the plan"""
        cls = type(self)
        if cls.NAME in OnlineCloneService._trials_cleared:
            return
        OnlineCloneService._trials_cleared.add(cls.NAME)
        threading.Thread(target=lambda: cls.keeper().remove_trial_voices(save_folder), name=f"{cls.NAME}: clear tried voices", daemon=True).start()
