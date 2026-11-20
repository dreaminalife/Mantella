"""Sharing voices with a friend: which game lines each voice type and each NPC with a voice of their own is cloned from, in order,
and the lines left out. The file holds no sound: each player's Mantella copies the lines from their own game files, so voice
replacers still apply.
"""
from __future__ import annotations

from typing import Any

from src.tts.voice_style import shared_values
from src.voice_clips.clip_store import MOST_PITCH, NPCS, PITCH, VOICE_TYPES, ClipStore
from src.voice_clips.requests import IMPORT, ClipRequests

FORMAT = "mantella_voice_picks"
KEEP_MINE = "keep mine"
TAKE_THEIRS = "take theirs"


def export_picks(store: ClipStore) -> dict[str, Any]:
    picks: dict[str, Any] = {FORMAT: 3, VOICE_TYPES: {}, NPCS: {}} # 3: a style setting's value may be by model
    for kind in (VOICE_TYPES, NPCS):
        for entry in store.entries(kind):
            info = entry.info
            if not info.path:
                continue
            pick = {"name": info.name, "line": info.path, "lines": [line.path for _, line in store.voice_lines(kind, entry.key)[1:] if line.path],
                    "turned_down": list(info.turned_down)}
            if store.pitch(kind, entry.key):
                pick["pitch"] = store.pitch(kind, entry.key)
            style = {name: value for name, value in store.voice_settings(kind, entry.key).items() if name != PITCH}
            if style:
                pick["style"] = style
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
            more = [str(l) for l in pick.get("lines", []) if l] if isinstance(pick.get("lines"), list) else []
            mine = store.info(kind, key)
            has_mine = store.clip(kind, key) is not None
            turned_down = list(dict.fromkeys(list(mine.turned_down) + list(pick.get("turned_down", []))))
            try:
                theirs_pitch = float(pick.get("pitch", 0) or 0)
            except (TypeError, ValueError):
                theirs_pitch = 0.0
            theirs_pitch = round(max(-MOST_PITCH, min(MOST_PITCH, theirs_pitch)), 2)
            theirs = {**shared_values(pick.get("style")), **({PITCH: theirs_pitch} if theirs_pitch else {})}
            if has_mine and [l.path.lower() for _, l in store.voice_lines(kind, key)] == [l.lower() for l in [line, *more]]:
                mine.turned_down = turned_down
                store.save_info(kind, key, mine)
                if when_different == TAKE_THEIRS:
                    store.set_voice_settings(kind, key, theirs)
                continue
            if has_mine and when_different == KEEP_MINE:
                kept += 1
                continue
            store.set_voice_settings(kind, key, theirs) # their voice sounds the way they set it
            for_value = "npc" if kind == NPCS else "voice_type"
            voice_type = str(pick.get("voice_type", "") if kind == NPCS else pick.get("name", "") or key)
            requests.ask_for_lines(IMPORT, [{"path": l, "for": for_value} for l in [line, *more]], voice_type, str(pick.get("plugin", "")) if kind == NPCS else "",
                                   str(pick.get("local_id", "")) if kind == NPCS else "", str(pick.get("name", "")), turned_down=turned_down)
            asked += 1
    return asked, kept
