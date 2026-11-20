"""The keys of the online speech services: one text file per service next to GPT_SECRET_KEY.txt, written by the Key box.
Keys are never put in config.ini and never written to the log"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from src import utils
from src.stt_backends.presets import STTPreset

STT_KEY_FILE = "STT_SECRET_KEY.txt" # the old shared speech key: only read once, when an old setup moves over (copy_old_key)
CHAT_KEY_FILE = "GPT_SECRET_KEY.txt"


def _places(file_name: str) -> list[Path]:
    """Where key files are looked for, the same as GPT_SECRET_KEY.txt: the mod folder first, then Mantella's own folder"""
    return [
        Path(utils.resolve_path()).parent.parent.parent / file_name,
        Path(utils.resolve_path()) / file_name,
        Path(file_name),
    ]


def find_key_file(file_name: str) -> Path | None:
    if not file_name:
        return None
    for place in _places(file_name):
        try:
            if place.is_file():
                return place
        except OSError:
            continue
    return None


def read_key(file_name: str) -> str:
    place = find_key_file(file_name)
    if place is None:
        return ""
    try:
        text = place.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return text.splitlines()[0].strip() if text else ""


def key_for(preset: STTPreset, llm_api: str = "", chat_key_file: str = CHAT_KEY_FILE) -> tuple[str, str]:
    """The key to send to this preset, and the file it came from. Empty for a program that needs none.
    Its own file, then the chat key only when the chat AI uses the same service, so it never goes anywhere else.
    STT_SECRET_KEY.txt is not read: as a backup for every service it would send a Groq key to Deepgram"""
    files = [preset.key_file]
    if preset.chat_service and (llm_api or "").strip().lower() == preset.chat_service.lower():
        files.append(chat_key_file)
    for file_name in files:
        key = read_key(file_name)
        if key:
            return key, file_name
    return "", ""


def copy_old_key(file_name: str, old_key_file: str = STT_KEY_FILE) -> bool:
    """Someone updating: the old shared STT_SECRET_KEY.txt becomes the key of the service they were using, unless it has one already"""
    if not file_name or find_key_file(file_name) is not None:
        return False
    key = read_key(old_key_file)
    return bool(key) and save_key(file_name, key) is not None


def save_key(file_name: str, key: str, chat_key_file: str = CHAT_KEY_FILE) -> Path | None:
    """Writes the key into the folder that holds GPT_SECRET_KEY.txt (Mantella's own folder if there is none).
    An empty key deletes the service's own file. Returns where it went"""
    chat = find_key_file(chat_key_file)
    folder = chat.parent if chat is not None else Path(utils.resolve_path())
    existing = find_key_file(file_name)
    target = existing if existing is not None else folder / file_name
    key = (key or "").strip()
    try:
        if not key:
            if existing is not None:
                os.remove(existing)
            return None
        target.write_text(key + "\n", encoding="utf-8")
        logging.info(f"Saved the key in {target.name}")
        return target
    except OSError as e:
        logging.error(f"Could not save the key in {target}: {e}")
        return None
