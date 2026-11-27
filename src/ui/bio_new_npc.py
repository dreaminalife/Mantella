"""Adding a new NPC in the Bio Editor: a name (and, if you have them, a base ID and race) that gets its own bio.

Nothing that's already there is ever changed or lost:
- a new NPC is refused when the game would merge it into an NPC that already has a bio (same name, and a matching
  or empty base ID, and the same or an empty race), because that would overwrite their bio when the game loads
- a name that is one bio for everyone with it (Bandit) never gets a second row: the game would no longer find any of them
- the row is written by bio_override_file, like Save: every cell kept as its exact text, a `<file>.bak` copy first,
  one write at a time (shared with the bios saved for NPCs met in game), through a temporary file, and a file it can't
  read is left as it is
"""
from __future__ import annotations

import os
import re
from typing import Dict, List

import pandas as pd

import src.utils as utils
from src.ui import bio_override_file

_HEX = re.compile(r"^[0-9A-F]+$")
# The loader matches a base ID on its last 6 characters, then on its last 5, 4 and 3
_ID_LENGTHS = (6, 5, 4, 3)


NotSaved = bio_override_file.NotSaved # why the new NPC wasn't saved, in words for the player. Nothing was written


def clean_name(text: str | None) -> str:
    return utils.normalize_apostrophes((text or "").strip())


def clean_base_id(text: str | None) -> str:
    """`0x1a694` → `1A694`. Empty stays empty. Raises ValueError for anything that isn't a hex number"""
    value = (text or "").strip().replace(" ", "").upper()
    if value.startswith("0X"):
        value = value[2:]
    if value and not _HEX.match(value):
        raise ValueError(value)
    return value


def race_choices(df: pd.DataFrame | None) -> List[str]:
    if df is None or "race" not in getattr(df, "columns", []):
        return []
    races = {str(value).strip() for value in df["race"].tolist() if value is not None and not pd.isna(value)}
    return sorted(race for race in races if race and race.lower() != "nan")


def _text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none") else text # an empty cell, as an old Save could write it


def _id_part(base_id: str, length: int) -> str:
    return base_id[-length:].lstrip("0").lower()


def _same_id(existing: str, new: str) -> bool:
    """Whether the game would take these for the same NPC's base ID (full or partial match, leading zeros ignored)"""
    if not existing:
        return False
    for length in _ID_LENGTHS:
        if length != _ID_LENGTHS[0] and len(new) < length:
            continue
        if _id_part(existing, length) == _id_part(new, length):
            return True
    return False


def clashing_npc(df: pd.DataFrame | None, name: str, base_id: str, race: str) -> str | None:
    """The name of an NPC in this table that a new row with these values would overwrite when the game loads, or None.

    Same rule as the game's override loader (Gameable.__validate_override_match), a little stricter: the same name,
    and the base ID empty or matching, and the race empty or the same, all ignoring case"""
    if df is None or df.empty or "name" not in df.columns:
        return None
    name_key = clean_name(name).lower()
    race_key = (race or "").strip().lower()
    for _, row in df.iterrows():
        existing_name = _text(row.get("name"))
        if clean_name(existing_name).lower() != name_key:
            continue
        if base_id and not _same_id(_text(row.get("base_id")), base_id):
            continue
        if race_key and _text(row.get("race")).lower() != race_key:
            continue
        return existing_name
    return None


def shared_bio_npc(df: pd.DataFrame | None, name: str) -> str | None:
    """The name of a bio shared by everyone with this name (a row with no base ID, like Bandit or Whiterun Guard), or None.

    The game finds those NPCs by name alone, which only works while exactly one row has that name. A second row with it,
    whatever its base ID or race, would leave everyone with that name without a bio"""
    if df is None or df.empty or "name" not in df.columns:
        return None
    name_key = clean_name(name).lower()
    for _, row in df.iterrows():
        existing_name = _text(row.get("name"))
        if clean_name(existing_name).lower() == name_key and not _text(row.get("base_id")).strip("0"):
            return existing_name
    return None


def clash_problem(df: pd.DataFrame | None, name: str, base_id: str, race: str, file_name: str | None = None) -> str | None:
    """What to tell the player when a new NPC with these values would change an NPC who has a bio, or None when it's safe"""
    where = f" in `{file_name}`" if file_name else ""
    shared = shared_bio_npc(df, name)
    if shared:
        return f"`{shared}`{where} has one bio for everyone with that name. Pick them from the list to change it."
    existing = clashing_npc(df, name, base_id, race)
    if existing:
        return clash_message(existing, file_name)
    return None


def new_row(name: str, base_id: str, race: str, bio: str, tags: str, voice_effect: str) -> Dict[str, str]:
    """The cells of the new NPC's row. Tags and voice effect only when they say something"""
    row = {"name": name, "base_id": base_id, "race": race, "bio": bio or ""}
    if (tags or "").strip():
        row["tags"] = tags.strip()
    if (voice_effect or "").strip():
        row["voice_effect"] = voice_effect.strip()
    return row


def clash_message(existing: str, file_name: str | None = None) -> str:
    where = f" in `{file_name}`" if file_name else ""
    return (f"`{existing}` already has a bio{where}. Pick them from the list, "
            "or type their base ID to add a different NPC with the same name.")


def add_row_to_file(path: str, row: Dict[str, str]) -> str | None:
    """Add the new NPC's row to this override file, keeping everything already in it (bio_override_file writes it).

    Returns the backup's path, or None for a new file. Raises NotSaved, with nothing written, when the file can't be read
    or already has an NPC the new one would overwrite.

    Only the overwrite rule is checked against the file, not the shared-bio rule: a row there with just a name usually
    adds to an NPC who has a base ID (an override for Lydia), and on_create checks shared bios in the merged list"""
    file_name = os.path.basename(path)

    def problem(rows: pd.DataFrame | None) -> str | None:
        if rows is None:
            return None
        if "name" not in rows.columns:
            return f"`{file_name}` has no `name` column, so it isn't a character file. Nothing was saved."
        existing = clashing_npc(rows, row["name"], row["base_id"], row["race"])
        return clash_message(existing, file_name) if existing else None

    other_cells = {column: value for column, value in row.items() if column not in ("name", "base_id", "race", "bio", "tags")}
    return bio_override_file.save_npc(path, row["name"], row["base_id"], row["race"], row["bio"], other_cells,
                                      row.get("tags"), refuse_if=problem)
