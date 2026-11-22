"""The one list of voice services Mantella knows.

The TTS Service setting, the Backup Voice Service setting, the per-NPC tts_service column and changing settings mid-game all read this list.
Piper, XTTS and xVASynth are listed with their own classes, unchanged. New services and the player's own voice setups are added here.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from src.config.definitions.tts_definitions import TTSEnum
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VOICES)

if TYPE_CHECKING:
    from src.config.config_loader import ConfigLoader
    from src.games.gameable import Gameable
    from src.tts.ttsable import TTSable
    from src.tts.voice_tags import VoiceTags

CLIP = "clip"       # copies a voice from a clip taken from the game
TRAINED = "trained" # has trained voices named after Skyrim's voice types

FALLBACK_SERVICE = TTSEnum.PIPER.display_name


def normalize_service_name(value: str | None) -> str:
    """How names are compared: case, spaces, dashes, underscores and brackets don't count"""
    if value is None:
        return ""
    text = str(value).strip().lower()
    if text in ("nan", "none", "null"):
        return ""
    for character in " -_()":
        text = text.replace(character, "")
    return text


@dataclass
class VoiceServiceInfo:
    """What Mantella needs to know about a voice service to list it and start it"""
    name: str
    factory: Callable[["ConfigLoader", "Gameable | None", "VoiceServiceInfo"], "TTSable"]
    kind: str = CLIP
    runs_online: bool = False
    aliases: list[str] = field(default_factory=list)
    languages: list[str] | None = None # None: any language
    accepts_ipa: bool = False # takes sound symbols (/ˈdoʊvɑːkiːn/) for how a word is said
    legacy_enum: TTSEnum | None = None # Piper, XTTS and xVASynth: the TTS Service value they had before this list
    is_setup: bool = False # one of the player's own voice setups
    setup: dict | None = None # the setup's boxes, for a voice setup
    volume_setting: str = "" # the config.ini setting with this service's Volume (a voice setup keeps its own in the setup)
    clip_seconds: float = 0.0 # how much of a voice's chosen clips the service clones from, from the top. 0: the top clip only
    fill_seconds: float = 0.0 # a voice shorter than this gets more lines from the game, even one whose clips the player chose
    clip_lines: int = 4 # the lines a new voice gets from the game when this service speaks first
    # The sounds and directions it takes in square brackets with the current settings (src/tts/voice_tags.py). None: it takes none
    voice_tags: Callable[["ConfigLoader"], "VoiceTags"] | None = None
    # The model it speaks with now (Inworld's TTS-2 or TTS-2 Flash). None: it has no choice of model
    model: Callable[["ConfigLoader"], str] | None = None

    def mode_text(self, config) -> str:
        """How it speaks now, for the log: "TTS-2, audio tags, directions". Audio tags and directions only when they work with the
        current settings, as the prompt offers them. Empty for a service with no model choice and no tags"""
        parts: list[str] = []
        try:
            if self.model is not None:
                parts.append(self.model(config))
            tags = self.voice_tags(config) if self.voice_tags is not None else None
        except Exception:
            return ""
        if tags is not None and tags.sounds:
            parts.append("audio tags")
        if tags is not None and tags.directions:
            parts.append("directions")
        return ", ".join(part for part in parts if part)

    @property
    def uses_clips(self) -> bool:
        """Clones voices from game clips: Pocket TTS, the online services and voice setups. Piper, XTTS and xVASynth don't"""
        return self.kind == CLIP and not self.is_legacy

    @property
    def is_legacy(self) -> bool:
        return self.legacy_enum is not None

    def speaks(self, language: str | None) -> bool:
        """Whether the service is set up for this language code ('zh' also matches 'zh-cn'). True when no languages are listed"""
        if not self.languages or not language:
            return True
        wanted = language.lower()
        return any(l.lower() == wanted or l.lower().split("-")[0] == wanted.split("-")[0] for l in self.languages)

    def matches(self, value: str) -> bool:
        wanted = normalize_service_name(value)
        return bool(wanted) and wanted in {normalize_service_name(n) for n in [self.name, *self.aliases]}


def _piper(config, game, info):
    from src.tts.piper import Piper
    if game is None:
        raise RuntimeError("Piper needs the game")
    return Piper(config, game)


