"""Vanilla Dialogues: the heard lines each NPC keeps, next to their summaries, one set per playthrough"""
from abc import ABC, abstractmethod
import json
import os
import threading
from typing import Any
from src.config.config_loader import ConfigLoader
from src.games.gameable import Gameable
from src.heard_lines.heard_line import GameTime, HeardLine, HeardPerson
from src.quest_tags.history import write_json
from src.remember.npc_folder import NpcFolder
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VANILLA_DIALOGUES)


class HeardLinesStore(ABC):
    @abstractmethod
    def note_now(self, world_id: str, now: GameTime) -> None:
        """The game's time, sent with a hand-over. Game time never goes back within one save, so a time earlier than the latest
        one seen in this playthrough means an older save was loaded: every NPC loses the lines from after it"""
        pass

    @abstractmethod
    def add(self, world_id: str, listener: HeardPerson, lines: list[HeardLine]) -> None:
        """Keeps lines the NPC heard. A repeat of a line still kept (same speaker, same words, and for menu talk the same
        question from the player) is dropped"""
        pass

    @abstractmethod
    def lines_for(self, world_id: str, name: str, ref_id: str) -> list[HeardLine]:
        """The lines the NPC keeps, oldest first, within Vanilla Dialogue Memory (Days) and Most Vanilla Dialogue Lines per NPC"""
        pass

    @abstractmethod
    def latest_now(self, world_id: str) -> GameTime | None:
        """The latest game time seen in this playthrough, which the day words and the days kept are measured against"""
        pass


class JsonHeardLinesStore(HeardLinesStore):
    """One heard_lines.json in each NPC's folder, and the latest game time in <world_id>/heard_lines_now.json.
    The limits are read from the config each time, so changed settings apply at once"""
    FILE_NAME: str = "heard_lines.json"
    NOW_FILE_NAME: str = "heard_lines_now.json"
    # One lock for every store: the NPC Initiative and NPC Event checks run alongside other requests, and a settings reload
    # briefly leaves two stores
    _lock: threading.Lock = threading.Lock()

    def __init__(self, game: Gameable, config: ConfigLoader):
        self.__game: Gameable = game
        self.__config: ConfigLoader = config

    def note_now(self, world_id: str, now: GameTime) -> None:
        with JsonHeardLinesStore._lock:
            latest = self.__read_now(world_id)
            if latest is not None and now.day == latest.day:
                return
            if latest is not None and now.day < latest.day:
                self.__forget_after(world_id, now)
            write_json(self.__now_path(world_id), {"day": now.day, "hour": now.hour})

    def add(self, world_id: str, listener: HeardPerson, lines: list[HeardLine]) -> None:
        if not lines:
            return
        with JsonHeardLinesStore._lock:
            path = self.__npc_path(world_id, listener.name, listener.ref_id)
            kept = self.__still_kept(self.__read_lines(path), self.__read_now(world_id))
            for line in sorted(lines, key=lambda l: l.time):
                if any(k.speaker.ref_id == line.speaker.ref_id and k.words == line.words and k.asked == line.asked for k in kept):
                    continue
                kept.append(line)
            kept = self.__newest(kept)
            write_json(path, {"lines": [line.to_dict() for line in kept]})

    def lines_for(self, world_id: str, name: str, ref_id: str) -> list[HeardLine]:
        with JsonHeardLinesStore._lock:
            path = self.__npc_path(world_id, name, ref_id)
            if not os.path.exists(path):
                return []
            return self.__newest(self.__still_kept(self.__read_lines(path), self.__read_now(world_id)))

    def latest_now(self, world_id: str) -> GameTime | None:
        with JsonHeardLinesStore._lock:
            return self.__read_now(world_id)

    def __still_kept(self, lines: list[HeardLine], now: GameTime | None) -> list[HeardLine]:
        """Without the lines older than Vanilla Dialogue Memory (Days). All of them while the game's time is unknown"""
        if now is None:
            return list(lines)
        days_kept = self.__config.heard_lines_days_kept
        return [line for line in lines if now.day - line.game_time.day <= days_kept]

    def __newest(self, lines: list[HeardLine]) -> list[HeardLine]:
        """The newest Most Vanilla Dialogue Lines per NPC, oldest first"""
        most = max(1, self.__config.heard_lines_most_per_npc)
        return sorted(lines, key=lambda l: l.time)[-most:]

    def __forget_after(self, world_id: str, now: GameTime):
        """An older save was loaded: every NPC of this playthrough loses the lines from after it"""
        world_folder = os.path.join(self.__game.conversation_folder_path, world_id)
        if not os.path.isdir(world_folder):
            return
        forgotten = 0
        for folder_name in os.listdir(world_folder):
            path = os.path.join(world_folder, folder_name, JsonHeardLinesStore.FILE_NAME)
            if not os.path.exists(path):
                continue
            lines = self.__read_lines(path)
            kept = [line for line in lines if line.game_time.day <= now.day]
            if len(kept) != len(lines):
                forgotten += len(lines) - len(kept)
                write_json(path, {"lines": [line.to_dict() for line in kept]})
        if forgotten:
            log.info(f"Vanilla Dialogues: an older save was loaded, so {forgotten} heard lines from after it were forgotten")

    def __npc_path(self, world_id: str, name: str, ref_id: str) -> str:
        return os.path.join(NpcFolder(self.__game.conversation_folder_path).path(world_id, name, ref_id), JsonHeardLinesStore.FILE_NAME)

    def __now_path(self, world_id: str) -> str:
        return os.path.join(self.__game.conversation_folder_path, world_id, JsonHeardLinesStore.NOW_FILE_NAME)

    def __read_now(self, world_id: str) -> GameTime | None:
        return GameTime.from_game(_read_json(self.__now_path(world_id)))

    @staticmethod
    def __read_lines(path: str) -> list[HeardLine]:
        data = _read_json(path)
        items = data.get("lines") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        return [line for line in (HeardLine.from_dict(item) for item in items) if line]


def _read_json(path: str) -> Any:
    """The file's contents, or None when it is missing or broken (logged)"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        log.warning(f"Vanilla Dialogues: could not read {path} ({e})")
        return None
