"""The one list of voice services Mantella knows.

The TTS Service setting, the Backup Voice Service setting, the per-NPC tts_service column and changing settings mid-game all read this list.
Piper, XTTS and xVASynth are listed with their own classes, unchanged. New services and the player's own voice setups are added here.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from src.config.definitions.tts_definitions import TTSEnum

if TYPE_CHECKING:
    from src.config.config_loader import ConfigLoader
    from src.games.gameable import Gameable
    from src.tts.ttsable import TTSable

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
    price_per_million_chars: float | None = None # an online service's list price, for the cost shown on the page

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


def _legacy_services() -> list[VoiceServiceInfo]:
    return [
        VoiceServiceInfo(TTSEnum.PIPER.display_name, _piper, TRAINED, aliases=["piper"], legacy_enum=TTSEnum.PIPER),
        VoiceServiceInfo(TTSEnum.XTTS.display_name, _xtts, CLIP, aliases=["xtts"], legacy_enum=TTSEnum.XTTS),
        VoiceServiceInfo(TTSEnum.XVASYNTH.display_name, _xvasynth, TRAINED, aliases=["xvasynth", "xva"], legacy_enum=TTSEnum.XVASYNTH),
    ]


def _new_services() -> list[VoiceServiceInfo]:
    return [
        VoiceServiceInfo("Pocket TTS", _pocket_tts, CLIP, aliases=["pockettts", "pocket"], languages=["en", "fr", "de", "it", "pt", "es", "nl"]),
        VoiceServiceInfo("ElevenLabs", _elevenlabs, CLIP, runs_online=True, aliases=["elevenlabs", "11labs", "eleven"], price_per_million_chars=40.0),
        VoiceServiceInfo("Inworld", _inworld, CLIP, runs_online=True, aliases=["inworld"], accepts_ipa=True, price_per_million_chars=25.0),
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
                    logging.warning(f"The voice setup '{setup.name}' has the same name as another voice service and is left out. Rename it on the Text-to-Speech page.")
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
        logging.warning(f"The voice service '{name}' is not in the list of voice services. {FALLBACK_SERVICE} is used instead.")
        info = voice_services.find(FALLBACK_SERVICE)
    return serving_in_game(voice_services.create(info, config, game))


def serving_in_game(service: "TTSable") -> "TTSable":
    """A service that will speak in game. Prepare all voices makes the voices of one that copies voices"""
    serve = getattr(service, "serve_in_game", None)
    if serve is not None:
        serve()
    return service
