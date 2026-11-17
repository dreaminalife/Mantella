"""Sharing voice clip picks: which game line each voice type and each NPC with a clip of their own uses, and the lines turned down.
The file holds no sound: each player's Mantella copies the lines from their own game files, so voice replacers still apply.
"""
from __future__ import annotations

from typing import Any

from src.voice_clips.clip_store import NPCS, VOICE_TYPES, ClipStore
from src.voice_clips.requests import IMPORT, ClipRequests

FORMAT = "mantella_voice_picks"
KEEP_MINE = "keep mine"
TAKE_THEIRS = "take theirs"


def export_picks(store: ClipStore) -> dict[str, Any]:
    picks: dict[str, Any] = {FORMAT: 1, VOICE_TYPES: {}, NPCS: {}}
    for kind in (VOICE_TYPES, NPCS):
        for entry in store.entries(kind):
            info = entry.info
            if not info.path:
                continue
            pick = {"name": info.name, "line": info.path, "turned_down": list(info.turned_down)}
            if kind == NPCS:
                pick.update({"plugin": info.npc_plugin, "local_id": info.npc_local_id, "voice_type": info.voice_type})
            picks[kind][entry.key] = pick
    return picks


def import_picks(store: ClipStore, requests: ClipRequests, picks: dict[str, Any], when_different: str) -> tuple[int, int]:
    """Asks the game for the lines in someone's picks. Returns (lines asked for, picks of your own kept)"""
    if not isinstance(picks, dict) or FORMAT not in picks:
        raise ValueError("This isn't a Mantella voices file.")
    asked = kept = 0
    for kind in (VOICE_TYPES, NPCS):
        for key, pick in (picks.get(kind) or {}).items():
            line = str(pick.get("line", ""))
            if not line:
                continue
            mine = store.info(kind, key)
            has_mine = store.clip(kind, key) is not None
            turned_down = list(dict.fromkeys(list(mine.turned_down) + list(pick.get("turned_down", []))))
            if has_mine and mine.path.lower() == line.lower():
                mine.turned_down = turned_down
                store.save_info(kind, key, mine)
                continue
            if has_mine and when_different == KEEP_MINE:
                kept += 1
                continue
            for_value = "npc" if kind == NPCS else "voice_type"
            voice_type = str(pick.get("voice_type", "") if kind == NPCS else pick.get("name", "") or key)
            requests.ask_for_lines(IMPORT, [{"path": line, "for": for_value}], voice_type, str(pick.get("plugin", "")) if kind == NPCS else "",
                                   str(pick.get("local_id", "")) if kind == NPCS else "", str(pick.get("name", "")), turned_down=turned_down)
            asked += 1
    return asked, kept
