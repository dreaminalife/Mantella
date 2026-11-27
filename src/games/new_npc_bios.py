"""Bios for NPCs who have none: the first time the player talks to one, Mantella saves the short bio it made for them
("You are a female Breton Aveline.") in the file the Bio Editor saves to. They then show up in the Bio Editor, and an edit there sticks.

Only NPCs the game marks as one of a kind are saved: names shared by many people (Bandit, Whiterun Guard) are skipped, so editing
one entry never changes a whole crowd.

The file is only ever added to: an existing row is never changed, and nothing is written when the file can't be read.
"""
import csv
import io
import json
import os
import shutil
import threading

import pandas as pd

from src.bio_template_manager import file_encoding

SETTING = "save_bios_for_new_npcs"
DEFAULT_FILE_NAME = "character_overrides.csv"
BACKUP_SUFFIX = ".bak"

_write_lock = threading.Lock() # one write at a time, so two NPCs joining together can't both append to an old copy


def bio_editor_file(chosen_path: str | None, personal_folder: str) -> str:
    """The file the Bio Editor saves bios to: the Override path box, or `character_overrides.csv` in the personal override folder.
    - A folder: `<folder>/character_overrides.csv`.
    - A file name without a folder: that file in the personal override folder.
    - A file without an extension: `.csv` is added.
    Creates the folder if it's missing."""
    path = (chosen_path or "").strip()
    if path:
        try:
            if path.endswith(os.sep) or os.path.isdir(path):
                folder = path if os.path.isdir(path) else path.rstrip(os.sep)
                if folder:
                    os.makedirs(folder, exist_ok=True)
                return os.path.join(folder or personal_folder, DEFAULT_FILE_NAME)
            folder = os.path.dirname(path)
            final_path = path if folder else os.path.join(personal_folder, os.path.basename(path))
            os.makedirs(os.path.dirname(final_path) or personal_folder, exist_ok=True)
            if not os.path.splitext(final_path)[1]:
                final_path += ".csv"
            return final_path
        except Exception:
            pass
    try:
        os.makedirs(personal_folder, exist_ok=True)
    except Exception:
        pass
    return os.path.join(personal_folder, DEFAULT_FILE_NAME)


def _clean_id(value) -> str:
    """A form ID as it is compared when the game looks an NPC up: leading zeros don't count, case doesn't matter"""
    text = str(value or "").strip()
    if text.lower() in ("nan", "none"):
        return ""
    return text.lstrip("0").lower()


def has_entry(rows: list[dict], name: str, base_id: str) -> bool:
    """Whether a row is already there for this NPC: the same name, and the same base ID or none"""
    wanted_name = name.strip().lower()
    wanted_id = _clean_id(base_id)
    for row in rows:
        if str(row.get("name", "") or "").strip().lower() != wanted_name:
            continue
        row_id = _clean_id(row.get("base_id", ""))
        if not row_id or not wanted_id or row_id == wanted_id:
            return True
    return False


def add_entry(path: str, entry: dict[str, str]) -> bool:
    """Add one NPC to the CSV or JSON file at path. True if it was added; False if the file already has them.
    Raises if the file can't be read or written, and then the file is unchanged."""
    with _write_lock:
        if os.path.splitext(path)[1].lower() == ".json":
            return _add_to_json(path, entry)
        return _add_to_csv(path, entry)


# --- CSV ---

def _newline_of(data: bytes) -> str:
    return "\n" if b"\n" in data and b"\r\n" not in data else "\r\n"


def _csv_line(values: list[str], newline: str) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator=newline).writerow(values)
    return buffer.getvalue()


def _add_to_csv(path: str, entry: dict[str, str]) -> bool:
    columns = list(entry.keys())
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        _write_new_file(path, (_csv_line(columns, "\r\n") + _csv_line([entry[c] for c in columns], "\r\n")).encode("utf-8"))
        return True

    encoding = file_encoding(path)
    # Every cell as the text it is in the file, so nothing read here can change what's written back
    df = pd.read_csv(path, dtype=str, keep_default_na=False, engine="python", encoding=encoding)
    if has_entry(df.to_dict("records"), entry["name"], entry["base_id"]):
        return False
    with open(path, "rb") as file:
        data = file.read()
    newline = _newline_of(data)

    if all(column in df.columns for column in columns):
        # Every column is there: the row goes at the end, and the rest of the file is left as it is
        line = _csv_line([entry.get(str(column), "") for column in df.columns], newline)
        append_encoding = "utf-8" if encoding.lower() in ("utf-8-sig", "ascii") else encoding
        try:
            encoded = line.encode(append_encoding)
        except (UnicodeEncodeError, LookupError):
            encoded = None
        if encoded is not None:
            if data and not data.endswith(b"\n"):
                encoded = newline.encode("ascii") + encoded
            with open(path, "ab") as file:
                file.write(encoded)
            return True

    # A column is missing (or the name can't be written in the file's encoding): the file is written again with it, after a backup
    for column in columns:
        if column not in df.columns:
            df[column] = ""
    df.loc[len(df.index)] = [entry.get(str(column), "") for column in df.columns]
    headers = ["" if str(column).startswith("Unnamed: ") else column for column in df.columns]
    text = df.to_csv(index=False, header=headers, lineterminator=newline)
    try:
        encoded = text.encode("utf-8" if encoding.lower() == "ascii" else encoding)
    except (UnicodeEncodeError, LookupError):
        encoded = text.encode("utf-8")
    shutil.copy2(path, path + BACKUP_SUFFIX)
    _replace_file(path, encoded)
    return True


# --- JSON ---

def _add_to_json(path: str, entry: dict[str, str]) -> bool:
    items: list = []
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "r", encoding="utf-8-sig") as file:
            loaded = json.load(file) # raises when the file can't be read, and nothing is written
        if isinstance(loaded, dict):
            items = [loaded]
        elif isinstance(loaded, list):
            items = loaded
        else:
            raise ValueError(f"{path} holds neither an NPC nor a list of NPCs")
        if has_entry([item for item in items if isinstance(item, dict)], entry["name"], entry["base_id"]):
            return False
        shutil.copy2(path, path + BACKUP_SUFFIX)
        _replace_file(path, json.dumps(items + [entry], ensure_ascii=False, indent=2).encode("utf-8"))
        return True
    _write_new_file(path, json.dumps([entry], ensure_ascii=False, indent=2).encode("utf-8"))
    return True


# --- Writing ---

def _write_new_file(path: str, data: bytes):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    _replace_file(path, data)


def _replace_file(path: str, data: bytes):
    """Write a temporary file next to it, then swap it in: if writing stops halfway, the file is never left cut off"""
    temporary = path + ".tmp"
    try:
        with open(temporary, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
