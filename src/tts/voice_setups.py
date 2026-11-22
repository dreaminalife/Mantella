"""The player's own voice setups: any voice program or website that follows a common request style, added with no code.

Setups are saved in voice_setups.json next to config.ini, so the file can be copied to another PC.
Ready-made setups for the programs we know ship in data/voice_setups_ready_made.json; picking one copies it into the player's setups.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from src import utils
from src.tts.registry import CLIP, VoiceServiceInfo, normalize_service_name, voice_services
from src.tts.voice_folders import voice_setups_file
from src.tts.voice_style import SPEED_DEFAULT, SPEED_HIGH, SPEED_LOW
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VOICES)

OPENAI = "openai"
XTTS = "xtts"
STYLES = {OPENAI: "OpenAI-style", XTTS: "XTTS-style"}

CLIP_PATH = "path"     # the clip's place on this PC
CLIP_BASE64 = "base64" # the clip itself, with every line
CLIP_NAME = "name"     # the voice type's name; the program has its own copy of the clip
CLIP_MODES = {
    CLIP_PATH: "The clip's place on this PC",
    CLIP_BASE64: "The clip itself, sent with every line",
    CLIP_NAME: "The voice type's name (the program keeps its own clips)",
}

FIELDS = ["name", "style", "url", "key_file", "model", "male_voice", "female_voice", "clip_field", "clip_mode",
          "languages", "extra_fields", "runs_online", "volume", "speed"]
MIN_VOLUME, MAX_VOLUME = 10, 300


def empty_setup() -> dict[str, Any]:
    return {"name": "", "style": OPENAI, "url": "http://127.0.0.1:8080", "key_file": "", "model": "", "male_voice": "", "female_voice": "",
            "clip_field": "", "clip_mode": CLIP_BASE64, "languages": [], "extra_fields": {}, "runs_online": False, "volume": 100,
            "speed": SPEED_DEFAULT}


def normalized(setup: dict[str, Any]) -> dict[str, Any]:
    """A setup with every field present and of the right kind"""
    result = empty_setup()
    for field in FIELDS:
        if field in setup and setup[field] is not None:
            result[field] = setup[field]
    result["name"] = str(result["name"]).strip()
    result["style"] = str(result["style"]).strip().lower()
    result["url"] = str(result["url"]).strip().rstrip("/")
    for field in ("key_file", "model", "male_voice", "female_voice", "clip_field", "clip_mode"):
        result[field] = str(result[field] or "").strip()
    languages = result["languages"]
    if isinstance(languages, str):
        languages = [part for part in (p.strip().lower() for p in languages.split(",")) if part]
    result["languages"] = [str(l).lower() for l in languages]
    if isinstance(result["extra_fields"], str):
        text = result["extra_fields"].strip()
        result["extra_fields"] = json.loads(text) if text else {}
    result["runs_online"] = bool(result["runs_online"])
    try:
        result["volume"] = int(min(MAX_VOLUME, max(MIN_VOLUME, round(float(result["volume"])))))
    except (TypeError, ValueError):
        result["volume"] = 100
    try:
        result["speed"] = round(min(SPEED_HIGH, max(SPEED_LOW, float(result["speed"]))), 2)
    except (TypeError, ValueError):
        result["speed"] = SPEED_DEFAULT
    return result


def problems(setup: dict[str, Any], other_names: list[str]) -> list[str]:
    """What is wrong with a setup, in plain words. Empty when it can be saved"""
    found: list[str] = []
    name = str(setup.get("name", "")).strip()
    if not name:
        found.append("Give the setup a name. It is shown in the TTS Service list and used in the tts_service column.")
    elif normalize_service_name(name) in {normalize_service_name(n) for n in other_names}:
        found.append(f"The name '{name}' is already taken by another voice service or setup.")
    style = str(setup.get("style", "")).strip().lower()
    if style not in STYLES:
        found.append("Pick a request style: OpenAI-style or XTTS-style.")
    url = str(setup.get("url", "")).strip()
    if not url:
        found.append("Fill in the address, for example http://127.0.0.1:8080.")
    elif not url.lower().startswith(("http://", "https://")):
        found.append("The address must start with http:// or https://.")
    clip_mode = str(setup.get("clip_mode", "")).strip()
    if clip_mode and clip_mode not in CLIP_MODES:
        found.append("Pick how the clip is handed over.")
    if style == OPENAI and clip_mode and not str(setup.get("clip_field", "")).strip():
        found.append("Fill in the name of the box that carries the clip, for example speaker_ref_b64.")
    if style == XTTS and clip_mode == CLIP_BASE64:
        found.append("XTTS-style programs can't take the clip itself: pick the clip's place on this PC or the voice type's name.")
    try:
        volume = float(setup.get("volume", 100) if setup.get("volume", 100) not in (None, "") else 100)
        if not MIN_VOLUME <= volume <= MAX_VOLUME:
            found.append(f"Volume must be from {MIN_VOLUME}% to {MAX_VOLUME}%.")
    except (TypeError, ValueError):
        found.append(f"Volume must be a number from {MIN_VOLUME} to {MAX_VOLUME}.")
    try:
        speed = float(setup.get("speed", SPEED_DEFAULT) if setup.get("speed", SPEED_DEFAULT) not in (None, "") else SPEED_DEFAULT)
        if not SPEED_LOW <= speed <= SPEED_HIGH:
            found.append(f"Talking speed must be from {SPEED_LOW:g} to {SPEED_HIGH:g}.")
    except (TypeError, ValueError):
        found.append(f"Talking speed must be a number from {SPEED_LOW:g} to {SPEED_HIGH:g}.")
    extra = setup.get("extra_fields", {})
    if isinstance(extra, str) and extra.strip():
        try:
            extra = json.loads(extra)
        except json.JSONDecodeError as e:
            found.append(f"Extra settings must be written as JSON, for example {{\"seed\": 42}}: {e.msg}.")
            extra = {}
    if not isinstance(extra, (dict, str)):
        found.append("Extra settings must be written as JSON, for example {\"seed\": 42}.")
    return found


def key_file_places(key_file: str) -> list[Path]:
    """Where a key file is looked for, in order: the mod folder first, then Mantella's own folder, then the folder Mantella runs in"""
    if os.path.isabs(key_file):
        return [Path(key_file)]
    return [Path(utils.resolve_path()).parent.parent.parent / key_file, Path(utils.resolve_path()) / key_file, Path(key_file)]


