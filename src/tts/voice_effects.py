"""Voice effects and Voice Volume, for the newer voice services (Pocket TTS, voice setups, ElevenLabs, Inworld, HeyGen, Qwen, Boson AI, Iodine).

Both run on a finished line, after its lip file is made: pitch and filters don't change when the mouth moves, and an echo's tail
would only add mouth movement where nobody speaks. The effect comes first, then the line is made as loud as Skyrim's own lines,
then the Voice Volume setting, then a limiter so loud lines don't crackle.

Every effect is the same few sliders (SLIDERS) and a Strength, plus an optional "extra" chain of raw ffmpeg filters for power users.
Mantella's own effects are in data/voice_effects (replaced by updates). The player's are in voice_effects next to config.ini:
a file with the same name as one of Mantella's holds only what the player changed (laid over Mantella's), any other file is a
whole effect of their own. No effect is used until the player ticks it ("on" in their file).
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Any

import av
import numpy as np
import soundfile as sf
from scipy.signal import fftconvolve, lfilter

from src import utils

VOICE_EFFECTS_FOLDER = "voice_effects"
NO_EFFECT = ("none", "off", "no") # in the voice_effect column: this NPC gets no effect, whatever their race
GAME_LOUDNESS_DB = -19.0 # the spoken parts of Skyrim's own lines (vanilla clips measured 2026-11-17: -18.5 to -19.0 dB)
MOST_BOOST_DB = 12.0 # a very quiet line is raised at most this much, so a near-silent one doesn't turn into noise
LIMIT = 0.89 # about -1 dB: the loudest a sample may be after Voice Volume
SPEECH_WINDOW_SECONDS = 0.05
SPEECH_BELOW_LOUDEST_DB = 20.0 # windows quieter than this below the loudest are pauses, left out of the measure
PITCH_RANGE = (0.5, 2.0)
CHUNK = 4096
TRY_RATE = 24000 # an effect is tried on a moment of silence when it's read, so wrong settings show up on the page, not on every line


@dataclass(frozen=True)
class Slider:
    key: str # in the effect's file
    label: str # on the page
    info: str # what the player hears
    low: float
    high: float
    step: float


SLIDERS = (
    Slider("pitch", "Deeper or higher", "Below 0 is deeper, above 0 higher, in semitones", -12, 12, 0.5),
    Slider("echo", "Echo", "How much the voice rings", 0, 100, 1),
    Slider("space", "Space", "How big the place sounds, from a small room to a huge cave. Only heard with Echo", 0, 100, 1),
    Slider("doubled", "Doubled voice", "Sounds like two voices at once", 0, 100, 1),
    Slider("muffled", "Muffled", "As if behind a helmet or a wall", 0, 100, 1),
    Slider("thin", "Thin", "Less body, farther away", 0, 100, 1),
    Slider("growl", "Growl", "A rough, beastly rumble", 0, 100, 1),
    Slider("waver", "Waver", "An unsteady, unearthly voice", 0, 100, 1),
)
SLIDER_KEYS = tuple(s.key for s in SLIDERS)
SCALED_BY_STRENGTH = tuple(k for k in SLIDER_KEYS if k != "space") # Space is the size of the place, not how much of it you hear

REVERB_SHORTEST, REVERB_LONGEST = 0.3, 4.0 # seconds the echo takes to die away, at Space 0 and 100
REVERB_TAIL_MOST = 1.2 # seconds of echo kept after the last word: the game waits for the whole file
REVERB_FADE = 0.3

# The races offered on the page, by the name players know, and every race in the game they cover
RACE_GROUPS: dict[str, tuple[str, ...]] = {
    "Draugr": ("DraugrRace", "DraugrMagicRace"),
    "Dremora": ("DremoraRace",),
    "Falmer": ("FalmerRace",),
    "Werewolf": ("WerewolfBeastRace",),
    "Werebear": ("DLC2WerebearBeastRace",),
    "Vampire Lord": ("DLC1VampireBeastRace",),
    "Altmer (High Elf)": ("HighElfRace", "HighElfRaceVampire"),
    "Argonian": ("ArgonianRace", "ArgonianRaceVampire"),
    "Bosmer (Wood Elf)": ("WoodElfRace", "WoodElfRaceVampire"),
    "Breton": ("BretonRace", "BretonRaceVampire"),
    "Dunmer (Dark Elf)": ("DarkElfRace", "DarkElfRaceVampire"),
    "Imperial": ("ImperialRace", "ImperialRaceVampire"),
    "Khajiit": ("KhajiitRace", "KhajiitRaceVampire"),
    "Nord": ("NordRace", "NordRaceVampire"),
    "Orc": ("OrcRace", "OrcRaceVampire"),
    "Redguard": ("RedguardRace", "RedguardRaceVampire"),
}

_warned: set[str] = set()


def own_effects_folder(save_folder: str) -> str:
    return os.path.join(save_folder, VOICE_EFFECTS_FOLDER)


def built_in_effects_folder() -> str:
    return os.path.join(utils.resolve_path(), "data", VOICE_EFFECTS_FOLDER)


def _warn_once(message: str) -> None:
    if message not in _warned:
        _warned.add(message)
        logging.warning(message)


def race_key(race: str | None) -> str:
    """'[Race <DraugrRace (00000D53)>]', 'DraugrRace' or 'Draugr' -> 'draugr'. Keeps DLC1VampireBeastRace whole"""
    if not race:
        return ""
    words = re.findall(r"[A-Za-z0-9_]+", str(race))
    named = [w for w in words if w.lower().endswith("race") and w.lower() != "race"]
    word = named[0] if named else (next((w for w in words if w.lower() != "race"), ""))
    word = word.lower()
    return word[:-4] if word.endswith("race") else word


def race_names(race_ids: list[str]) -> list[str]:
    """The page's names for a list of races: a group's name when any of its races is listed, and anything else as written"""
    keys = {race_key(r) for r in race_ids}
    names = [name for name, ids in RACE_GROUPS.items() if keys & {race_key(i) for i in ids}]
    covered = {race_key(i) for name in names for i in RACE_GROUPS[name]}
    return names + [r for r in race_ids if race_key(r) not in covered]


def race_ids(names: list[str]) -> list[str]:
    """Every race a list of the page's names covers. A name that isn't a group is a race typed in, kept as it is"""
    found: list[str] = []
    for name in names or []:
        name = str(name).strip()
        for race in RACE_GROUPS.get(name, (name,) if name else ()):
            if race not in found:
                found.append(race)
    return found


def clean_sliders(values: Any, where: str = "") -> dict[str, float]:
    """The sliders of a file or the page, each kept in its range. A missing one is 0"""
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValueError(f"{where}: \"sliders\" must be a list of names and numbers, like {{\"echo\": 40, \"pitch\": -2}}")
    out: dict[str, float] = {}
    for slider in SLIDERS:
        raw = values.get(slider.key, 0)
        try:
            number = float(raw or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{where}: {slider.key} must be a number, not '{raw}'")
        out[slider.key] = min(slider.high, max(slider.low, number))
    unknown = [k for k in values if k not in SLIDER_KEYS]
    if unknown:
        raise ValueError(f"{where}: there is no slider called '{unknown[0]}'. The sliders are {', '.join(SLIDER_KEYS)}")
    return out


def clean_strength(value: Any, where: str = "") -> float:
    try:
        return min(100.0, max(0.0, float(100 if value is None else value)))
    except (TypeError, ValueError):
        raise ValueError(f"{where}: strength must be a number from 0 to 100, not '{value}'")


@dataclass
class VoiceEffect:
    name: str # the file name without .json: what the voice_effect column says
    title: str
    about: str = ""
    races: list[str] = field(default_factory=list)
    ghosts: bool = False
    strength: float = 100.0
    sliders: dict[str, float] = field(default_factory=lambda: {k: 0.0 for k in SLIDER_KEYS})
    extra: list[tuple[str, str]] = field(default_factory=list) # raw ffmpeg filters, for power users
    on: bool = False # ticked on the page: used in game
    from_mantella: bool = False # one of Mantella's, maybe with the player's changes laid over it
    changed: bool = False # one of Mantella's that the player changed
    path: str = ""

    @property
    def is_own(self) -> bool:
        """One the player made, not one of Mantella's"""
        return not self.from_mantella

    def who(self) -> str:
        """Who gets it, for the page"""
        parts = (["ghosts"] if self.ghosts else []) + race_names(self.races)
        if not parts:
            return "only NPCs picked in the Bio Editor"
        return ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]

    def scaled(self) -> dict[str, float]:
        """The sliders as heard: Strength tones down all but Space"""
        part = self.strength / 100
        return {k: (v * part if k in SCALED_BY_STRENGTH else v) for k, v in self.sliders.items()}

    def with_values(self, strength: Any, sliders: dict[str, Any]) -> "VoiceEffect":
        """The same effect with the page's Strength and sliders, saved or not"""
        return replace(self, strength=clean_strength(strength), sliders=clean_sliders(sliders))


