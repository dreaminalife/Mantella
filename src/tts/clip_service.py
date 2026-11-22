"""The base of every new voice service (Pocket TTS, the player's voice setups, online services).

Everything that is the same for all of them lives here, written once:
choosing the NPC's voice, the lists of words to say differently, one retry, the shared clean-up, and the lip file (made by TTSable as today).
A service only answers what differs: how to start it, how a clip becomes a voice, and how to turn a line into sound.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import threading
import time
from abc import abstractmethod
from typing import Any, Callable

from src import utils
from src.config.config_loader import ConfigLoader
from src.tts import audio_cleanup, voice_effects
from src.tts.registry import VoiceServiceInfo, voice_services
from src.tts.synthesization_options import SynthesizationOptions
from src.tts.ttsable import TTSable
from src.tts.voice_folders import pronunciation_folder, voice_clips_folder
from src.tts.voice_picker import NAMED, OWN_CLIP, STAND_INS, VoicePick, VoicePicker, VoiceSpeaker, file_value
from src.tts.voice_style import ServiceStyle, style_for
from src.tts.word_lists import replace_words, shared_word_lists
from src.voice_clips.clip_store import MOST_CHOSEN, NPCS, VOICE_TYPES, ClipStore, clip_seconds, lines_within, shared_clip_store, voice_key
from src.voice_clips.preparing import preparation_for
from src.voice_clips.requests import clip_requests_for
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VOICES)
voice_clips_log = get_topic_logger(LogTopic.VOICE_CLIPS)

TRIES = 2 # one retry, then the line goes to the backup service
FIRST_LINE_WAIT_SECONDS = 10.0 # a new voice's first line waits at most this long for its clips from the game, then a stand-in speaks it
LOOK_SECONDS = 0.5 # how often the folder is looked in meanwhile, in case the game's message saying they came is held up
_warned_languages: set[tuple[str, str]] = set()


def clip_hash(clip_path: str) -> str:
    """Names the voice made from a clip: the same sound gives the same name, whichever voice it is kept under"""
    with open(clip_path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()[:16]


def clips_hash(clip_paths: list[str]) -> str:
    """Names the voice made from several clips, in order. One clip is named as clip_hash names it"""
    if len(clip_paths) == 1:
        return clip_hash(clip_paths[0])
    return hashlib.sha256("|".join(clip_hash(p) for p in clip_paths).encode("ascii")).hexdigest()[:16]


def clip_owner(pick: VoicePick) -> tuple[str, str]:
    """Where the picked clip is kept: an NPC's own voice, or a voice type's"""
    return (NPCS if pick.kind == OWN_CLIP else VOICE_TYPES), pick.key


def lines_used(clips: ClipStore, info: VoiceServiceInfo | None, kind: str, key: str) -> list[str]:
    """The clips a service clones the voice from: as many as it takes from the top of the voice's chosen clips"""
    lines = [path for path, _ in clips.voice_lines(kind, key)]
    if not lines:
        return []
    most = info.clip_seconds if info is not None else 0.0
    return lines[:lines_within([clip_seconds(p) for p in lines], most)]


class VoiceServiceFailure(Exception):
    pass


class NoRetry(RuntimeError):
    """Trying the line again wouldn't help: the service is off for a while, or didn't answer in time (the NPC would wait that long
    again). The Backup Voice Service speaks it straight away"""


class NoSoundSymbols(ValueError):
    """Hear it fixed, with sound symbols, in a voice that doesn't read them"""


def service_volume(config, info: VoiceServiceInfo) -> float:
    """A service's Volume in percent, as set on the page now: a voice setup keeps its own, the others are a config.ini setting"""
    if info.is_setup:
        try:
            return float((info.setup or {}).get("volume", 100))
        except (TypeError, ValueError):
            return 100.0
    if info.volume_setting:
        chosen = getattr(config, "chosen_voice_volume", None)
        return float(chosen(info.volume_setting)) if callable(chosen) else float(getattr(config, info.volume_setting, 100))
    return 100.0


