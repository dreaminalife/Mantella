"""The voice clips that services copy voices from.

voice_clips/voice_types/<voice type>.wav is the clip every NPC with that voice type uses (FemaleEvenToned.wav).
voice_clips/npcs/<plugin>_<local form ID>.wav is a clip of one NPC's own (Skyrim.esm_000A2C94.wav).
Next to each clip, a .json says how it came about: the game line it was copied from, the file's size and where it came from
(a loose file or which archive), the lines the player turned down, and whether the player added the sound file themselves.
voice_clips/candidates/<voice_types|npcs>/<key>/ holds the voice's other lines from the game.
voice_clips/npcs_seen.json lists the NPCs that have spoken, so they can be given a clip of their own from the list.
Only clips Mantella copied from the game count: a sound file with no .json next to it is left alone.

Each voice has a list of chosen clips, in order: the clip itself on top, then the other lines whose .json has an order of 1, 2, ...
(0 is not chosen). Every service takes as much as it can from the top: Pocket TTS and voice setups the top clip, Inworld the top
30 seconds, ElevenLabs up to 2 minutes (lines_within).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from typing import Any

import soundfile as sf

VOICE_TYPES = "voice_types"
NPCS = "npcs"
CANDIDATES = "candidates"
NPCS_SEEN_FILE = "npcs_seen.json"
VOICE_SETTINGS_FILE = "voice_settings.json" # each voice's Pitch and style settings, kept apart from its clip so swapping the clip keeps them
PITCH = "pitch"
MOST_PITCH = 12.0 # semitones up or down
CLIP_EXTENSION = ".wav"
MOST_CHOSEN = 16 # clips a voice is cloned from at most: about 2 minutes of game lines
JOIN_GAP_SECONDS = 0.5 # the silence between two clips joined into one, counted against a service's length

# How a clip came about
AUTOMATIC = "automatic" # a line from the game, picked by Mantella
OWN_LINES = "own lines" # an NPC whose own lines come from another place than their voice type's (a voice mod for just them)
CHOSEN = "chosen" # a line the player picked from the list


def voice_key(name: str | None) -> str:
    """A voice type's name as clips are stored: 'Female Even Toned' and 'FemaleEvenToned' are both 'femaleeventoned'"""
    if not name:
        return ""
    return "".join(c for c in str(name).lower() if c.isalnum())


def npc_key(plugin: str | None, local_id: str | None) -> str:
    if not plugin or not local_id:
        return ""
    return voice_key(f"{plugin}_{local_id}")


def line_id(path: str | None) -> str:
    """A game line as '<plugin>|<INFO form ID>', from its voice file's path: sound\\voice\\skyrim.esm\\femaleeventoned\\dialoguesolitude__000dd60f_1.fuz"""
    if not path:
        return ""
    parts = path.replace("/", "\\").lower().split("\\")
    if len(parts) < 3:
        return ""
    stem = os.path.splitext(parts[-1])[0].split("_")
    if len(stem) < 3:
        return ""
    return f"{parts[-3]}|{stem[-2]}"


_seconds_cache: dict[tuple[str, int, int], float] = {}


def clip_seconds(path: str) -> float:
    """How long a clip file plays (read from its header, so cheap). 0 when it can't be read"""
    try:
        stat = os.stat(path)
        version = (path, stat.st_size, stat.st_mtime_ns)
        found = _seconds_cache.get(version)
        if found is None:
            found = float(sf.info(path).duration)
            _seconds_cache[version] = found
        return found
    except Exception:
        return 0.0


def lines_within(seconds: list[float], most_seconds: float, most_lines: int = MOST_CHOSEN) -> int:
    """How many lines from the top of a voice's chosen clips a service takes: the top one always, then each next one while the
    clips, with a short gap between two, stay within *most_seconds*. 0 for most_seconds means the top clip only"""
    if not seconds:
        return 0
    if most_seconds <= 0:
        return 1
    total = seconds[0]
    count = 1
    for length in seconds[1:]:
        if count >= most_lines or total + JOIN_GAP_SECONDS + length > most_seconds:
            break
        total += JOIN_GAP_SECONDS + length
        count += 1
    return count