def _settings_text(settings: Any) -> str:
    if isinstance(settings, dict):
        return ":".join(f"{k}={v}" for k, v in settings.items())
    if settings is None or settings is True:
        return ""
    return str(settings)


def parse_steps(effects: Any, where: str) -> list[tuple[str, str]]:
    """The "extra" list: raw ffmpeg filters, like [{"pitch": 0.8}, {"lowpass": {"f": 3000}}, {"aecho": "0.8:0.7:40:0.3"}]"""
    if effects is None:
        return []
    if not isinstance(effects, list):
        raise ValueError(f"{where}: \"extra\" must be a list, like [{{\"lowpass\": {{\"f\": 3000}}}}]")
    known = set(av.filter.filters_available)
    steps: list[tuple[str, str]] = []
    for i, step in enumerate(effects, start=1):
        if not isinstance(step, dict) or len(step) != 1:
            raise ValueError(f"{where}: effect {i} must be one name with its settings, like {{\"lowpass\": {{\"f\": 3000}}}}")
        [(name, settings)] = step.items()
        name = str(name).strip()
        if name == "pitch":
            amount = float(settings)
            if not PITCH_RANGE[0] <= amount <= PITCH_RANGE[1]:
                raise ValueError(f"{where}: pitch must be between {PITCH_RANGE[0]} (an octave lower) and {PITCH_RANGE[1]} (an octave higher)")
            steps.append(("pitch", str(amount)))
        elif name in known:
            steps.append((name, _settings_text(settings)))
        else:
            raise ValueError(f"{where}: there is no sound effect called '{name}' (effect {i}). The names are ffmpeg's audio filters, such as aecho, lowpass or chorus")
        try:
            run_filters(np.zeros(TRY_RATE // 10, dtype=np.float32), TRY_RATE, [steps[-1]])
        except Exception as e:
            raise ValueError(f"{where}: the settings of effect {i} ({name}) don't work: {e}. "
                             "Check the setting names in ffmpeg's description of that filter") from e
    return steps


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: must hold one effect, between {{ and }}")
    return data


def merged(mantellas: dict | None, players: dict | None) -> dict:
    """The player's changes laid over Mantella's effect: sliders one by one, everything else whole"""
    data = copy.deepcopy(mantellas or {})
    for key, value in (players or {}).items():
        if key == "sliders" and isinstance(value, dict) and isinstance(data.get("sliders"), dict):
            data["sliders"] = {**data["sliders"], **value}
        else:
            data[key] = copy.deepcopy(value)
    return data


def effect_from(name: str, data: dict, where: str, from_mantella: bool, changed: bool, path: str) -> VoiceEffect:
    races = data.get("races") or []
    if isinstance(races, str):
        races = [races]
    extra = data.get("extra", data.get("effects")) # "effects" was the name before the sliders
    return VoiceEffect(name=name, title=str(data.get("name") or name), about=str(data.get("about") or ""),
                       races=[str(r) for r in races], ghosts=bool(data.get("ghosts", False)),
                       strength=clean_strength(data.get("strength"), where), sliders=clean_sliders(data.get("sliders"), where),
                       extra=parse_steps(extra, where), on=bool(data.get("on", False)),
                       from_mantella=from_mantella, changed=changed, path=path)


def file_name_for(title: str) -> str:
    """'Echoing Hall' -> 'echoing_hall'"""
    return re.sub(r"[^a-z0-9]+", "_", str(title or "").lower()).strip("_")


class VoiceEffects:
    """Mantella's effects and the player's, read again whenever a file in either folder changes"""

    def __init__(self, own_folder: str, built_in_folder: str | None = None) -> None:
        self.__own_folder = own_folder
        self.__built_in_folder = built_in_folder or built_in_effects_folder()
        self.__lock = threading.RLock()
        self.__signature: tuple = ()
        self.__effects: dict[str, VoiceEffect] = {}
        self.__problems: list[str] = []

    @property
    def own_folder(self) -> str:
        return self.__own_folder

    @property
    def built_in_folder(self) -> str:
        return self.__built_in_folder

    @staticmethod
    def __files(folder: str) -> dict[str, str]:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return {}
        return {os.path.splitext(n)[0].lower(): os.path.join(folder, n) for n in names if n.lower().endswith(".json")}

    def __reload_if_changed(self) -> None:
        mantellas, players = self.__files(self.__built_in_folder), self.__files(self.__own_folder)
        signature = tuple((p, os.path.getmtime(p) if os.path.exists(p) else 0) for p in [*mantellas.values(), *players.values()])
        if signature == self.__signature:
            return
        effects: dict[str, VoiceEffect] = {}
        problems: list[str] = []
        for name in sorted(set(mantellas) | set(players)):
            mantella_path, player_path = mantellas.get(name), players.get(name)
            where = os.path.basename(player_path or mantella_path or name)
            try:
                base = _read_json(mantella_path) if mantella_path else None
                own = _read_json(player_path) if player_path else None
                data = merged(base, own)
                file_name = os.path.splitext(os.path.basename(player_path or mantella_path))[0]
                changed = base is not None and any(k != "on" for k in (own or {}))
                effects[name] = effect_from(file_name, data, where, base is not None, changed, player_path or mantella_path)
            except Exception as e:
                problems.append(f"{where}: {e}")
                _warn_once(f"The voice effect {player_path or mantella_path} can't be used: {e}")
        self.__effects, self.__problems, self.__signature = effects, problems, signature

    def all(self) -> list[VoiceEffect]:
        with self.__lock:
            self.__reload_if_changed()
            return sorted(self.__effects.values(), key=lambda e: e.title.lower())

    def problems(self) -> list[str]:
        with self.__lock:
            self.__reload_if_changed()
            return list(self.__problems)

    def find(self, name: str) -> VoiceEffect | None:
        wanted = str(name or "").strip().lower()
        if not wanted:
            return None
        for effect in self.all():
            if wanted in (effect.name.lower(), effect.title.lower()):
                return effect
        return None

    def choose(self, speaker: Any) -> VoiceEffect | None:
        """The effect for an NPC: the voice_effect column first, then ghosts, then race. Only ticked effects; the player's own first"""
        if speaker is None:
            return None
        chosen = str(getattr(speaker, "voice_effect", "") or "").strip()
        if chosen:
            if chosen.lower() in NO_EFFECT:
                return None
            effect = self.find(chosen)
            if effect is not None:
                if not effect.on:
                    logging.debug(f"{getattr(speaker, 'name', '') or 'An NPC'} has the voice effect '{effect.title}', which isn't ticked, so no effect")
                    return None
                return effect
            _warn_once(f"{getattr(speaker, 'name', '') or 'An NPC'} has the voice effect '{chosen}' in their voice_effect column, but there is no effect "
                       f"by that name. Names: {', '.join(e.name for e in self.all())}. The usual effect for them is used instead.")
        effects = sorted((e for e in self.all() if e.on), key=lambda e: not e.is_own)
        if getattr(speaker, "is_ghost", False):
            for effect in effects:
                if effect.ghosts:
                    return effect
        race = race_key(getattr(speaker, "race", None))
        if race:
            for effect in effects:
                if race in (race_key(r) for r in effect.races):
                    return effect
        return None

    # ---- the player's changes ----

    def __own_path(self, name: str) -> str:
        return os.path.join(self.__own_folder, f"{name}.json")

    def __mantellas(self, name: str) -> dict | None:
        path = self.__files(self.__built_in_folder).get(name.lower())
        return _read_json(path) if path else None

    def __players(self, name: str) -> dict:
        path = self.__files(self.__own_folder).get(name.lower())
        return _read_json(path) if path else {}

    def __write(self, name: str, data: dict | None) -> None:
        """Writes the player's file for an effect, or removes it for None"""
        path = self.__files(self.__own_folder).get(name.lower()) or self.__own_path(name)
        if data is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            os.makedirs(self.__own_folder, exist_ok=True)
            with open(path, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2, ensure_ascii=False)
                file.write("\n")
        self.__signature = () # read again now, even within the file system's time step

    def __effect(self, name: str) -> VoiceEffect:
        effect = self.find(name)
        if effect is None:
            raise ValueError(f"There is no effect called '{name}'.")
        return effect

    def set_on(self, names: list[str]) -> None:
        """Ticks these effects and unticks the rest"""
        wanted = {str(n).lower() for n in names or []}
        with self.__lock:
            for effect in self.all():
                on = effect.name.lower() in wanted
                if on == effect.on:
                    continue
                players = self.__players(effect.name)
                if on:
                    players["on"] = True
                else:
                    players.pop("on", None)
                self.__write(effect.name, players if players or not effect.from_mantella else None)

    def save_changes(self, name: str, races: list[str], ghosts: bool, strength: Any, sliders: dict[str, Any]) -> VoiceEffect:
        """Saves who gets an effect and how it sounds. For one of Mantella's, only what differs from Mantella's is kept"""
        with self.__lock:
            effect = self.__effect(name)
            values = {"races": list(races or []), "ghosts": bool(ghosts), "strength": clean_strength(strength), "sliders": clean_sliders(sliders)}
            players = self.__players(effect.name)
            if effect.from_mantella:
                mantellas = effect_from(effect.name, self.__mantellas(effect.name) or {}, effect.name, True, False, "")
                for key in ("races", "ghosts", "strength"):
                    same = ({race_key(r) for r in values[key]} == {race_key(r) for r in mantellas.races}) if key == "races" else values[key] == getattr(mantellas, key)
                    if same:
                        players.pop(key, None)
                    else:
                        players[key] = values[key]
                changed = {k: v for k, v in values["sliders"].items() if v != mantellas.sliders[k]}
                if changed:
                    players["sliders"] = changed
                else:
                    players.pop("sliders", None)
            else:
                players.update(values)
                players.setdefault("name", effect.title)
            self.__write(effect.name, players if players or not effect.from_mantella else None)
            return self.__effect(effect.name)

    def undo_changes(self, name: str) -> VoiceEffect:
        """One of Mantella's back as Mantella made it. Keeps the tick"""
        with self.__lock:
            effect = self.__effect(name)
            if not effect.from_mantella:
                raise ValueError(f"{effect.title} is your own effect, so there is no Mantella version to go back to.")
            self.__write(effect.name, {"on": True} if effect.on else None)
            return self.__effect(effect.name)

    def save_as_new(self, title: str, from_name: str, strength: Any, sliders: dict[str, Any]) -> VoiceEffect:
        """A new effect of the player's own, with these sounds. It starts unticked and used for nobody"""
        title = str(title or "").strip()
        file_name = file_name_for(title)
        if not file_name:
            raise ValueError("Type a name for the new effect first.")
        if title.lower() in NO_EFFECT or file_name in NO_EFFECT: # in the Bio Editor and the voice_effect column these mean no effect
            raise ValueError(f"{title} means \"no effect\" in the Bio Editor, so an effect can't be called that. Pick another name.")
        with self.__lock:
            if self.find(title) is not None or self.find(file_name) is not None or file_name in self.__files(self.__built_in_folder):
                raise ValueError(f"There is already an effect called {title}. Pick another name.")
            if file_name in self.__files(self.__own_folder): # an effect of yours with a mistake in it: not listed, but not to be written over
                raise ValueError(f"Your effects folder already has {file_name}.json, which has a mistake in it (see How it works). "
                                 "Pick another name, or fix or delete that file.")
            source = self.find(from_name)
            data: dict[str, Any] = {"name": title, "about": "", "races": [], "ghosts": False,
                                    "strength": clean_strength(strength), "sliders": clean_sliders(sliders)}
            source_file = self.__players(source.name) if source and not source.from_mantella else merged(self.__mantellas(source.name) if source else None,
                                                                                                         self.__players(source.name) if source else None)
            if source_file.get("extra", source_file.get("effects")):
                data["extra"] = source_file.get("extra", source_file.get("effects"))
            self.__write(file_name, data)
            return self.__effect(file_name)

    def delete(self, name: str) -> None:
        """Removes one of the player's own effects. Mantella's can only be unticked"""
        with self.__lock:
            effect = self.__effect(name)
            if effect.from_mantella:
                raise ValueError(f"{effect.title} is one of Mantella's effects. Untick it instead.")
            self.__write(effect.name, None)


_shared: dict[str, VoiceEffects] = {}
_shared_lock = threading.Lock()


def shared_voice_effects(save_folder: str) -> VoiceEffects:
    folder = os.path.normcase(os.path.abspath(own_effects_folder(save_folder)))
    with _shared_lock:
        if folder not in _shared:
            _shared[folder] = VoiceEffects(own_effects_folder(save_folder))
        return _shared[folder]


# ---- the sound ----

def run_filters(samples: np.ndarray, rate: int, steps: list[tuple[str, str]]) -> np.ndarray:
    """Runs mono samples through a chain of ffmpeg filters and returns mono samples at the same rate"""
    if not steps or samples.size == 0:
        return samples
    graph = av.filter.Graph()
    source = graph.add_abuffer(format="flt", sample_rate=rate, layout="mono", time_base=Fraction(1, rate))
    last = source
    chain: list[tuple[str, str]] = []
    for name, settings in steps:
        if name == "pitch": # higher or lower without changing how long the line is, so the lip file still fits
            amount = float(settings)
            chain += [("asetrate", str(round(rate * amount))), ("aresample", str(rate)), ("atempo", f"{1 / amount:.6f}")]
        else:
            chain.append((name, settings))
    chain.append(("aformat", f"sample_fmts=flt:channel_layouts=mono:sample_rates={rate}"))
    for name, settings in chain:
        node = graph.add(name, settings) if settings else graph.add(name)
        last.link_to(node)
        last = node
    sink = graph.add("abuffersink")
    last.link_to(sink)
    graph.configure()

    out: list[np.ndarray] = []

    def drain() -> None:
        while True:
            try:
                frame = sink.pull()
            except (av.BlockingIOError, av.EOFError):
                return
            out.append(frame.to_ndarray().reshape(-1))

    for start in range(0, samples.size, CHUNK):
        chunk = np.ascontiguousarray(samples[start:start + CHUNK], dtype=np.float32).reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(chunk, format="flt", layout="mono")
        frame.sample_rate = rate
        frame.pts = start
        frame.time_base = Fraction(1, rate)
        source.push(frame)
        drain()
    source.push(None)
    drain()
    return np.concatenate(out).astype(np.float32) if out else np.zeros(0, dtype=np.float32)


WAVER_RATE = 5.0 # times a second the voice wavers
WAVER_MOST_DELAY = 0.004 # seconds: at Waver 100 the pitch swings about a semitone


def waver(samples: np.ndarray, rate: int, amount: float) -> np.ndarray:
    """An unsteady voice: the pitch swings up and down a little, 5 times a second. *amount* 0-100.
    Done here, not with ffmpeg's vibrato, which now and then gives broken samples"""
    depth = max(0.0, min(100.0, amount)) / 100 * WAVER_MOST_DELAY * rate
    if depth <= 0 or samples.size == 0:
        return samples
    n = np.arange(samples.size)
    delay = depth / 2 * (1 - np.cos(2 * np.pi * WAVER_RATE * n / rate)) # starts at no delay, so the line's start isn't moved
    return np.interp(n - delay, n, samples, left=0.0).astype(np.float32)


def slider_steps(sliders: dict[str, float], rate: int) -> list[tuple[str, str]]:
    """The ffmpeg filters for Thin, Muffled, Growl and Doubled voice, in that order.
    Pitch, Waver and Echo are done apart (one pitch pass with the voice's own Pitch; waver() and reverb() here)"""
    steps: list[tuple[str, str]] = []
    thin, muffled = sliders.get("thin", 0) / 100, sliders.get("muffled", 0) / 100
    if thin > 0:
        steps.append(("highpass", f"f={20 * 40 ** thin:.0f}:poles=2"))
    if muffled > 0:
        steps.append(("lowpass", f"f={min(12000 * 0.1 ** muffled, rate * 0.45):.0f}:poles=2"))
    growl = sliders.get("growl", 0) / 100
    if growl > 0:
        steps.append(("tremolo", f"f=35:d={0.6 * growl:.3f}"))
    doubled = sliders.get("doubled", 0) / 100
    if doubled > 0:
        steps.append(("chorus", f"in_gain=0.7:out_gain=0.9:delays=25|40:decays={0.55 * doubled:.3f}|{0.4 * doubled:.3f}:speeds=0.3|0.45:depths=2|3"))
    return steps


def reverb(samples: np.ndarray, rate: int, echo: float, space: float) -> np.ndarray:
    """A room or a cave around the voice. *echo* 0-100: how much of it is heard. *space* 0-100: how long it rings.
    The echo after the last word is kept short and faded, since the game waits for the whole line"""
    wet = max(0.0, min(100.0, echo)) / 100
    if wet <= 0 or samples.size == 0:
        return samples
    seconds = REVERB_SHORTEST * (REVERB_LONGEST / REVERB_SHORTEST) ** (max(0.0, min(100.0, space)) / 100)
    length = max(1, int(rate * seconds))
    pre_delay = int(rate * (0.01 + 0.02 * space / 100))
    t = np.arange(length) / rate
    noise = np.random.default_rng(7).standard_normal(length) * np.exp(-6.9 * t / seconds) # 60 dB quieter after *seconds*
    noise = lfilter([0.35], [1, -0.65], noise) # softer highs, like stone and air
    response = np.concatenate([np.zeros(pre_delay), noise]).astype(np.float32)
    response /= max(1e-9, float(np.sqrt(np.sum(response ** 2))))
    tail = min(pre_delay + int(rate * seconds * 2 / 3), int(rate * REVERB_TAIL_MOST)) # by 2/3 of *seconds* it has died 40 dB away
    ringing = fftconvolve(samples, response)[:samples.size + tail].astype(np.float32)
    out = np.zeros(ringing.size, dtype=np.float32)
    out[:samples.size] = samples * (1 - 0.3 * wet)
    out += ringing * 0.8 * wet
    tail = out.size - samples.size
    fade = min(tail, int(rate * REVERB_FADE))
    if fade > 0:
        out[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    return out


def speech_level_db(samples: np.ndarray, rate: int) -> float | None:
    """How loud the spoken parts are (pauses left out), in dB below full scale. None for silence"""
    window = max(1, int(rate * SPEECH_WINDOW_SECONDS))
    count = samples.size // window
    if count == 0:
        levels = np.array([np.sqrt(np.mean(samples ** 2))]) if samples.size else np.zeros(0)
    else:
        levels = np.sqrt(np.mean(samples[:count * window].reshape(count, window) ** 2, axis=1))
    if levels.size == 0 or levels.max() <= 1e-5:
        return None
    speech = levels[levels >= levels.max() * 10 ** (-SPEECH_BELOW_LOUDEST_DB / 20)]
    return float(20 * np.log10(np.sqrt(np.mean(speech ** 2))))


def at_game_loudness(samples: np.ndarray, rate: int, volume_percent: float) -> np.ndarray:
    """As loud as Skyrim's own lines, then Voice Volume, then a limiter so loud lines don't crackle"""
    level = speech_level_db(samples, rate)
    gain_db = min(GAME_LOUDNESS_DB - level, MOST_BOOST_DB) if level is not None else 0.0
    louder = samples * (10 ** (gain_db / 20) * max(0.0, volume_percent) / 100)
    if louder.size and float(np.abs(louder).max()) > LIMIT:
        louder = run_filters(louder, rate, [("alimiter", f"limit={LIMIT}:level=false:latency=true:attack=5:release=50")])
    return np.clip(louder, -LIMIT, LIMIT)


def pitch_factor(semitones: float) -> float:
    return min(PITCH_RANGE[1], max(PITCH_RANGE[0], 2 ** (float(semitones or 0) / 12)))


def apply_effect(samples: np.ndarray, rate: int, effect: VoiceEffect | None, pitch_semitones: float = 0) -> np.ndarray:
    """The voice's Pitch and the effect's Deeper or higher in one pass, then the effect's other sliders, then its extra filters"""
    sliders = effect.scaled() if effect is not None else {}
    semitones = float(pitch_semitones or 0) + sliders.get("pitch", 0)
    if abs(semitones) > 1e-6:
        samples = run_filters(samples, rate, [("pitch", str(pitch_factor(semitones)))])
    if effect is not None:
        steps = slider_steps(sliders, rate)
        doubled = [s for s in steps if s[0] == "chorus"]
        samples = run_filters(samples, rate, [s for s in steps if s[0] != "chorus"]) # Thin, Muffled, Growl
        samples = waver(samples, rate, sliders.get("waver", 0))
        samples = run_filters(samples, rate, doubled)
        samples = reverb(samples, rate, sliders.get("echo", 0), sliders.get("space", 0))
        samples = run_filters(samples, rate, effect.extra)
    return np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0) # a filter that slips gives a click, not a broken line


def finish_line(path: str, effect: VoiceEffect | None, volume_percent: float = 100, pitch_semitones: float = 0) -> None:
    """The voice's Pitch, the voice effect, then the loudness, written over the line's sound file"""
    samples, rate = sf.read(path, dtype="float32", always_2d=True)
    samples = samples.mean(axis=1)
    if samples.size == 0:
        return
    try:
        samples = apply_effect(samples, rate, effect, pitch_semitones)
    except Exception as e: # the line still gets the voice's Pitch and Voice Volume
        _warn_once(f"The voice effect {effect.path if effect else ''} didn't work on a line: {e}. Lines play without it.")
        samples = apply_effect(sf.read(path, dtype="float32", always_2d=True)[0].mean(axis=1), rate, None, pitch_semitones)
    samples = at_game_loudness(samples, rate, volume_percent)
    sf.write(path, samples, rate, subtype="PCM_16")
