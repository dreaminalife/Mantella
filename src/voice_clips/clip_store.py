"""The voice clips that services copy voices from.

voice_clips/voice_types/<voice type>.wav is the clip every NPC with that voice type uses (FemaleEvenToned.wav).
voice_clips/npcs/<plugin>_<local form ID>.wav is a clip of one NPC's own (Skyrim.esm_000A2C94.wav).
Next to each clip, a .json says how it came about: the game line it was copied from, the file's size and where it came from
(a loose file or which archive), the lines the player turned down, and whether the player added the sound file themselves.
voice_clips/candidates/<voice_types|npcs>/<key>/ holds other lines from the game to choose from.
voice_clips/npcs_seen.json lists the NPCs that have spoken, so they can be given a clip of their own from the list.
Only clips Mantella copied from the game count: a sound file with no .json next to it is left alone.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from typing import Any

VOICE_TYPES = "voice_types"
NPCS = "npcs"
CANDIDATES = "candidates"
NPCS_SEEN_FILE = "npcs_seen.json"
CLIP_EXTENSION = ".wav"

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
        """Removes a clip, for example an NPC's own one, which puts them back on their voice type's clip"""
        with self.__lock:
            self.__remove_files(kind, key)
            shutil.rmtree(self.__candidate_folder(kind, key), ignore_errors=True)
            self.__index.pop(kind, None)

    # ---- lines to choose from ----

    def candidates(self, kind: str, key: str) -> list[tuple[str, ClipInfo]]:
        """Other game lines for this clip, not yet turned down. Another file of the clip's own line is left out"""
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

    def add_candidate(self, kind: str, key: str, wav_path: str, info: ClipInfo) -> None:
        folder = self.__candidate_folder(kind, key)
        os.makedirs(folder, exist_ok=True)
        name = voice_key(info.line) or str(len(os.listdir(folder)))
        destination = os.path.join(folder, name + ".wav")
        shutil.copyfile(wav_path, destination)
        self.__write_info(destination, info)

    def choose_candidate(self, kind: str, key: str, candidate_path: str) -> None:
        """Pick another line: the chosen line becomes the clip and the one before goes into the list, so the player can switch back"""
        with self.__lock:
            old = self.info(kind, key)
            old_path = self.__find(kind, key)
            with open(os.path.splitext(candidate_path)[0] + ".json", encoding="utf-8") as file:
                new = ClipInfo.from_json(json.load(file))
            if old_path:
                self.add_candidate(kind, key, old_path, ClipInfo.from_json({**old.to_json(), "turned_down": []}))
            new.turned_down = list(old.turned_down)
            new.how = CHOSEN
            new.name = old.name or new.name
            new.voice_type = old.voice_type or new.voice_type
            new.npc_plugin, new.npc_local_id = old.npc_plugin or new.npc_plugin, old.npc_local_id or new.npc_local_id
            self.set_clip(kind, key, candidate_path, new)
            for path in (candidate_path, os.path.splitext(candidate_path)[0] + ".json"):
                if os.path.exists(path):
                    os.remove(path)

    def use_npc_clip_for_voice_type(self, key: str) -> str:
        """An NPC's own clip becomes their voice type's clip, and the NPC shares it again: their own clip goes.
        The voice type's old clip goes into its list, so the player can switch back. Returns the voice type's key"""
        with self.__lock:
            npc_path = self.__find(NPCS, key)
            npc = self.info(NPCS, key)
            voice_type = voice_key(npc.voice_type)
            if not npc_path or not voice_type:
                raise ValueError("This NPC has no voice of their own, or their voice type isn't known.")
            old_path = self.__find(VOICE_TYPES, voice_type)
            old = self.info(VOICE_TYPES, voice_type)
            if old_path:
                self.add_candidate(VOICE_TYPES, voice_type, old_path, ClipInfo.from_json({**old.to_json(), "turned_down": []}))
            new = ClipInfo.from_json({**npc.to_json(), "how": CHOSEN, "name": old.name if old_path else npc.voice_type,
                                      "turned_down": list(old.turned_down) if old_path else [], "voice_type": "", "npc_plugin": "", "npc_local_id": ""})
            self.set_clip(VOICE_TYPES, voice_type, npc_path, new)
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