def _xtts(config, game, info):
    from src.tts.xtts import XTTS
    if game is None:
        raise RuntimeError("XTTS needs the game")
    return XTTS(config, game)


def _xvasynth(config, game, info):
    from src.tts.xvasynth import xVASynth
    return xVASynth(config)


def _pocket_tts(config, game, info):
    from src.tts.pocket_tts import PocketTTS
    return PocketTTS(config, info)


def _elevenlabs(config, game, info):
    from src.tts.elevenlabs import ElevenLabs
    return ElevenLabs(config, info)


def _inworld(config, game, info):
    from src.tts.inworld import Inworld
    return Inworld(config, info)


def _inworld_voice_tags(config):
    from src.tts.inworld import voice_tags
    return voice_tags(config)


def _model_name(service: str) -> Callable[["ConfigLoader"], str]:
    """The name of the model an online service speaks with now (model_catalog.py), for the log"""
    def model_name(config) -> str:
        from src.tts.model_catalog import ServiceModels
        models = ServiceModels.of(service)
        return models.picked(config).name if models is not None else ""
    return model_name


def _heygen(config, game, info):
    from src.tts.heygen import HeyGen
    return HeyGen(config, info)


def _heygen_voice_tags(config):
    from src.tts.heygen import voice_tags
    return voice_tags(config)


def _qwen(config, game, info):
    from src.tts.qwen import Qwen
    return Qwen(config, info)


def _qwen_voice_tags(config):
    from src.tts.qwen import voice_tags
    return voice_tags(config)


def _boson(config, game, info):
    from src.tts.boson import BosonAI
    return BosonAI(config, info)


def _boson_voice_tags(config):
    from src.tts.boson import voice_tags
    return voice_tags(config)


def _iodine(config, game, info):
    from src.tts.iodine import Iodine
    return Iodine(config, info)


def _iodine_voice_tags(config):
    from src.tts.iodine import voice_tags
    return voice_tags(config)


HEYGEN_LANGUAGES = ["ar", "be", "bg", "ca", "cs", "da", "de", "el", "en", "es", "fa", "fi", "fil", "fr", "he", "hi", "hr", "hu", "id", "it", "ja",
                    "ko", "mk", "ms", "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sr", "sv", "ta", "th", "tl", "tr", "uk", "vi", "zh"]
QWEN_LANGUAGES = ["zh", "en", "ja", "ko", "ru", "fr", "de", "pt", "th", "id", "vi", "es", "it", "ms", "fil", "ar"]
# Every one of Mantella's languages but Hausa and Yoruba
IODINE_LANGUAGES = ["en", "zh", "es", "hi", "ar", "fr", "pt", "bn", "ru", "ja", "de", "ko", "ur", "id", "tr", "vi", "it", "ta", "te", "pa", "th",
                    "tl", "fa", "pl", "uk", "nl", "ms", "ro", "sw", "el", "cs", "hu", "sv", "he", "da", "fi", "no", "sk", "bg", "hr"]


def _legacy_services() -> list[VoiceServiceInfo]:
    return [
        VoiceServiceInfo(TTSEnum.PIPER.display_name, _piper, TRAINED, aliases=["piper"], legacy_enum=TTSEnum.PIPER),
        VoiceServiceInfo(TTSEnum.XTTS.display_name, _xtts, CLIP, aliases=["xtts"], legacy_enum=TTSEnum.XTTS),
        VoiceServiceInfo(TTSEnum.XVASYNTH.display_name, _xvasynth, TRAINED, aliases=["xvasynth", "xva"], legacy_enum=TTSEnum.XVASYNTH),
    ]