_key_saves: dict[str, int] = {} # by key file: how often Save key saved it this run


def key_saves(key_file: str) -> int:
    """How often a key was saved in *key_file* this run. A running service that read the key before compares it to see a new key"""
    return _key_saves.get(key_file, 0)


def save_key_file(key_file: str, key: str) -> Path:
    """Saves a key where read_key_file finds it first: over the file already there, else next to GPT_SECRET_KEY.txt, else in Mantella's own folder"""
    places = key_file_places(key_file)
    target = next((p for p in places if p.is_file()), None)
    if target is None:
        gpt_key = next((p for p in key_file_places("GPT_SECRET_KEY.txt") if p.is_file()), None)
        target = gpt_key.with_name(key_file) if gpt_key is not None else Path(utils.resolve_path()) / key_file
    target.write_text(key.strip() + "\n", encoding="utf-8")
    _key_saves[key_file] = key_saves(key_file) + 1
    return target


def read_key_file(key_file: str) -> str:
    """Reads a key file the way the AI model's GPT_SECRET_KEY.txt is read: the mod folder first, then Mantella's own folder"""
    if not key_file:
        return ""
    for place in key_file_places(key_file):
        try:
            if place.is_file():
                return place.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    log.warning(f"Could not find the key file '{key_file}'. Put it in Mantella's folder.")
    return ""


def make_service(config, game, info: VoiceServiceInfo):
    style = (info.setup or {}).get("style")
    if style == XTTS:
        from src.tts.xtts_style import XTTSStyleService
        return XTTSStyleService(config, info)
    from src.tts.openai_style import OpenAIStyleService
    return OpenAIStyleService(config, info)


def to_service_info(setup: dict[str, Any]) -> VoiceServiceInfo:
    return VoiceServiceInfo(
        name=setup["name"], factory=make_service, kind=CLIP, runs_online=setup["runs_online"],
        languages=setup["languages"] or None,
        is_setup=True, setup=copy.deepcopy(setup),
    )


