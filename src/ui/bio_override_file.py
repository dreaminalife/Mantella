"""Saving an NPC's bio from the Bio Editor (the Save button) without losing anything in the override file.

- every cell is read as the text it is in the file, so `013385` keeps its leading zero and `NA` stays `NA`
- the file keeps its encoding and line endings, and is copied to `<file>.bak` before it's written again
- it's written to a temporary file that is then swapped in, so it's never left empty or cut off
- a file that can't be read is left as it is, and nothing is saved
- one write at a time, shared with the bios saved for NPCs met in game (new_npc_bios), so neither write loses the other's row

Which cells are written is unchanged from before: the NPC's row (same name, base ID and race) gets the bio, the tags
from the Tags box, and the other cells passed in; with no such row, a new row is added at the end.
"""
from __future__ import annotations

import json
import os
import shutil
from typing import Callable, Dict

import pandas as pd

from src.bio_template_manager import file_encoding
from src.games import new_npc_bios

REQUIRED_COLUMNS = ["name", "base_id", "race", "bio"]
# A JSON entry only ever got these of the other cells written (a CSV row gets all of them), as before
JSON_CELLS = ("ref_id", "tags_overwrite", "voice_effect", "tts_service")


class NotSaved(Exception):
    """Why the bio wasn't saved, in words for the player. The file wasn't changed"""


def save_npc(path: str, name: str, base_id: str, race: str, bio: str, other_cells: Dict[str, str | None],
             tags: str | None, refuse_if: Callable[[pd.DataFrame | None], str | None] | None = None) -> str | None:
    """Write the NPC's bio, tags (None leaves them as they are) and other cells (None skips a cell) to the file at path.
    Returns the backup's path, or None for a new file. Raises NotSaved, with the file unchanged, when it can't be read.

    refuse_if is shown the NPCs already in the file (None for a new or empty file), read during the same write, so nothing
    can be added in between. When it returns a reason, NotSaved is raised with it and nothing is written"""
    # The Bio Editor's list says `nan` for an empty base ID or race; written as `nan`, a JSON entry would no longer match its NPC
    name, base_id, race = _blank(name), _blank(base_id), _blank(race)
    with new_npc_bios._write_lock:
        if os.path.splitext(path)[1].lower() == ".json":
            return _save_json(path, name, base_id, race, bio, other_cells, tags, refuse_if)
        return _save_csv(path, name, base_id, race, bio, other_cells, tags, refuse_if)


def _blank(value) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def _same_npc(row_name, row_base_id, row_race, name: str, base_id: str, race: str) -> bool:
    """The same NPC as the game sees it: case and leading zeros in the base ID don't count, and `nan` is empty.
    So a row whose base ID the list shows without its leading zero (`13385` for `013385`) is updated, not added again"""
    return (_blank(row_name).lower() == name.lower() and _blank(row_race).lower() == race.lower()
            and _blank(row_base_id).lstrip("0").lower() == base_id.lstrip("0").lower())


def read_cell(path: str, name: str, base_id: str, race: str, column: str) -> str:
    """What the NPC's row in the file says in this column, found the way save_npc finds it, so the Tags box shows what
    Save will write over. Empty when the file, the row or the cell isn't there. Raises NotSaved when the file can't be read"""
    name, base_id, race = _blank(name), _blank(base_id), _blank(race)
    if not _has_content(path):
        return ""
    try:
        if os.path.splitext(path)[1].lower() == ".json":
            with open(path, "r", encoding="utf-8-sig") as file:
                content = json.load(file)
            rows = [content] if isinstance(content, dict) else content if isinstance(content, list) else []
            rows = [row for row in rows if isinstance(row, dict)]
        else:
            rows = pd.read_csv(path, dtype=str, keep_default_na=False, engine="python",
                               encoding=file_encoding(path)).to_dict("records")
    except Exception as e:
        raise _cannot_read(path, e)
    for row in rows:
        if _same_npc(row.get("name"), row.get("base_id"), row.get("race"), name, base_id, race):
            return _blank(row.get(column))
    return ""


def _cannot_read(path: str, error: Exception) -> NotSaved:
    return NotSaved(f"Couldn't read `{os.path.basename(path)}` ({error}), so nothing was saved. Fix or move the file and try again.")


