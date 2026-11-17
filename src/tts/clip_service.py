"""The base of every new voice service (Pocket TTS, the player's voice setups, online services).

Everything that is the same for all of them lives here, written once:
choosing the NPC's voice, the lists of words to say differently, one retry, the shared clean-up, and the lip file (made by TTSable as today).
A service only answers what differs: how to start it, how a clip becomes a voice, and how to turn a line into sound.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from abc import abstractmethod
from typing import Any

from src import utils
from src.config.config_loader import ConfigLoader
from src.tts import audio_cleanup
from src.tts.registry import VoiceServiceInfo
from src.tts.synthesization_options import SynthesizationOptions
from src.tts.ttsable import TTSable
from src.tts.voice_folders import pronunciation_folder, voice_clips_folder
from src.tts.voice_picker import NAMED, VoicePick, VoicePicker, VoiceSpeaker, file_value
from src.tts.word_lists import shared_word_lists
from src.voice_clips.clip_store import ClipStore, shared_clip_store
from src.voice_clips.preparing import preparation_for
from src.voice_clips.requests import clip_requests_for

TRIES = 2 # one retry, then the line goes to the backup service
_warned_languages: set[tuple[str, str]] = set()


def clip_hash(clip_path: str) -> str:
    """Names the voice made from a clip: the same sound gives the same name, whichever voice it is kept under"""
    with open(clip_path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()[:16]


class VoiceServiceFailure(Exception):
    pass


class ClipVoiceService(TTSable):
    request_timeout: float = 30.0 # seconds to wait for one line

    def __init__(self, config: ConfigLoader, info: VoiceServiceInfo, clips: ClipStore | None = None) -> None:
        super().__init__(config)
        self._info = info
        self._clips = clips or shared_clip_store(voice_clips_folder(config.save_folder))
        self._clip_requests = clip_requests_for(self._clips)
        self._words = shared_word_lists(pronunciation_folder(config.save_folder))
        self._picker = VoicePicker(self._clips, self.built_in_voice, self.has_voice)
        self._pick: VoicePick | None = None
        self._prepared: dict[VoicePick, tuple[tuple, Any]] = {} # by voice: (the clip's size and time, the prepared voice)
        self._prepare_lock = threading.Lock() # guards _prepared and _making, never held while a voice is made
        self._making: dict[str, threading.Lock] = {} # one per voice, held while it is made
        self._preparation = preparation_for(self._clips, self._clip_requests)
        if not info.speaks(config.language) and (info.name, config.language) not in _warned_languages:
            _warned_languages.add((info.name, config.language)) # once, not on every settings change
            utils.play_error_sound()
            logging.warning(f"{info.name} is set up for {', '.join(info.languages or [])}, not for your Language setting ({config.language}). "
                            f"Its lines may sound wrong. Change the setup's Languages on the Text-to-Speech page if it does speak {config.language}.")

    @property
    def info(self) -> VoiceServiceInfo:
        return self._info

    # ---- what each service answers ----

    @abstractmethod
    def built_in_voice(self, gender: int | None) -> str:
        """The service's own voice for when no clip fits (gender 1 is female)"""

    def has_voice(self, name: str) -> bool:
        """Whether the service itself has a voice of this name (for a voice named in the character file)"""
        return False

    def is_ready(self) -> bool:
        return True

    def start(self) -> None:
        """Starts the voice program, for a service Mantella runs itself"""

    def prepare_voice(self, pick: VoicePick) -> Any:
        """Turns a clip into whatever the service speaks with (a sound file, a saved voice, an online voice). Done once per voice"""
        return pick

    def voice_ready(self, pick: VoicePick) -> bool:
        """Whether the voice is made, so a line in it needn't wait. A service that keeps its voices on disk looks there"""
        with self._prepare_lock:
            kept = self._prepared.get(pick)
        return kept is not None and kept[0] == self._clip_version(pick)

    def serve_in_game(self) -> None:
        """This service speaks in game (not only for a Test button): Prepare all voices makes its voices"""
        self._preparation.add_service(self)

    def stop(self) -> None:
        self._preparation.remove_service(self)

    @abstractmethod
    def speak(self, text: str, voice: Any, out_path: str) -> None:
        """Turns one line into sound, in any sound format, written to *out_path*. Raises on failure"""

    def format_ipa(self, word: str, ipa: str) -> str:
        """How sound symbols are written into a line, for a service that takes them"""
        return ipa

    # ---- shared ----

    def pick_voice(self, speaker: VoiceSpeaker | None, named_voice: str | None = None) -> VoicePick:
        return self._picker.pick(speaker, named_voice)

    @staticmethod
    def _clip_version(pick: VoicePick) -> tuple:
        """The clip file's size and time: a clip replaced under the same name gives a new voice"""
        if not pick.clip_path:
            return ()
        try:
            stat = os.stat(pick.clip_path)
            return (stat.st_size, stat.st_mtime_ns)
        except OSError:
            return ()

    def __kept_voice(self, pick: VoicePick, version: tuple) -> tuple[bool, Any]:
        with self._prepare_lock:
            kept = self._prepared.get(pick)
        return (True, kept[1]) if kept is not None and kept[0] == version else (False, None)

    def prepared_voice(self, pick: VoicePick) -> Any:
        """The voice, made the first time. Making one voice (seconds, for Pocket TTS) holds up only lines in that same voice:
        a voice made by Prepare all voices never makes the lines of a conversation wait"""
        version = self._clip_version(pick)
        found, voice = self.__kept_voice(pick, version)
        if found and self.voice_ready(pick): # a service that keeps its voices on disk: the file may have been removed since
            return voice
        with self._prepare_lock:
            making = self._making.setdefault(pick.key, threading.Lock())
        with making:
            found, voice = self.__kept_voice(pick, version) # made meanwhile, by the line or Prepare all voices
            if found and self.voice_ready(pick):
                return voice
            voice = self.prepare_voice(pick)
            with self._prepare_lock:
                self._prepared[pick] = (version, voice)
            return voice

    def forget_prepared_voices(self) -> None:
        """After a clip is swapped, the old voice is thrown away and the next line uses the new clip"""
        with self._prepare_lock:
            self._prepared.clear()

    def synthesize(self, voice: str, voiceline: str, in_game_voice: str, csv_in_game_voice: str, voice_accent: str, synth_options: SynthesizationOptions,
                   advanced_voice_model: str | None = None, speaker: VoiceSpeaker | None = None):
        if speaker is None: # narration, or a caller that only knows the voice names
            speaker = VoiceSpeaker(voice_type=in_game_voice or csv_in_game_voice, file_voice_type=file_value(csv_in_game_voice))
        self._clip_requests.collect() # clips the game delivered since the last line
        self._pick = self.pick_voice(speaker, advanced_voice_model)
        if self._pick.kind != NAMED:
            # No clip yet: asked for now, and this line uses a stand-in. Also notices a changed game line, once per run
            named = file_value(advanced_voice_model)
            if named and not self.has_voice(named):
                self._clip_requests.ask_for(named) # a voice type named for them in the character file, with no clip yet
            self._clip_requests.ask_for(speaker.voice_type, speaker.plugin, speaker.local_id, speaker.name, stand_ins=speaker.stand_ins())
        self._last_voice = self._pick.label # the voice is chosen here, so TTSable doesn't change it again
        logging.debug(f"{self._info.name}: {speaker.name or 'narrator'} speaks with {self._pick.kind} '{self._pick.label}'")
        return super().synthesize(self._pick.label, voiceline, in_game_voice, csv_in_game_voice, voice_accent, synth_options, advanced_voice_model)

    def change_voice(self, voice: str, in_game_voice: str | None = None, csv_in_game_voice: str | None = None, advanced_voice_model: str | None = None,
                     voice_accent: str | None = None, voice_gender: int | None = None, voice_race: str | None = None):
        """Getting a voice ready ahead of an NPC's first line"""
        pick = self.pick_voice(VoiceSpeaker(gender=voice_gender, race=voice_race, voice_type=in_game_voice or csv_in_game_voice), advanced_voice_model)
        try:
            self.prepared_voice(pick)
        except Exception as e:
            logging.warning(f"{self._info.name}: could not get the voice '{pick.label}' ready: {e}")

    def spoken_text(self, text: str) -> str:
        """The line as the service should say it, after the lists of words to say differently"""
        return self._words.apply(text, self._info.matches, self._language, self._info.accepts_ipa, self.format_ipa)

    def tts_synthesize(self, voiceline: str, final_voiceline_file: str, synth_options: SynthesizationOptions):
        self.voice_into(voiceline, self._pick or self.pick_voice(None), final_voiceline_file)

    def voice_into(self, voiceline: str, pick: VoicePick, final_voiceline_file: str, use_word_lists: bool = True) -> None:
        """One line, in one voice, into a cleaned-up sound file. Also used by the Test buttons, which don't make lip files"""
        text = (self.spoken_text(voiceline) if use_word_lists else voiceline).strip()
        raw_file = final_voiceline_file + ".service"
        last_error: Exception | None = None
        for attempt in range(TRIES):
            try:
                if not self.is_ready():
                    self.start()
                self.speak(text, self.prepared_voice(pick), raw_file)
                audio_cleanup.clean_up(raw_file, final_voiceline_file)
                last_error = None
                break
            except Exception as e:
                last_error = e
                logging.warning(f"{self._info.name} could not voice the line (try {attempt + 1} of {TRIES}): {e}")
            finally:
                if os.path.exists(raw_file):
                    try:
                        os.remove(raw_file)
                    except OSError:
                        pass
        if last_error is not None:
            raise VoiceServiceFailure(f"{self._info.name} failed: {last_error}") from last_error
