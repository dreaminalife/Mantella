"""The voices an online service cloned on the player's account, remembered so each clip is uploaded once.

voice_clips/online_voices/<service>.json holds, by voice (a voice type's or an NPC's key, or try_<key> for a clip being tried on the
Voice Clips page), the voice's ID on the service and the hash of the clip it was cloned from. It also holds a fingerprint of the key:
voices cloned with another key belong to another account, so they are forgotten when the key changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from typing import Any, Callable

from src.tts.clip_service import clip_hash
from src.tts.voice_folders import voice_clips_folder
from src.tts.voice_picker import TRIAL_PREFIX

FOLDER = "online_voices"


def key_fingerprint(api_key: str) -> str:
    """Tells accounts apart without keeping the key itself"""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12] if api_key else ""


class ClonedVoices:
    def __init__(self, path: str) -> None:
        self.__path = path
        self.__lock = threading.RLock()

    @property
    def path(self) -> str:
        return self.__path

    def get(self, key: str) -> dict[str, str] | None:
        with self.__lock:
            return self.__read()["voices"].get(key)

    def set(self, key: str, voice_id: str, clip: str) -> None:
        with self.__lock:
            data = self.__read()
            data["voices"][key] = {"voice_id": voice_id, "clip": clip}
            self.__write(data)

    def pop(self, key: str) -> dict[str, str] | None:
        with self.__lock:
            data = self.__read()
            found = data["voices"].pop(key, None)
            if found is not None:
                self.__write(data)
            return found

    def pop_voice_id(self, voice_id: str) -> list[str]:
        """Forgets a voice the service no longer has. Returns the voices that used it"""
        with self.__lock:
            data = self.__read()
            keys = [k for k, v in data["voices"].items() if v.get("voice_id") == voice_id]
            for k in keys:
                del data["voices"][k]
            if keys:
                self.__write(data)
            return keys

    def keys(self) -> list[str]:
        with self.__lock:
            return list(self.__read()["voices"].keys())

    def count(self) -> int:
        return len([k for k in self.keys() if not k.startswith(TRIAL_PREFIX)])

    def use_account(self, fingerprint: str) -> int:
        """The key in use now. Voices from another key's account are forgotten (they can't be used or deleted with this key).
        Returns how many were forgotten"""
        with self.__lock:
            data = self.__read()
            if data.get("account") == fingerprint:
                return 0
            forgotten = len(data["voices"]) if data.get("account") else 0
            self.__write({"account": fingerprint, "voices": {} if data.get("account") else data["voices"]})
            return forgotten

    def __read(self) -> dict[str, Any]:
        try:
            with open(self.__path, encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict) and isinstance(data.get("voices"), dict):
                return data
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            logging.warning(f"Could not read {self.__path}, so its voices are cloned again: {e}")
        return {"account": "", "voices": {}}

    def __write(self, data: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.__path), exist_ok=True)
        temp = self.__path + ".tmp"
        with open(temp, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        os.replace(temp, self.__path)


_stores: dict[str, ClonedVoices] = {}
_stores_lock = threading.Lock()


def cloned_voices(save_folder: str, service_name: str) -> ClonedVoices:
    """One store per service and folder, shared by every copy of the service and the Voice Clips page"""
    file_name = "".join(c for c in service_name.lower() if c.isalnum()) + ".json"
    path = os.path.join(voice_clips_folder(save_folder), FOLDER, file_name)
    with _stores_lock:
        if path not in _stores:
            _stores[path] = ClonedVoices(path)
        return _stores[path]


class VoiceKeeper:
    """What the Voice Clips page's buttons do to the voices one online service keeps. Deleting on the service happens in the background,
    so the page never waits for the internet. *delete(api_key, voice_id)* deletes one voice on the service"""

    def __init__(self, service_name: str, key_file: str, delete: Callable[[str, str], None]) -> None:
        self.service_name = service_name
        self.key_file = key_file
        self.__delete = delete

    def __store(self, save_folder: str) -> ClonedVoices:
        return cloned_voices(save_folder, self.service_name)

    def delete_later(self, voice_ids: list[str], api_key: str | None = None) -> threading.Thread | None:
        voice_ids = [v for v in voice_ids if v]
        if not voice_ids:
            return None
        def run(key: str | None):
            if key is None:
                from src.tts.voice_setups import read_key_file
                key = read_key_file(self.key_file)
            if not key:
                return
            for voice_id in voice_ids:
                try:
                    self.__delete(key, voice_id)
                except Exception as e:
                    logging.warning(f"{self.service_name}: could not delete a voice Mantella no longer uses ({voice_id}). "
                                    f"You can delete it on the {self.service_name} website: {e}")
        thread = threading.Thread(target=run, args=(api_key,), name=f"{self.service_name}: delete voices", daemon=True)
        thread.start()
        return thread

    def use_trial_voice(self, save_folder: str, key: str, clip_path: str) -> None:
        """Clone from this clip, right after hearing it: the voice made to hear it becomes the voice NPCs use, so the clip isn't cloned again"""
        store = self.__store(save_folder)
        trial = store.pop(f"{TRIAL_PREFIX}{key}")
        if trial is None:
            return
        try:
            same = bool(clip_path) and trial.get("clip") == clip_hash(clip_path)
        except OSError:
            same = False
        if not same:
            self.delete_later([trial.get("voice_id", "")])
            return
        old = store.get(key)
        store.set(key, trial["voice_id"], trial["clip"])
        if old is not None and old.get("voice_id") != trial["voice_id"]:
            self.delete_later([old.get("voice_id", "")])

    def move_saved_voice(self, save_folder: str, from_key: str, to_key: str, clip_path: str) -> None:
        """An NPC's own voice used for their whole voice type: the voice moves with the clip, so it isn't cloned again"""
        store = self.__store(save_folder)
        moved = store.get(from_key)
        try:
            same = moved is not None and moved.get("clip") == clip_hash(clip_path)
        except OSError:
            same = False
        if not same:
            return
        store.pop(from_key)
        old = store.get(to_key)
        store.set(to_key, moved["voice_id"], moved["clip"])
        if old is not None and old.get("voice_id") != moved["voice_id"]:
            self.delete_later([old.get("voice_id", "")])

    def remove_saved_voices(self, save_folder: str, key: str) -> None:
        """A voice that is gone (an NPC's own voice removed): deleted on the service too, with its trial voice"""
        store = self.__store(save_folder)
        gone = [store.pop(key), store.pop(f"{TRIAL_PREFIX}{key}")]
        self.delete_later([g.get("voice_id", "") for g in gone if g])

    def remove_trial_voices(self, save_folder: str, only: str | None = None, keep: str | None = None) -> None:
        """The voices made to hear a clip from the list: all of them, or *only* one clip's, or all but *keep*'s"""
        store = self.__store(save_folder)
        gone = []
        for name in store.keys():
            if not name.startswith(TRIAL_PREFIX):
                continue
            key = name[len(TRIAL_PREFIX):]
            if (only is not None and key != only) or (keep is not None and key == keep):
                continue
            found = store.pop(name)
            if found:
                gone.append(found.get("voice_id", ""))
        self.delete_later(gone)