class VoiceSetups:
    """The player's setups, kept in one file and listed in the voice services list"""
    def __init__(self, save_folder: str) -> None:
        self.__file = voice_setups_file(save_folder)
        self.__read_time: float | None = None
        self.__setups: list[dict[str, Any]] = []
        self.reload_if_changed()

    @property
    def file(self) -> str:
        return self.__file

    def __modified_time(self) -> float | None:
        try:
            return os.path.getmtime(self.__file)
        except OSError:
            return None

    def reload_if_changed(self) -> None:
        """Reads the file again when it was changed by hand"""
        modified = self.__modified_time()
        if modified != self.__read_time:
            self.__setups = self.__read()
            self.__read_time = modified

    def __read(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.__file):
            return []
        try:
            with open(self.__file, encoding="utf-8") as f:
                data = json.load(f)
            result = []
            for raw in data.get("setups", []):
                try:
                    result.append(normalized(raw))
                except (ValueError, TypeError, json.JSONDecodeError) as e:
                    log.warning(f"A voice setup in {self.__file} could not be read and is left out: {e}")
            return result
        except (OSError, json.JSONDecodeError) as e:
            utils.play_error_sound()
            log.error(f"Could not read the voice setups in {self.__file}: {e}")
            return []

    def __write(self) -> None:
        os.makedirs(os.path.dirname(self.__file), exist_ok=True)
        with open(self.__file, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"setups": self.__setups}, f, indent=2)
        self.__read_time = self.__modified_time()

    def names(self) -> list[str]:
        return [s["name"] for s in self.__setups]

    def get(self, name: str) -> dict[str, Any] | None:
        for setup in self.__setups:
            if normalize_service_name(setup["name"]) == normalize_service_name(name):
                return copy.deepcopy(setup)
        return None

    def publish(self) -> None:
        """Lists the setups in the voice services list"""
        voice_services.set_setups([to_service_info(s) for s in self.__setups])

    def save(self, setup: dict[str, Any], old_name: str | None = None) -> tuple[bool, str]:
        """Adds a setup, or replaces *old_name* with it"""
        others = [s.name for s in voice_services.all() if not (old_name and normalize_service_name(s.name) == normalize_service_name(old_name))]
        found = problems(setup, others)
        if found:
            return False, " ".join(found)
        clean = normalized(setup)
        replaced = False
        for i, existing in enumerate(self.__setups):
            if old_name and normalize_service_name(existing["name"]) == normalize_service_name(old_name):
                self.__setups[i] = clean
                replaced = True
                break
        if not replaced:
            self.__setups.append(clean)
        self.__write()
        self.publish()
        return True, f"Saved the voice setup '{clean['name']}'."

    def delete(self, name: str, in_use_by: list[str]) -> tuple[bool, str]:
        if in_use_by:
            return False, f"'{name}' is chosen as the {' and the '.join(in_use_by)}. Pick another service there first."
        before = len(self.__setups)
        self.__setups = [s for s in self.__setups if normalize_service_name(s["name"]) != normalize_service_name(name)]
        if len(self.__setups) == before:
            return False, f"There is no voice setup called '{name}'."
        self.__write()
        self.publish()
        return True, f"Removed the voice setup '{name}'."


def ready_made_setups() -> list[dict[str, Any]]:
    """The ready-made setups, each with an 'about' line saying what it is for"""
    path = os.path.join(utils.resolve_path(), "data", "voice_setups_ready_made.json")
    try:
        with open(path, encoding="utf-8") as f:
            return [{**normalized(s), "about": str(s.get("about", ""))} for s in json.load(f).get("setups", [])]
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
        log.warning(f"Could not read the ready-made voice setups in {path}: {e}")
        return []


_setups: dict[str, VoiceSetups] = {}


def voice_setups_for(save_folder: str) -> VoiceSetups:
    """The setups next to this config.ini, listed in the voice services list"""
    if save_folder not in _setups:
        _setups[save_folder] = VoiceSetups(save_folder)
    _setups[save_folder].reload_if_changed()
    _setups[save_folder].publish()
    return _setups[save_folder]