def _has_content(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > 0


def _backup(path: str) -> str:
    backup = path + new_npc_bios.BACKUP_SUFFIX
    shutil.copy2(path, backup)
    return backup


def _make_folder(path: str):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def _save_csv(path, name, base_id, race, bio, other_cells, tags, refuse_if=None) -> str | None:
    existed = _has_content(path)
    if existed:
        try:
            encoding = file_encoding(path)
            df = pd.read_csv(path, dtype=str, keep_default_na=False, engine="python", encoding=encoding)
            with open(path, "rb") as file:
                newline = new_npc_bios._newline_of(file.read())
        except Exception as e:
            raise _cannot_read(path, e)
    else:
        df, encoding, newline = pd.DataFrame(columns=REQUIRED_COLUMNS), "utf-8", "\r\n"

    if refuse_if is not None:
        problem = refuse_if(df if existed else None)
        if problem:
            raise NotSaved(problem)

    for column in REQUIRED_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    for column, value in other_cells.items():
        if value is not None and column not in df.columns:
            df[column] = ""
    if tags is not None and "tags" not in df.columns:
        df["tags"] = ""

    rows = pd.Series([_same_npc(n, b, r, name, base_id, race) for n, b, r in zip(df["name"], df["base_id"], df["race"])],
                     index=df.index, dtype=bool)
    if rows.any():
        df.loc[rows, "bio"] = bio
        if tags is not None:
            df.loc[rows, "tags"] = tags
        for column, value in other_cells.items():
            if value is not None and column != "tags":
                df.loc[rows, column] = value
    else:
        cells = {column: "" for column in df.columns}
        cells.update({"name": name, "base_id": base_id, "race": race, "bio": bio})
        for column, value in other_cells.items():
            if value is not None and not (column == "tags" and tags is not None):
                cells[column] = value
        if tags is not None:
            cells["tags"] = tags
        df = pd.concat([df, pd.DataFrame([cells], columns=df.columns)], ignore_index=True)

    # Columns without a header (e.g. from trailing commas) are read as "Unnamed: N"; keep them without a header
    headers = ["" if str(column).startswith("Unnamed: ") else column for column in df.columns]
    text = df.to_csv(index=False, header=headers, lineterminator=newline)
    try:
        data = text.encode("utf-8" if encoding.lower() == "ascii" else encoding)
    except (UnicodeEncodeError, LookupError):
        data = text.encode("utf-8")
    backup = _backup(path) if existed else None
    if not existed:
        _make_folder(path)
    new_npc_bios._replace_file(path, data)
    return backup


def _save_json(path, name, base_id, race, bio, other_cells, tags, refuse_if=None) -> str | None:
    existed = _has_content(path)
    was_single = False
    if existed:
        try:
            with open(path, "r", encoding="utf-8-sig") as file:
                content = json.load(file)
        except Exception as e:
            raise _cannot_read(path, e)
        was_single = isinstance(content, dict)
        if was_single:
            items = [content]
        elif isinstance(content, list):
            items = content
        else:
            raise NotSaved(f"`{os.path.basename(path)}` holds neither an NPC nor a list of NPCs, so nothing was saved.")
    else:
        items = []
    if refuse_if is not None:
        entries = [item for item in items if isinstance(item, dict)]
        problem = refuse_if(pd.DataFrame(entries) if entries else None)
        if problem:
            raise NotSaved(problem)

    for content in items:
        if isinstance(content, dict) and _same_npc(content.get("name"), content.get("base_id"), content.get("race"), name, base_id, race):
            content["bio"] = bio
            for column in JSON_CELLS:
                if other_cells.get(column) is not None:
                    content[column] = other_cells[column]
            if tags is not None:
                content["tags"] = tags
            break
    else:
        entry = {"name": name, "base_id": base_id, "race": race, "bio": bio}
        for column in JSON_CELLS:
            if other_cells.get(column) is not None:
                entry[column] = other_cells[column]
        if tags is not None:
            entry["tags"] = tags
        elif other_cells.get("tags") is not None:
            entry["tags"] = other_cells["tags"]
        items.append(entry)

    to_write = items[0] if was_single and len(items) == 1 else items
    backup = _backup(path) if existed else None
    if not existed:
        _make_folder(path)
    # Plain ASCII (other letters written as \u codes): the game reads override JSON files in the system's encoding
    new_npc_bios._replace_file(path, json.dumps(to_write, indent=2).encode("utf-8"))
    return backup
