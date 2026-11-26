"""Made-up heard lines, in the form the MantellaHeardLines plugin hands them over (see src/heard_lines/heard_line.py)"""
import json
from types import SimpleNamespace
from typing import Any

# Form ids, as the plugin sends them, and the ref ids Mantella masks them to
MIKAEL = {"id": 0x0001A671, "name": "Mikael"}
CARLOTTA = {"id": 0x0001A672, "name": "Carlotta"}
SAADIA = {"id": 0x000D7505, "name": "Saadia"}
HULDA = {"id": 0x0001A66E, "name": "Hulda"}
PLAYER = {"id": 0x00000014, "name": "Kit"}
REF = {"Mikael": "01A671", "Carlotta": "01A672", "Saadia": "0D7505", "Hulda": "01A66E"}

WORLD = "KitTest1"
SCENE = 0x000ABCDE
START_DAY = 42.0 # Midnight of day 42


def at(hour: float, day: int = 0) -> dict[str, float]:
    """A game time, the given hour of START_DAY + day"""
    return {"day": START_DAY + day + hour / 24, "hour": hour}


# Scenes' quests as Skyrim.esm has them, in the form the plugin sends them
STORY_QUEST = {"id": "MS11", "journal": True, "runs_once": True, "npcs_meet": False} # Blood on the Ice
MEETING_QUEST = {"id": "DialogueWhiterunCarlottaOlfinaScene1", "journal": False, "runs_once": False, "npcs_meet": True}
TOWN_QUEST = {"id": "DialogueWhiterunHeimskrPreachScene", "journal": False, "runs_once": False, "npcs_meet": False} # Starts on a location change
DIBELLA_QUEST = {"id": "T01", "journal": True, "runs_once": False, "npcs_meet": False} # No journal category, but journal objectives
JOURNAL_MEETING_QUEST = {"id": "DarkBrotherhoodSanctuaryConvSystem", "journal": True, "runs_once": False, "npcs_meet": True}
ONCE_MEETING_QUEST = {"id": "AModsMeetingScene", "journal": False, "runs_once": True, "npcs_meet": True}
ONCE_QUEST = {"id": "DialogueWhiterunArgueScene", "journal": False, "runs_once": True, "npcs_meet": False}
HELPER_QUEST = {"id": "MS11WuunferthArrest", "journal": False, "runs_once": False, "npcs_meet": False} # A story scene that looks like chatter
TAVERN_QUEST = {"id": "WITavern", "journal": True, "runs_once": False, "npcs_meet": False} # Chatter filed as a Miscellaneous quest
# Small talk from two NPCs meeting that only plays after a step of The Heart of Dibella: Enmon and Mena missing their daughter
FOLLOWING_MEETING_QUEST = {"id": "DialogueKarthwastenT01EnmonsHouseScene03", "journal": False, "runs_once": False, "npcs_meet": True, "follows_quest": True}
TOWN_STORY_QUEST = {"id": "DialogueWhiterun", "journal": False, "runs_once": False, "npcs_meet": False} # Mikael's song, and the Jarl hearing about the dragon

# Scenes as the plugin names them: the file that added them, and their form id in it
SONG_SCENE = "Skyrim.esm:092A31" # Mikael singing Ragnar the Red, in DialogueWhiterun
DRAGON_NEWS_SCENE = "Skyrim.esm:09DCD5" # The Jarl hearing about the dragon, in DialogueWhiterun


def scene_line(words: str, speaker=MIKAEL, to=CARLOTTA, heard_by=None, hour: float = 12.0, day: int = 0, time: int = 1000,
               scene: int = SCENE, place: str = "The Bannered Mare", player_there: bool = True, quest: dict[str, Any] | None = None,
               scene_key: str | None = None) -> dict[str, Any]:
    line = {**at(hour, day), "time": time, "place": place, "scene": scene, "speaker": speaker, "words": words, "asked": "",
            "heard_by": heard_by if heard_by is not None else [MIKAEL, CARLOTTA, SAADIA], "player_there": player_there}
    if to is not None:
        line["to"] = to
    if quest is not None:
        line["quest"] = quest
    if scene_key is not None:
        line["scene_key"] = scene_key
    return line


def menu_line(asked: str, words: str, speaker=HULDA, heard_by=None, hour: float = 12.0, time: int = 1000) -> dict[str, Any]:
    return {**at(hour), "time": time, "place": "The Bannered Mare", "scene": 0, "menu": True, "speaker": speaker, "to": PLAYER, "words": words,
            "asked": asked, "heard_by": heard_by if heard_by is not None else [speaker], "player_there": True}


def hand_over(*lines: dict[str, Any], now: dict[str, float] | None = None, player: str = "Kit") -> str:
    data: dict[str, Any] = {"player": player, "lines": list(lines)}
    if now is not None:
        data["now"] = now
    return json.dumps(data)


def settings(days_kept: int = 3, most: int = 30, menu_only: list[str] | None = None, menu_talk: bool = True, scenes: bool = True,
             ignored_npcs: list[str] | None = None, npcs_meet: bool = False, story_quests: list[str] | None = None,
             chatter_quests: list[str] | None = None) -> SimpleNamespace:
    """The Mantella settings, as ConfigLoader holds them: list entries trimmed and in lower case"""
    def entries(values: list[str] | None) -> list[str]:
        return [v.strip().lower() for v in values or [] if v.strip()]
    return SimpleNamespace(heard_lines_days_kept=days_kept, heard_lines_most_per_npc=most, heard_lines_menu_only=entries(menu_only),
                           heard_lines_menu_talk=menu_talk, heard_lines_scenes=scenes, heard_lines_ignored_npcs=entries(ignored_npcs),
                           heard_lines_npcs_meet=npcs_meet, heard_lines_story_quests=entries(story_quests),
                           heard_lines_chatter_quests=entries(chatter_quests))
