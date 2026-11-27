"""The Voice effect list in the Bio Editor: one NPC's voice effect, saved in the voice_effect column of their character override"""
from __future__ import annotations

import pandas as pd

from src.tts import voice_effects
from src.tts.voice_effects import NO_EFFECT

COLUMN = "voice_effect"
AUTOMATIC = "" # the effect their race or being a ghost gives them, if that effect is ticked
NONE = "none"


def effect_choices(save_folder: str, current: str = "") -> list[tuple[str, str]]:
    """Automatic, no effect, then every effect by the name shown on the Voice Effects part of the Text-to-Speech tab.
    An effect that isn't ticked there says so: picking it gives no effect until it's ticked"""
    effects = voice_effects.shared_voice_effects(save_folder)
    choices = [("Automatic (by race or ghost, if that effect is ticked)", AUTOMATIC), ("No effect", NONE)]
    choices += [(e.title if e.on else f"{e.title} (turned off)", e.name) for e in effects.all()]
    current = (current or "").strip()
    if current and current.lower() not in {value.lower() for _, value in choices}:
        found = effects.find(current) # written by hand as the shown name, e.g. "Vampire Lord"
        choices.append((found.title if found else f"{current} (no effect by that name)", current))
    return choices


def picked_value(save_folder: str, written: str) -> str:
    """The list's value for what the column says: 'Ghost' and 'ghost' both pick the Ghost effect"""
    written = (written or "").strip()
    if not written:
        return AUTOMATIC
    if written.lower() in NO_EFFECT:
        return NONE
    found = voice_effects.shared_voice_effects(save_folder).find(written)
    return found.name if found else written


def _rows(df: pd.DataFrame, name: str, base_id: str, race: str) -> pd.Series | None:
    if df is None or df.empty or not name:
        return None
    try:
        return ((df["name"].fillna("").astype(str) == str(name)) & (df["base_id"].fillna("").astype(str) == str(base_id))
                & (df["race"].fillna("").astype(str) == str(race)))
    except KeyError:
        return None


def effect_of_row(df: pd.DataFrame, name: str, base_id: str, race: str, column: str = COLUMN) -> str:
    """What the NPC's voice_effect column (or another column) says now, all character files merged"""
    rows = _rows(df, name, base_id, race)
    if rows is None or not rows.any() or column not in df.columns:
        return ""
    value = df.loc[rows, column].iloc[0]
    return "" if value is None or pd.isna(value) or str(value).strip().lower() == "nan" else str(value).strip()


def set_in_table(df: pd.DataFrame, name: str, base_id: str, race: str, value: str, column: str = COLUMN) -> bool:
    """Writes the NPC's voice effect (or another column) into a character table in memory, so new conversations use it without Reload Character Data"""
    rows = _rows(df, name, base_id, race)
    if rows is None or not rows.any():
        return False
    if column not in df.columns:
        df[column] = ""
    df.loc[rows, column] = value
    return True