@dataclass
class ClipInfo:
    """What the .json next to a clip says"""
    name: str = "" # the voice type, or the NPC's name
    how: str = AUTOMATIC
    path: str = "" # the game line's voice file, relative to the game's Data folder
    plugin: str = ""
    info: str = "" # the game line's INFO form ID
    size: int = 0 # the game file's size and where it came from: a voice replacer changes them
    source: str = ""
    seconds: float = 0.0
    turned_down: list[str] = field(default_factory=list) # line ids the player turned down
    voice_type: str = "" # for an NPC's own clip
    npc_plugin: str = ""
    npc_local_id: str = ""
    order: int = 0 # one of the voice's other lines: its place among the chosen clips below the top one (1, 2, ...); 0 is not chosen
    # The voice's clip: the player changed which clips it is cloned from. Mantella then changes none of them by itself; it only adds
    # new lines after them when the service speaking needs more
    hand_picked: bool = False

    @property
    def line(self) -> str:
        return line_id(self.path)

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    @staticmethod
    def from_json(data: dict[str, Any]) -> "ClipInfo":
        known = {k: v for k, v in data.items() if k in ClipInfo.__dataclass_fields__}
        return ClipInfo(**known)


@dataclass
class ClipEntry:
    kind: str # VOICE_TYPES or NPCS
    key: str
    path: str
    info: ClipInfo