def _new_services() -> list[VoiceServiceInfo]:
    return [
        VoiceServiceInfo("Pocket TTS", _pocket_tts, CLIP, aliases=["pockettts", "pocket"], languages=["en", "fr", "de", "it", "pt", "es", "nl"],
                         volume_setting="pocket_tts_volume"),
        # The online services' models and prices are on Mantella's list of models (model_catalog.py).
        # ElevenLabs works best with 1-2 minutes of speech; Inworld with 10-30 seconds, and cuts the rest
        VoiceServiceInfo("ElevenLabs", _elevenlabs, CLIP, runs_online=True, aliases=["elevenlabs", "11labs", "eleven"],
                         volume_setting="elevenlabs_volume", clip_seconds=120.0, fill_seconds=60.0, clip_lines=10, model=_model_name("ElevenLabs")),
        VoiceServiceInfo("Inworld", _inworld, CLIP, runs_online=True, aliases=["inworld"], accepts_ipa=True,
                         volume_setting="inworld_volume", clip_seconds=29.5, fill_seconds=25.0, voice_tags=_inworld_voice_tags,
                         model=_model_name("Inworld")),
        # HeyGen clones from one recording (about a minute works best); Qwen, Boson AI and Iodine use at most 30 seconds of it
        VoiceServiceInfo("HeyGen", _heygen, CLIP, runs_online=True, aliases=["heygen"], languages=HEYGEN_LANGUAGES, accepts_ipa=True,
                         volume_setting="heygen_volume", clip_seconds=60.0, fill_seconds=45.0, clip_lines=10, voice_tags=_heygen_voice_tags,
                         model=_model_name("HeyGen")),
        VoiceServiceInfo("Qwen", _qwen, CLIP, runs_online=True, aliases=["qwen", "alibaba"], languages=QWEN_LANGUAGES,
                         volume_setting="qwen_volume", clip_seconds=29.5, fill_seconds=20.0, voice_tags=_qwen_voice_tags, model=_model_name("Qwen")),
        VoiceServiceInfo("Boson AI", _boson, CLIP, runs_online=True, aliases=["boson", "higgs"],
                         volume_setting="boson_volume", clip_seconds=29.5, fill_seconds=20.0, voice_tags=_boson_voice_tags,
                         model=_model_name("Boson AI")),
        VoiceServiceInfo("Iodine", _iodine, CLIP, runs_online=True, aliases=["iodine"], languages=IODINE_LANGUAGES,
                         volume_setting="iodine_volume", clip_seconds=29.5, fill_seconds=20.0, voice_tags=_iodine_voice_tags,
                         model=_model_name("Iodine")),
    ]


class VoiceServiceRegistry:
    def __init__(self) -> None:
        self.__lock = threading.RLock()
        self.__built_in: list[VoiceServiceInfo] = _legacy_services() + _new_services()
        self.__setups: list[VoiceServiceInfo] = []

    def add_built_in(self, info: VoiceServiceInfo) -> None:
        """A new service that ships with Mantella (Pocket TTS, ElevenLabs, ...)"""
        with self.__lock:
            self.__built_in = [s for s in self.__built_in if s.name != info.name] + [info]

    def set_setups(self, setups: list[VoiceServiceInfo]) -> None:
        with self.__lock:
            taken = {normalize_service_name(n) for s in self.__built_in for n in [s.name, *s.aliases]}
            kept: list[VoiceServiceInfo] = []
            for setup in setups:
                if normalize_service_name(setup.name) in taken:
                    log.warning(f"The voice setup '{setup.name}' has the same name as another voice service and is left out. Rename it on the Text-to-Speech page.")
                    continue
                taken.add(normalize_service_name(setup.name))
                kept.append(setup)
            self.__setups = kept

    def all(self) -> list[VoiceServiceInfo]:
        with self.__lock:
            return [*self.__built_in, *self.__setups]

    def names(self) -> list[str]:
        return [s.name for s in self.all()]

    def find(self, value: str | None) -> VoiceServiceInfo | None:
        if not normalize_service_name(value):
            return None
        for service in self.all():
            if service.matches(str(value)):
                return service
        return None

    def create(self, info: VoiceServiceInfo, config: "ConfigLoader", game: "Gameable | None") -> "TTSable":
        return info.factory(config, game, info)


voice_services = VoiceServiceRegistry()


def create_voice_service(name: str, config: "ConfigLoader", game: "Gameable | None") -> "TTSable":
    """Starts the voice service of that name. Piper when the name is not in the list (a voice setup that was removed)"""
    info = voice_services.find(name)
    if info is None:
        log.warning(f"The voice service '{name}' is not in the list of voice services. {FALLBACK_SERVICE} is used instead.")
        info = voice_services.find(FALLBACK_SERVICE)
    return serving_in_game(voice_services.create(info, config, game))


def serving_in_game(service: "TTSable") -> "TTSable":
    """A service that will speak in game. Prepare all voices makes the voices of one that copies voices"""
    serve = getattr(service, "serve_in_game", None)
    if serve is not None:
        serve()
    return service
