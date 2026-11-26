"""Vanilla Dialogues: the voiced lines NPCs heard in the game, as the MantellaHeardLines plugin hands them over.

The game add-on sends them, unparsed, under KEY_HEARDLINES with requests it already sends:
{"now": {"day": 42.37, "hour": 14.5}, "player": "Kit",
 "lines": [{"day": 42.30, "hour": 12.9, "time": 1763600000, "place": "The Bannered Mare", "scene": 123456, "menu": false,
            "speaker": {"id": 1234567, "name": "Mikael"}, "to": {"id": 7654321, "name": "Carlotta"},
            "words": "...", "asked": "", "heard_by": [{"id": 1234567, "name": "Mikael"}, ...], "player_there": true,
            "quest": {"id": "DialogueWhiterunBanneredMareScene1", "journal": false, "runs_once": false, "npcs_meet": true, "follows_quest": false},
            "scene_key": "Skyrim.esm:0ABCDE"}]}
"menu" marks menu talk. A scene line can have scene 0 too (the speaker had just left the scene), so the scene never decides it.
"quest" is the scene's quest, for scene lines only. "scene_key" names the scene the same way in every load order: the file that added
it and its form id in that file. Plugins from before they were sent leave them out.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
from typing import Any
import src.utils as utils
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VANILLA_DIALOGUES)

PLAYER_REF_ID = "000014" # The player's reference, masked like every other ref id


@dataclass(frozen=True)
class HeardPerson:
    """Someone who spoke, was spoken to or heard a line. ref_id is masked like load_character does"""
    ref_id: str
    name: str

    @property
    def is_player(self) -> bool:
        return self.ref_id == PLAYER_REF_ID

    @staticmethod
    def from_game(data: Any) -> HeardPerson | None:
        """From the plugin's {"id": <form id>, "name": "..."}. None when either is missing"""
        if not isinstance(data, dict):
            return None
        try:
            form_id = int(data["id"])
        except (KeyError, TypeError, ValueError):
            return None
        name = str(data.get("name") or "").strip()
        if not name:
            return None
        return HeardPerson(utils.mask_load_order(utils.convert_to_skyrim_hex_format(str(form_id))), name)

    def to_dict(self) -> dict[str, str]:
        return {"ref_id": self.ref_id, "name": self.name}

    @staticmethod
    def from_dict(data: Any) -> HeardPerson | None:
        """From the stored {"ref_id", "name"}"""
        if not isinstance(data, dict) or not data.get("ref_id") or not data.get("name"):
            return None
        return HeardPerson(str(data["ref_id"]), str(data["name"]))


@dataclass(frozen=True)
class GameTime:
    """The game's clock: days passed since the game started (the time of day is the fraction) and the hour (0 to 24)"""
    day: float
    hour: float

    @property
    def calendar_day(self) -> int:
        """Which day it is: the same number from midnight to midnight"""
        return round(self.day - self.hour / 24)

    @staticmethod
    def from_game(data: Any) -> GameTime | None:
        if not isinstance(data, dict):
            return None
        try:
            return GameTime(float(data["day"]), float(data["hour"]))
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class SceneQuest:
    """The quest a scene line belongs to, read from the quest's own settings in the game's data"""
    id: str # The quest's editor ID, such as DialogueWhiterunHeimskrPreachScene
    in_journal: bool # A quest the player can follow: filed under a journal category, or with journal objectives
    runs_once: bool # Run Once is ticked: it can't start again after it ends
    npcs_meet: bool # It starts when two NPCs meet
    follows_quest: bool = False # The quest or the scene checks how far a quest the player follows has got, like Enmon and Mena missing their daughter after The Heart of Dibella

    @property
    def is_small_talk_when_npcs_meet(self) -> bool:
        """Small talk Skyrim starts when two NPCs meet, like Carlotta and Olfina bickering. Never a quest the player follows, one that
        runs once, or one that follows a quest's progress: those are story, even when they start this way"""
        return self.npcs_meet and not self.in_journal and not self.runs_once and not self.follows_quest

    @staticmethod
    def from_dict(data: Any) -> SceneQuest | None:
        """From the plugin's, or a stored, {"id", "journal", "runs_once", "npcs_meet", "follows_quest"}. None when there is none"""
        if not isinstance(data, dict):
            return None
        return SceneQuest(str(data.get("id") or "").strip(), bool(data.get("journal", False)), bool(data.get("runs_once", False)), bool(data.get("npcs_meet", False)),
                          bool(data.get("follows_quest", False)))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "journal": self.in_journal, "runs_once": self.runs_once, "npcs_meet": self.npcs_meet, "follows_quest": self.follows_quest}