class ClipStore:
    def __init__(self, folder: str) -> None:
        self.__folder = folder
        self.__lock = threading.RLock()
        self.__index: dict[str, tuple[float, dict[str, str]]] = {} # sub folder -> (modified time, key -> path)

    @property
    def folder(self) -> str:
        return self.__folder

    # ---- finding clips ----

    def voice_type_clip(self, voice_type: str | None) -> str | None:
        return self.__find(VOICE_TYPES, voice_key(voice_type))

    def npc_clip(self, plugin: str | None, local_id: str | None) -> str | None:
        return self.__find(NPCS, npc_key(plugin, local_id))

    def clip(self, kind: str, key: str) -> str | None:
        return self.__find(kind, key)

    def voice_type_keys(self) -> list[str]:
        return sorted(self.__entries(VOICE_TYPES).keys())

    def entries(self, kind: str) -> list[ClipEntry]:
        return [ClipEntry(kind, key, path, self.info(kind, key)) for key, path in sorted(self.__entries(kind).items())]

    def info(self, kind: str, key: str) -> ClipInfo:
        path = self.__find(kind, key)
        if not path:
            return ClipInfo()
        try:
            with open(os.path.splitext(path)[0] + ".json", encoding="utf-8") as file:
                return ClipInfo.from_json(json.load(file))
        except (OSError, ValueError):
            return ClipInfo(name=key)

    # ---- changing clips ----

    def set_clip(self, kind: str, key: str, wav_path: str, info: ClipInfo) -> str:
        """Makes *wav_path* the clip for *key*, replacing the one before. The old voice is thrown away by each service on its next line"""
        with self.__lock:
            folder = os.path.join(self.__folder, kind)
            os.makedirs(folder, exist_ok=True)
            self.__remove_files(kind, key)
            destination = os.path.join(folder, key + ".wav")
            shutil.copyfile(wav_path, destination)
            self.__write_info(destination, info)
            self.__index.pop(kind, None)
            return destination

    def save_info(self, kind: str, key: str, info: ClipInfo) -> None:
        path = self.__find(kind, key)
        if path:
            self.__write_info(path, info)

    def remove(self, kind: str, key: str) -> None:
        """Removes a clip, for example an NPC's own one, which puts them back on their voice type's clip. Its Pitch and other
        settings go too"""
        with self.__lock:
            self.set_voice_settings(kind, key, {})
            self.__remove_files(kind, key)
            shutil.rmtree(self.__candidate_folder(kind, key), ignore_errors=True)
            self.__index.pop(kind, None)

    # ---- the voice's other lines ----

    def candidates(self, kind: str, key: str) -> list[tuple[str, ClipInfo]]:
        """The voice's other game lines, chosen or not, not turned down, by file name. Another file of the clip's own line is left out"""
        folder = self.__candidate_folder(kind, key)
        if not os.path.isdir(folder):
            return []
        current = self.info(kind, key)
        turned_down = set(current.turned_down) | ({current.line} if current.line else set())
        found: list[tuple[str, ClipInfo]] = []
        for file_name in sorted(os.listdir(folder)):
            if not file_name.endswith(".wav"):
                continue
            path = os.path.join(folder, file_name)
            try:
                with open(os.path.splitext(path)[0] + ".json", encoding="utf-8") as file:
                    info = ClipInfo.from_json(json.load(file))
            except (OSError, ValueError):
                continue
            if info.line not in turned_down:
                found.append((path, info))
        return found

    def add_candidate(self, kind: str, key: str, wav_path: str, info: ClipInfo) -> str:
        folder = self.__candidate_folder(kind, key)
        os.makedirs(folder, exist_ok=True)
        name = voice_key(info.line) or str(len(os.listdir(folder)))
        destination = os.path.join(folder, name + ".wav")
        if os.path.abspath(wav_path) != os.path.abspath(destination):
            shutil.copyfile(wav_path, destination)
        self.__write_info(destination, info)
        return destination

    def voice_lines(self, kind: str, key: str) -> list[tuple[str, ClipInfo]]:
        """The clips the voice is cloned from, in order: the clip, then the chosen other lines"""
        with self.__lock:
            clip = self.__find(kind, key)
            if not clip:
                return []
            chosen = sorted((c for c in self.candidates(kind, key) if c[1].order > 0), key=lambda c: c[1].order)
            return [(clip, self.info(kind, key)), *chosen]

    def other_lines(self, kind: str, key: str) -> list[tuple[str, ClipInfo]]:
        """The voice's lines that aren't chosen"""
        return [c for c in self.candidates(kind, key) if c[1].order <= 0]

    def choose_candidate(self, kind: str, key: str, candidate_path: str) -> None:
        """Use this one: the line swaps places with the clip. The old clip takes the line's place, chosen or not"""
        with self.__lock:
            self.__swap(kind, key, candidate_path)
            self.__mark_hand_picked(kind, key)

    def add_line(self, kind: str, key: str, candidate_path: str, position: int) -> None:
        """Add: the line joins the chosen clips at *position* (1 is right below the clip), the ones from there on move down"""
        with self.__lock:
            chosen = [p for p, _ in self.voice_lines(kind, key)[1:]]
            if candidate_path in chosen:
                chosen.remove(candidate_path)
            position = max(1, min(position, len(chosen) + 1))
            chosen.insert(position - 1, candidate_path)
            self.__set_orders(kind, key, chosen)
            self.__mark_hand_picked(kind, key)

    def take_out(self, kind: str, key: str, path: str) -> None:
        """Take out: the clip goes back among the other lines. Taking out the top clip puts the next chosen one on top.
        A voice always keeps one clip"""
        with self.__lock:
            lines = [p for p, _ in self.voice_lines(kind, key)]
            if path not in lines:
                return
            if len(lines) <= 1:
                raise ValueError("A voice needs at least one clip.")
            if path == lines[0]:
                swapped_in = lines[1]
                old_top = self.__swap(kind, key, swapped_in)
                lines = [p for p, _ in self.voice_lines(kind, key)]
                path = old_top
            self.__set_orders(kind, key, [p for p in lines[1:] if p != path])
            self.__mark_hand_picked(kind, key)

    def drop_other_lines(self, kind: str, key: str) -> int:
        """Find more clips brought new lines: the lines that weren't chosen go, and aren't found again until the game has no new
        ones left. Returns how many went"""
        with self.__lock:
            gone = self.other_lines(kind, key)
            if not gone:
                return 0
            info = self.info(kind, key)
            info.turned_down = list(dict.fromkeys([*info.turned_down, *(c.line for _, c in gone if c.line)]))
            self.save_info(kind, key, info)
            for path, _ in gone:
                self.__remove_candidate(path)
            return len(gone)

    def keep_chosen(self, kind: str, key: str, keep: list[str]) -> None:
        """Only these of the chosen clips below the top stay chosen, in their order (Find more clips: the other clips no service
        in use takes are swapped out)"""
        with self.__lock:
            wanted = {os.path.abspath(p) for p in keep}
            self.__set_orders(kind, key, [p for p, _ in self.voice_lines(kind, key)[1:] if os.path.abspath(p) in wanted])

    def drop_line(self, kind: str, key: str, candidate_path: str) -> None:
        """One of the voice's other lines is gone from the game: it goes, and the chosen ones below it move up"""
        with self.__lock:
            chosen = [p for p, _ in self.voice_lines(kind, key)[1:] if os.path.abspath(p) != os.path.abspath(candidate_path)]
            self.__remove_candidate(candidate_path)
            self.__set_orders(kind, key, chosen)

    def is_hand_picked(self, kind: str, key: str) -> bool:
        return self.info(kind, key).hand_picked

    def set_candidate_info(self, candidate_path: str, info: ClipInfo) -> None:
        self.__write_info(candidate_path, info)

    def use_npc_clip_for_voice_type(self, key: str) -> str:
        """An NPC's own voice becomes their voice type's voice, and the NPC shares it again: their own clips go, the chosen ones and
        the rest moving with the voice. The voice type's old clips stay as other lines, so the player can switch back.
        Returns the voice type's key"""
        with self.__lock:
            npc_path = self.__find(NPCS, key)
            npc = self.info(NPCS, key)
            voice_type = voice_key(npc.voice_type)
            if not npc_path or not voice_type:
                raise ValueError("This NPC has no voice of their own, or their voice type isn't known.")
            old_path = self.__find(VOICE_TYPES, voice_type)
            old = self.info(VOICE_TYPES, voice_type)
            for path, info in self.candidates(VOICE_TYPES, voice_type):
                if info.order:
                    info.order = 0
                    self.__write_info(path, info)
            if old_path:
                self.add_candidate(VOICE_TYPES, voice_type, old_path, ClipInfo.from_json({**old.to_json(), "turned_down": [], "order": 0, "hand_picked": False}))
            for path, info in self.candidates(NPCS, key):
                self.add_candidate(VOICE_TYPES, voice_type, path, info)
            new = ClipInfo.from_json({**npc.to_json(), "how": CHOSEN, "name": old.name if old_path else npc.voice_type,
                                      "turned_down": list(old.turned_down) if old_path else [], "voice_type": "", "npc_plugin": "", "npc_local_id": ""})
            self.set_clip(VOICE_TYPES, voice_type, npc_path, new)
            self.set_voice_settings(VOICE_TYPES, voice_type, self.voice_settings(NPCS, key)) # the voice type now sounds just like the NPC did
            self.remove(NPCS, key)
            return voice_type

    def bring_back_turned_down(self, kind: str, key: str) -> int:
        """Forgets the lines turned down for this clip, so asking for more lines can find them again. Returns how many"""
        with self.__lock:
            info = self.info(kind, key)
            count = len(info.turned_down)
            if count:
                info.turned_down = []
                self.save_info(kind, key, info)
            return count

    def turn_down_all(self, kind: str, key: str) -> list[str]:
        """The lines turned down so far, plus the clip's own line: what a request for more lines leaves out"""
        info = self.info(kind, key)
        lines = list(info.turned_down)
        if info.line and info.line not in lines:
            lines.append(info.line)
        return lines

    # ---- how a voice is changed after it's made ----

    def pitch(self, kind: str, key: str) -> float:
        """A voice's Pitch in semitones: below 0 deeper, above 0 higher"""
        try:
            return float(self.voice_setting(kind, key, PITCH) or 0)
        except (TypeError, ValueError):
            return 0.0

    def set_pitch(self, kind: str, key: str, semitones: float) -> None:
        semitones = round(max(-MOST_PITCH, min(MOST_PITCH, float(semitones or 0))), 2)
        self.set_voice_setting(kind, key, PITCH, semitones or None)

    def voice_setting(self, kind: str, key: str, name: str, model_id: str | None = None) -> Any:
        """One of the voice's own settings (Pitch, or a voice service's style such as heygen_expressiveness). None: not set,
        so the voice uses the Text-to-Speech page's.
        *model_id*: a style setting is kept per model ({model: value}). A plain value (kept before settings were per model, or from an
        older friend's file) counts as the picked model's until another model is picked"""
        value = self.voice_settings(kind, key).get(name)
        if model_id is None:
            return value
        return value.get(model_id) if isinstance(value, dict) else value

    def voice_settings(self, kind: str, key: str) -> dict[str, Any]:
        """All of the voice's own settings, by name"""
        found = self.__voice_settings().get(f"{kind}/{key}", {})
        return dict(found) if isinstance(found, dict) else {}

    def set_voice_setting(self, kind: str, key: str, name: str, value: Any, model_id: str | None = None) -> None:
        """None takes the voice's own value away. *model_id*: only that model's value of a style setting"""
        if not key:
            return
        with self.__lock:
            settings = self.__voice_settings()
            entry = f"{kind}/{key}"
            if model_id is not None:
                stored = settings.get(entry, {}).get(name)
                by_model = dict(stored) if isinstance(stored, dict) else {} # a plain value was the picked model's: this replaces it
                if value is None:
                    by_model.pop(model_id, None)
                else:
                    by_model[model_id] = value
                value = by_model or None
            if value is not None:
                if settings.get(entry, {}).get(name) == value:
                    return
                settings.setdefault(entry, {})[name] = value
            elif name in settings.get(entry, {}):
                settings[entry].pop(name)
                if not settings[entry]:
                    del settings[entry]
            else:
                return
            self.__write_json(os.path.join(self.__folder, VOICE_SETTINGS_FILE), settings)

    def set_voice_settings(self, kind: str, key: str, values: dict[str, Any]) -> None:
        """Replaces all of the voice's own settings: empty takes them all away"""
        if not key:
            return
        with self.__lock:
            settings = self.__voice_settings()
            entry = f"{kind}/{key}"
            values = {name: value for name, value in values.items() if value is not None}
            if settings.get(entry, {}) == values or (not values and entry not in settings):
                return
            if values:
                settings[entry] = values
            else:
                del settings[entry]
            self.__write_json(os.path.join(self.__folder, VOICE_SETTINGS_FILE), settings)

    def pin_plain_values(self, names: list[str], model_id: str) -> None:
        """Another model is picked: every voice's plain values of these settings (kept before settings were per model) stay with the
        model they were set for"""
        with self.__lock:
            settings = self.__voice_settings()
            changed = False
            for values in settings.values():
                if not isinstance(values, dict):
                    continue
                for name in names:
                    if name in values and not isinstance(values[name], dict):
                        values[name] = {model_id: values[name]}
                        changed = True
            if changed:
                self.__write_json(os.path.join(self.__folder, VOICE_SETTINGS_FILE), settings)

    def __voice_settings(self) -> dict[str, dict[str, Any]]:
        try:
            with open(os.path.join(self.__folder, VOICE_SETTINGS_FILE), encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    # ---- NPCs that have spoken ----

    def remember_npc(self, plugin: str, local_id: str, name: str, voice_type: str) -> bool:
        """Notes an NPC that spoke. True until the game has checked whether their own lines come from somewhere else"""
        key = npc_key(plugin, local_id)
        if not key:
            return False
        with self.__lock:
            seen = self.npcs_seen()
            entry = seen.get(key)
            if entry is None or entry.get("voice_type") != voice_type:
                seen[key] = {"name": name, "plugin": plugin, "local_id": local_id, "voice_type": voice_type, "checked": False}
                self.__write_json(os.path.join(self.__folder, NPCS_SEEN_FILE), seen)
                return True
            return not entry.get("checked", False)

    def mark_npc_checked(self, key: str) -> None:
        with self.__lock:
            seen = self.npcs_seen()
            if key in seen and not seen[key].get("checked"):
                seen[key]["checked"] = True
                self.__write_json(os.path.join(self.__folder, NPCS_SEEN_FILE), seen)

    def npcs_seen(self) -> dict[str, dict[str, str]]:
        try:
            with open(os.path.join(self.__folder, NPCS_SEEN_FILE), encoding="utf-8") as file:
                return json.load(file)
        except (OSError, ValueError):
            return {}

    # ---- inside ----

    def __swap(self, kind: str, key: str, candidate_path: str) -> str:
        """The line becomes the clip; the old clip becomes a line in its place. Returns the old clip's path among the lines"""
        old = self.info(kind, key)
        old_path = self.__find(kind, key)
        with open(os.path.splitext(candidate_path)[0] + ".json", encoding="utf-8") as file:
            new = ClipInfo.from_json(json.load(file))
        moved = ""
        if old_path:
            moved = self.add_candidate(kind, key, old_path, ClipInfo.from_json({**old.to_json(), "turned_down": [], "order": new.order,
                                                                               "hand_picked": False}))
        new.turned_down = list(old.turned_down)
        new.how = CHOSEN
        new.order = 0
        new.hand_picked = old.hand_picked
        new.name = old.name or new.name
        new.voice_type = old.voice_type or new.voice_type
        new.npc_plugin, new.npc_local_id = old.npc_plugin or new.npc_plugin, old.npc_local_id or new.npc_local_id
        self.set_clip(kind, key, candidate_path, new)
        if os.path.abspath(candidate_path) != os.path.abspath(moved):
            self.__remove_candidate(candidate_path)
        return moved

    def __set_orders(self, kind: str, key: str, chosen: list[str]) -> None:
        places = {os.path.abspath(p): i + 1 for i, p in enumerate(chosen[:MOST_CHOSEN - 1])}
        for path, info in self.candidates(kind, key):
            order = places.get(os.path.abspath(path), 0)
            if info.order != order:
                info.order = order
                self.__write_info(path, info)

    def __mark_hand_picked(self, kind: str, key: str) -> None:
        info = self.info(kind, key)
        if self.__find(kind, key) and not info.hand_picked:
            info.hand_picked = True
            self.save_info(kind, key, info)

    @staticmethod
    def __remove_candidate(path: str) -> None:
        for file in (path, os.path.splitext(path)[0] + ".json"):
            try:
                os.remove(file)
            except OSError:
                pass

    def __candidate_folder(self, kind: str, key: str) -> str:
        return os.path.join(self.__folder, CANDIDATES, kind, key)

    def __remove_files(self, kind: str, key: str) -> None:
        folder = os.path.join(self.__folder, kind)
        if not os.path.isdir(folder):
            return
        for file_name in os.listdir(folder):
            stem, extension = os.path.splitext(file_name)
            if voice_key(stem) == key and extension.lower() in (CLIP_EXTENSION, ".json"):
                try:
                    os.remove(os.path.join(folder, file_name))
                except OSError as e:
                    logging.warning(f"Could not remove the old voice clip {file_name}: {e}")

    def __write_info(self, clip_path: str, info: ClipInfo) -> None:
        self.__write_json(os.path.splitext(clip_path)[0] + ".json", info.to_json())

    @staticmethod
    def __write_json(path: str, data: Any) -> None:
        temporary = path + ".tmp"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        os.replace(temporary, path)

    def __find(self, sub_folder: str, key: str) -> str | None:
        if not key:
            return None
        return self.__entries(sub_folder).get(key)

    def __entries(self, sub_folder: str) -> dict[str, str]:
        path = os.path.join(self.__folder, sub_folder)
        try:
            modified = os.path.getmtime(path)
        except OSError:
            return {}
        with self.__lock:
            cached = self.__index.get(sub_folder)
            if cached and cached[0] == modified:
                return cached[1]
            entries: dict[str, str] = {}
            file_names = sorted(os.listdir(path))
            with_notes = {os.path.splitext(f)[0].lower() for f in file_names if f.lower().endswith(".json")}
            for file_name in file_names:
                stem, extension = os.path.splitext(file_name)
                if extension.lower() == CLIP_EXTENSION and stem.lower() in with_notes: # a clip Mantella copied, with its .json
                    entries.setdefault(voice_key(stem), os.path.join(path, file_name))
            self.__index[sub_folder] = (modified, entries)
            return entries


_shared: dict[str, ClipStore] = {}
_shared_lock = threading.Lock()


def shared_clip_store(folder: str) -> ClipStore:
    """One store per voice clips folder, shared by every service, the requests to the game and the TTS page"""
    with _shared_lock:
        found = _shared.get(folder)
        if found is None:
            found = ClipStore(folder)
            _shared[folder] = found
        return found