def voice_pitch(clips: ClipStore, pick: VoicePick | None) -> float:
    """The Pitch set on Voice Clips for the voice a line is spoken in (0 for a service's built-in voice)"""
    if pick is None or not pick.clip_path:
        return 0.0
    return clips.pitch(*clip_owner(pick))


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
        self._voice_text: str | None = None # the line with its sounds and directions, for the line being spoken
        self._prepared: dict[VoicePick, tuple[tuple, Any]] = {} # by voice: (the clip's size and time, the prepared voice)
        self._prepare_lock = threading.Lock() # guards _prepared and _making, never held while a voice is made
        self._making: dict[str, threading.Lock] = {} # one per voice, held while it is made
        self._preparation = preparation_for(self._clips, self._clip_requests)
        self._effects = voice_effects.shared_voice_effects(config.save_folder)
        self._effect: voice_effects.VoiceEffect | None = None # for the line being made
        self._pitch: float = 0.0
        self._style: ServiceStyle = style_for(config, info) # Talking speed, and an online service's other settings (voice_style.py)
        self.spoke_in_game = False # a line in game, not only a button on the page
        self.stop_waiting: Callable[[], bool] | None = None # true once Mantella stops the reply (the player interrupts, the conversation ends): a line waiting for its clips stops waiting
        if not info.speaks(config.language) and (info.name, config.language) not in _warned_languages:
            _warned_languages.add((info.name, config.language)) # once, not on every settings change
            utils.play_error_sound()
            log.warning(f"{info.name} is set up for {', '.join(info.languages or [])}, not for your Language setting ({config.language}). "
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

    @property
    def clones_voices(self) -> bool:
        """False for a voice setup with no box for the clip: the game isn't asked for clips it would never use"""
        return True

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

    def speak_line(self, text: str, voice: Any, out_path: str, pick: VoicePick | None) -> None:
        """speak, for a service that also wants to know which voice the line is for (an online service's style settings)"""
        self.speak(text, voice, out_path)

    def format_ipa(self, word: str, ipa: str) -> str:
        """How sound symbols are written into a line, for a service that takes them"""
        return ipa

    def takes_ipa(self, voice: Any) -> bool:
        """Whether this voice reads sound symbols, for a service that takes them. A word without them uses its spelling"""
        return True

    def no_ipa_reason(self, voice: Any) -> str:
        """Why this voice doesn't read sound symbols, for Hear it fixed"""
        return f"{self._info.name} doesn't take sound symbols."

    # ---- shared ----

    def pick_voice(self, speaker: VoiceSpeaker | None, named_voice: str | None = None) -> VoicePick:
        return self._picker.pick(speaker, named_voice)

    def _limits(self) -> VoiceServiceInfo:
        """The service as it clones now: an online service's picked model may take more or less speech than its usual"""
        return self._info

    def clips_for(self, pick: VoicePick) -> list[str]:
        """The clips this service clones the picked voice from: the top clip, or as many chosen clips as it takes"""
        if not pick.clip_path:
            return []
        limits = self._limits()
        if not limits.clip_seconds:
            return [pick.clip_path]
        found = lines_used(self._clips, limits, *clip_owner(pick))
        if not found or os.path.abspath(found[0]) != os.path.abspath(pick.clip_path):
            return [pick.clip_path] # a clip that isn't a voice's (a test)
        return found

    def _clip_version(self, pick: VoicePick) -> tuple:
        """The clip files' sizes and times: a clip replaced under the same name, or another clip chosen, gives a new voice"""
        version = []
        for path in self.clips_for(pick):
            try:
                stat = os.stat(path)
                version.append((path, stat.st_size, stat.st_mtime_ns))
            except OSError:
                return ()
        return tuple(version)

    def _top_up(self, pick: VoicePick) -> None:
        """A voice shorter than this service wants gets more lines from the game, added after its chosen clips. A voice whose clips
        the player chose too: their clips stay as they are, and the lines they took out aren't brought back"""
        limits = self._limits()
        if not limits.fill_seconds or not pick.clip_path:
            return
        kind, key = clip_owner(pick)
        lines = self._clips.voice_lines(kind, key)
        if not lines or os.path.abspath(lines[0][0]) != os.path.abspath(pick.clip_path):
            return
        used = lines_used(self._clips, limits, kind, key)
        seconds = sum(clip_seconds(p) for p in used)
        if seconds >= limits.fill_seconds or len(used) < len(lines) or len(lines) >= MOST_CHOSEN:
            return
        average = seconds / len(lines) if seconds > 0 else 7.0 # game lines are 5-10 seconds
        missing = math.ceil((limits.fill_seconds - seconds) / max(average, 1.0))
        count = max(missing, self._info.clip_lines - len(lines))
        self._clip_requests.ask_to_top_up(kind, key, max(1, min(count, MOST_CHOSEN - len(lines))))

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
            voice_clips_log.info(f"{self._info.name}: preparing the clips for '{pick.label}'")
            voice = self.prepare_voice(pick)
            if voice_clips_log.isEnabledFor(logging.INFO):
                voice_clips_log.info(f"{self._info.name}: the voice '{pick.label}' is ready{self.__clips_used_text(pick)}")
            with self._prepare_lock:
                self._prepared[pick] = (version, voice)
            return voice

    def __clips_used_text(self, pick: VoicePick) -> str:
        """", made from N clips" for the log. Empty without clips, or when they can't be counted: the voice is made already"""
        if not pick.clip_path:
            return ""
        try:
            return f", made from {len(lines_used(self._clips, self._limits(), *clip_owner(pick)))} clips"
        except Exception:
            return ""

    def forget_prepared_voices(self) -> None:
        """After a clip is swapped, the old voice is thrown away and the next line uses the new clip"""
        with self._prepare_lock:
            self._prepared.clear()

    def synthesize(self, voice: str, voiceline: str, in_game_voice: str, csv_in_game_voice: str, voice_accent: str, synth_options: SynthesizationOptions,
                   advanced_voice_model: str | None = None, speaker: VoiceSpeaker | None = None, voice_text: str | None = None):
        """voice_text: the line with its sounds and directions ([laugh], [say angrily]). Said instead of the line when this service takes them"""
        self.spoke_in_game = True
        self._voice_text = voice_text
        if speaker is None: # narration, or a caller that only knows the voice names
            speaker = VoiceSpeaker(voice_type=in_game_voice or csv_in_game_voice, file_voice_type=file_value(csv_in_game_voice))
        self._clip_requests.collect() # clips the game delivered since the last line
        self._pick = self.__wait_for_own_voice(speaker, advanced_voice_model, self.pick_voice(speaker, advanced_voice_model))
        self.__ask_for_clips(speaker, advanced_voice_model, self._pick)
        self._last_voice = self._pick.label # the voice is chosen here, so TTSable doesn't change it again
        self._effect = self._effects.choose(speaker)
        self._pitch = self.pitch_for(self._pick)
        log.debug(f"{self._info.name}: {speaker.name or 'narrator'} speaks with {self._pick.kind} '{self._pick.label}'"
                      + (f", voice effect {self._effect.name}" if self._effect else ""))
        return super().synthesize(self._pick.label, voiceline, in_game_voice, csv_in_game_voice, voice_accent, synth_options, advanced_voice_model)

    def get_ready_for(self, speaker: VoiceSpeaker, advanced_voice_model: str | None = None, make_ahead: bool = True,
                      in_background: bool = False) -> None:
        """An NPC joined the conversation: clips they have none of yet are asked for now, so the request goes with this reply to the game
        and the clips arrive while the AI writes. *make_ahead*: their voice is made as soon as the clips arrive, so their first line
        needn't wait for it. *in_background*: made at the lower priority of Prepare all voices, for an NPC who may not speak soon"""
        if not self.clones_voices:
            return
        self._clip_requests.collect()
        self.__ask_for_clips(speaker, advanced_voice_model, self.pick_voice(speaker, advanced_voice_model))
        if make_ahead:
            self._preparation.make_soon(self, speaker, advanced_voice_model, in_background)

    def __ask_for_clips(self, speaker: VoiceSpeaker, advanced_voice_model: str | None, pick: VoicePick) -> None:
        """No clip yet: asked for, and a line before it comes uses a stand-in. Also notices a changed game line, once per run, and asks for
        more lines for a voice shorter than this service wants"""
        if pick.kind != NAMED and self.clones_voices:
            named = file_value(advanced_voice_model)
            count = self._info.clip_lines
            if named and not self.has_voice(named):
                self._clip_requests.ask_for(named, count=count) # a voice type named for them in the character file, with no clip yet
            self._clip_requests.ask_for(speaker.voice_type, speaker.plugin, speaker.local_id, speaker.name, stand_ins=speaker.stand_ins(), count=count)
        self._top_up(pick)

    def __wait_for_own_voice(self, speaker: VoiceSpeaker, advanced_voice_model: str | None, pick: VoicePick) -> VoicePick:
        """A stand-in while the NPC's own clips are on their way from the game: the line waits for them, up to FIRST_LINE_WAIT_SECONDS.
        Only for clips already handed to the game (asked for when the NPC joined), and each request is waited for once, so a voice
        whose clips don't come holds up one line at most. Ends at once when Mantella stops the reply (stop_waiting)"""
        if pick.kind not in STAND_INS or not self.clones_voices or not getattr(self, "can_copy_voices", True):
            return pick
        if not (speaker.plugin and speaker.local_id):
            return pick # the game sends where an NPC comes from only with MantellaVoiceClips.dll installed: without it no clips come
        started = time.monotonic()
        waited: list[str] = []
        stopped = getattr(self, "stop_waiting", None) or (lambda: False)
        try:
            while pick.kind in STAND_INS:
                on_the_way = self._clip_requests.on_the_way(self.__better_voices(speaker, advanced_voice_model, pick))
                left = started + FIRST_LINE_WAIT_SECONDS - time.monotonic()
                if not on_the_way or left <= 0 or stopped():
                    break
                waited += [r for r in on_the_way if r not in waited]
                self._clip_requests.wait_for_delivery(min(left, LOOK_SECONDS))
                pick = self.pick_voice(speaker, advanced_voice_model)
        finally:
            self._clip_requests.waited_for(waited)
        if waited:
            outcome = (f"it uses {pick.kind} '{pick.label}'" if pick.kind not in STAND_INS
                       else "the reply was stopped" if stopped() else "a stand-in speaks it, as they didn't come in time")
            log.info(f"{self._info.name}: {speaker.name or 'the narrator'}'s line waited {time.monotonic() - started:.1f} s for their clips "
                         f"from the game, and {outcome}")
        return pick

    @staticmethod
    def __better_voices(speaker: VoiceSpeaker, advanced_voice_model: str | None, pick: VoicePick) -> list[str]:
        """The voices the game may still bring that the NPC would speak with instead of the stand-in *pick*: the one named for them,
        their voice type, then the stand-ins before *pick*"""
        names = [file_value(advanced_voice_model), speaker.voice_type or ""]
        for stand_in in speaker.stand_ins():
            if pick.clip_path and voice_key(stand_in) == pick.key:
                break
            names.append(stand_in)
        return [f"{VOICE_TYPES}/{voice_key(name)}" for name in dict.fromkeys(names) if voice_key(name)]

    @property
    def voice_in_use(self) -> str:
        return f"{self._pick.kind} '{self._pick.label}'" if self._pick else super().voice_in_use

    def _finish_line(self, wav_file: str) -> None:
        self.finish_line(wav_file, self._effect, pitch=self._pitch)

    def volume(self) -> float:
        """This service's Volume. A voice setup's as last saved, so a change is heard without starting the service again"""
        latest = voice_services.find(self._info.name)
        return service_volume(self._config, latest if latest is not None and latest.is_setup == self._info.is_setup else self._info)

    def pitch_for(self, pick: VoicePick | None) -> float:
        return voice_pitch(self._clips, pick)

    def speed_for(self, pick: VoicePick | None) -> float:
        """The Talking speed for a line in this voice: the voice's own, else the page's (a voice setup's as last saved). 1 for a
        service without one, or a model that doesn't take it"""
        style = self._style
        if self._info.is_setup:
            latest = voice_services.find(self._info.name)
            style = style_for(self._config, latest if latest is not None and latest.is_setup else self._info)
        setting = style.speed_setting()
        if setting is None or setting not in style.settings():
            return 1.0
        try:
            return float(style.voice_value(self._clips, pick, setting))
        except (TypeError, ValueError):
            return 1.0

    def finish_line(self, wav_file: str, effect: voice_effects.VoiceEffect | None = None, volume: float | None = None, pitch: float = 0.0) -> None:
        """The voice's Pitch, the voice effect, then the service's Volume, on a finished line. A line that can't be changed plays as it is"""
        try:
            voice_effects.finish_line(wav_file, effect, self.volume() if volume is None else volume, pitch)
        except Exception as e:
            log.warning(f"Could not change the line's pitch, voice effect or volume: {e}. It plays as it is.")

    def change_voice(self, voice: str, in_game_voice: str | None = None, csv_in_game_voice: str | None = None, advanced_voice_model: str | None = None,
                     voice_accent: str | None = None, voice_gender: int | None = None, voice_race: str | None = None):
        """Getting a voice ready ahead of an NPC's first line"""
        pick = self.pick_voice(VoiceSpeaker(gender=voice_gender, race=voice_race, voice_type=in_game_voice or csv_in_game_voice), advanced_voice_model)
        if pick.kind in STAND_INS and not self._clip_requests.voice_type_settled(in_game_voice or csv_in_game_voice):
            return # their own clips are on the way: their voice is made when they arrive (get_ready_for), not the stand-in's
        try:
            self.prepared_voice(pick)
        except Exception as e:
            log.warning(f"{self._info.name}: could not get the voice '{pick.label}' ready: {e}")

    def spoken_text(self, text: str, ipa: bool = True) -> str:
        """The line as the service should say it, after the lists of words to say differently. *ipa*: the voice reads sound symbols"""
        return self._words.apply(text, self._info.matches, self._language, self._info.accepts_ipa and ipa, self.format_ipa)

    def tts_synthesize(self, voiceline: str, final_voiceline_file: str, synth_options: SynthesizationOptions):
        voice_text, self._voice_text = self._voice_text, None
        if voice_text and self._info.voice_tags is not None:
            voiceline = voice_text # lip files, file names and subtitles keep the line without them
        self.voice_into(voiceline, self._pick or self.pick_voice(None), final_voiceline_file)

    def voice_into(self, voiceline: str, pick: VoicePick, final_voiceline_file: str, use_word_lists: bool = True,
                   ipa_word: tuple[str, str] | None = None) -> None:
        """One line, in one voice, into a cleaned-up sound file. Also used by the Test buttons, which don't make lip files.
        *ipa_word*: (word, sound symbols) to say that word by, for Hear it fixed. Raises NoSoundSymbols when the voice doesn't read them"""
        raw_file = final_voiceline_file + ".service"
        last_error: Exception | None = None
        for attempt in range(TRIES):
            try:
                if not self.is_ready():
                    self.start()
                voice = self.prepared_voice(pick) # a voice not cloned yet may be a built-in one, which may not read sound symbols
                ipa = self._info.accepts_ipa and self.takes_ipa(voice)
                line = voiceline
                if ipa_word is not None:
                    if not ipa:
                        raise NoSoundSymbols(self.no_ipa_reason(voice))
                    line = replace_words(line, {ipa_word[0].lower(): self.format_ipa(*ipa_word)})
                text = (self.spoken_text(line, ipa) if use_word_lists else line).strip()
                self.speak_line(text, voice, raw_file, pick)
                audio_cleanup.clean_up(raw_file, final_voiceline_file, speed=self.speed_for(pick))
                last_error = None
                break
            except NoSoundSymbols:
                raise
            except NoRetry as e:
                last_error = e
                break
            except Exception as e:
                last_error = e
                log.warning(f"{self._info.name} could not voice the line (try {attempt + 1} of {TRIES}): {e}")
            finally:
                if os.path.exists(raw_file):
                    try:
                        os.remove(raw_file)
                    except OSError:
                        pass
        if last_error is not None:
            why = str(last_error)
            raise VoiceServiceFailure(why if why.startswith(self._info.name) else f"{self._info.name} failed: {why}") from last_error