@dataclass(frozen=True)
class HeardLine:
    """One voiced line, and who heard it"""
    time: int # Real time (epoch seconds), the same clock as the summaries' ts= markers
    game_time: GameTime
    place: str
    scene: int # The scene's form id. 0 for menu talk, or when the speaker was in no scene
    menu: bool # Menu talk: the NPC answered the player in the dialogue menu
    speaker: HeardPerson
    to: HeardPerson | None # Who it was said to, if anyone
    words: str
    asked: str # Menu talk: the line the player picked. Empty for scene lines
    heard_by: tuple[HeardPerson, ...] # Never the player
    player_there: bool # The player was within Hearing Distance
    player: str # The player character's name
    quest: SceneQuest | None = None # Scene lines: the scene's quest. None for menu talk, and from plugins from before it was sent
    scene_key: str = "" # Scene lines: the scene as <file>:<6-hex form id>, such as Skyrim.esm:092A31. Empty for menu talk, outside a scene, and from older plugins

    @property
    def is_menu_talk(self) -> bool:
        return self.menu

    @staticmethod
    def from_game(data: Any, player: str) -> HeardLine | None:
        """From one of the plugin's lines. None when it has no speaker, words or game time"""
        if not isinstance(data, dict):
            return None
        speaker = HeardPerson.from_game(data.get("speaker"))
        words = str(data.get("words") or "").strip()
        game_time = GameTime.from_game(data)
        if not speaker or not words or not game_time:
            return None
        try:
            time = int(data.get("time") or 0)
            scene = int(data.get("scene") or 0)
        except (TypeError, ValueError):
            return None
        return HeardLine(time, game_time, str(data.get("place") or "").strip(), scene, bool(data.get("menu", False)), speaker, HeardPerson.from_game(data.get("to")), words,
                         str(data.get("asked") or "").strip(), _listeners(HeardPerson.from_game(p) for p in _as_list(data.get("heard_by"))),
                         bool(data.get("player_there", False)), player, SceneQuest.from_dict(data.get("quest")), str(data.get("scene_key") or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {"time": self.time, "day": self.game_time.day, "hour": self.game_time.hour, "place": self.place, "scene": self.scene, "menu": self.menu,
                "speaker": self.speaker.to_dict(), "to": self.to.to_dict() if self.to else None, "words": self.words, "asked": self.asked,
                "heard_by": [p.to_dict() for p in self.heard_by], "player_there": self.player_there, "player": self.player,
                "quest": self.quest.to_dict() if self.quest else None, "scene_key": self.scene_key}

    @staticmethod
    def from_dict(data: Any) -> HeardLine | None:
        """From a stored line. None when it is broken"""
        if not isinstance(data, dict):
            return None
        speaker = HeardPerson.from_dict(data.get("speaker"))
        game_time = GameTime.from_game(data)
        if not speaker or not game_time or not data.get("words"):
            return None
        try:
            return HeardLine(int(data.get("time") or 0), game_time, str(data.get("place") or ""), int(data.get("scene") or 0), bool(data.get("menu", False)), speaker,
                             HeardPerson.from_dict(data.get("to")), str(data["words"]), str(data.get("asked") or ""),
                             _listeners(HeardPerson.from_dict(p) for p in _as_list(data.get("heard_by"))), bool(data.get("player_there", False)),
                             str(data.get("player") or ""), SceneQuest.from_dict(data.get("quest")), str(data.get("scene_key") or ""))
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class HandOver:
    """Everything one request brought: the game's time now, and the lines caught since the request before"""
    now: GameTime | None # Missing while the game can't tell the time (loading). Then nothing is checked against it
    lines: tuple[HeardLine, ...]

    @staticmethod
    def parse(text: Any) -> HandOver | None:
        """None when the request brought nothing (the plugin isn't installed) or something unreadable, which is logged"""
        if not text or not isinstance(text, str) or not text.strip():
            return None
        try:
            data = json.loads(text)
        except ValueError as e:
            log.warning(f"Vanilla Dialogues: could not read the heard lines from the game ({e})")
            return None
        if not isinstance(data, dict):
            log.warning("Vanilla Dialogues: could not read the heard lines from the game (not a JSON object)")
            return None
        player = str(data.get("player") or "").strip()
        lines: list[HeardLine] = []
        for item in _as_list(data.get("lines")):
            line = HeardLine.from_game(item, player)
            if line:
                lines.append(line)
            else:
                log.debug(f"Vanilla Dialogues: skipped an unreadable heard line: {item}")
        return HandOver(GameTime.from_game(data.get("now")), tuple(lines))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _listeners(people) -> tuple[HeardPerson, ...]:
    """The readable people, once each, without the player"""
    result: list[HeardPerson] = []
    for person in people:
        if person and not person.is_player and all(p.ref_id != person.ref_id for p in result):
            result.append(person)
    return tuple(result)
