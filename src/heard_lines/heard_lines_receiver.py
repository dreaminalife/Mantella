"""Vanilla Dialogues: sorts the lines a request brought. A line heard by someone in the running conversation goes into it, once.
Everyone else who heard it keeps it as a heard line. Lines the settings leave out go nowhere"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import replace
import fnmatch
import logging
from typing import Any, TYPE_CHECKING
from src.config.config_loader import ConfigLoader
from src.heard_lines.heard_line import HandOver, HeardLine, HeardPerson
from src.heard_lines.heard_lines_store import HeardLinesStore
from src.heard_lines.vanilla_dialogue_lists import EditableList, ShippedLists
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VANILLA_DIALOGUES)

if TYPE_CHECKING:
    from src.conversation.conversation import Conversation


class HeardLinesReceiver(ABC):
    @abstractmethod
    def receive(self, hand_over_text: Any, world_id: str, conversation: Conversation | None) -> None:
        """Sorts the lines of one hand-over.

        Args:
            hand_over_text (Any): the plugin's JSON text, as the game sent it. Missing or empty: nothing to do
            conversation (Conversation | None): the running conversation, before the request changes who is in it. None stores every line
        """
        pass


class GameHeardLinesReceiver(HeardLinesReceiver):
    def __init__(self, config: ConfigLoader, store: HeardLinesStore, lists: ShippedLists | None = None):
        """lists: Mantella's own list entries. None reads them from data/Skyrim/vanilla_dialogues"""
        self.__config: ConfigLoader = config
        self.__store: HeardLinesStore = store
        self.__lists: ShippedLists = lists if lists is not None else ShippedLists.load()

    def receive(self, hand_over_text: Any, world_id: str, conversation: Conversation | None) -> None:
        hand_over = HandOver.parse(hand_over_text)
        if not hand_over:
            return
        if hand_over.now:
            self.__store.note_now(world_id, hand_over.now)
        full = log.isEnabledFor(logging.DEBUG) # Each line is written out only at Full
        lines: list[HeardLine] = []
        for line in hand_over.lines:
            why = self.__left_out_because(line)
            if not why:
                lines.append(line)
            elif full:
                log.debug(f"Vanilla Dialogues: {self.__describe(line)} Left out: {why}")
        is_live = conversation is not None and not conversation.has_already_ended and not conversation.is_stopping and conversation.passes_ingame_events()
        to_conversation: list[HeardLine] = []
        to_store: dict[HeardPerson, list[HeardLine]] = {}
        for line in lines:
            insiders: list[HeardPerson] = []
            for person in line.heard_by:
                if is_live and conversation and line.time >= conversation.start_ts and conversation.contains_character(person.ref_id):
                    insiders.append(person)
                else:
                    to_store.setdefault(person, []).append(line)
            if insiders:
                # With only the people in the conversation: it keeps the line for them when it ends. The others keep it now
                to_conversation.append(replace(line, heard_by=tuple(insiders)))
            if full:
                log.debug(f"Vanilla Dialogues: {self.__describe(line)} {self.__where(line, insiders)}")
        if conversation and to_conversation:
            conversation.add_heard_dialogue(to_conversation)
        for person, person_lines in to_store.items():
            try: # One NPC that can't be written (a name that can't be a folder, a full disk) loses no one else's lines
                self.__store.add(world_id, person, person_lines)
            except Exception as e:
                log.error(f"Vanilla Dialogues: could not keep the lines {person.name} heard: {e}")
        if lines:
            log.info(f"Vanilla Dialogues: {len(lines)} heard lines arrived. {len(to_conversation)} go into the conversation, "
                         f"and {len(to_store)} NPCs keep some as heard lines")

    @staticmethod
    def __describe(line: HeardLine) -> str:
        """One heard line for the log (Vanilla Dialogues at Full): who said it to whom, the words, and who heard it"""
        if line.is_menu_talk and line.asked:
            said = f'{line.speaker.name}, answering {line.player}\'s "{line.asked}": "{line.words}"'
        elif line.is_menu_talk: # Unprompted: a forced greeting, or a line that carries on by itself
            said = f'{line.speaker.name} to {line.player}: "{line.words}"'
        elif line.to:
            said = f'{line.speaker.name} to {line.to.name}: "{line.words}"'
        else:
            said = f'{line.speaker.name}: "{line.words}"'
        where = f" in {line.place}" if line.place else ""
        names = ", ".join(person.name for person in line.heard_by)
        there = f"{line.player} was there" if line.player_there else f"{line.player} wasn't there"
        # For Quests That Are (or Aren't) Background Chatter: a whole quest, or one scene
        quest = f" Quest {line.quest.id}." if line.quest and line.quest.id else ""
        scene = f" Scene {line.scene_key}." if line.scene_key and not line.is_menu_talk else ""
        return f"{said}{where}. Heard by {len(line.heard_by)} ({names}). {there}.{quest}{scene}"

    @staticmethod
    def __where(line: HeardLine, insiders: list[HeardPerson]) -> str:
        """Where a heard line went, for the log"""
        kept = len(line.heard_by) - len(insiders)
        if not line.heard_by:
            return "Nobody to keep it."
        if not insiders:
            return f"Kept by all {kept} as a heard line."
        into = f"Into the conversation for {', '.join(person.name for person in insiders)}"
        return f"{into}; kept as a heard line by the other {kept}." if kept else f"{into}."

    def __left_out_because(self, line: HeardLine) -> str:
        """Why a line is left out, for everyone who heard it. Empty when it is kept"""
        if line.is_menu_talk and not self.__config.heard_lines_menu_talk:
            return "dialogue menu talk is off (Remember Dialogue Menu Talk)"
        if not line.is_menu_talk and not self.__config.heard_lines_scenes:
            return "scenes are off (Remember Scenes)"
        ignored = next((person.name for person in (line.speaker, line.to) if person and self.__matches(person.name, self.__config.heard_lines_ignored_npcs)), None)
        if ignored:
            return f"{ignored} is on Ignored NPCs"
        if line.is_menu_talk and line.asked and EditableList(self.__lists.menu_choices).with_players(self.__config.heard_lines_menu_only).matches(line.asked):
            return "the pick only opens a menu (Ignored Dialogue Menu Choices)"
        chatter = self.__why_background_chatter(line)
        if chatter:
            return f"background chatter: {chatter}"
        return ""

    def __why_background_chatter(self, line: HeardLine) -> str:
        """Why a scene line is background chatter that NPCs don't remember. Empty when they remember it, also for menu talk and a line
        without a quest or scene (from a plugin from before they were sent).
        A scene entry decides before a quest entry, so one scene can be dropped from a story quest, or kept from a chatter quest. Within
        each, the player's entries come before Mantella's, and Quests That Aren't Background Chatter before Quests That Are. The lists
        always apply. Remember Scenes When NPCs Meet only decides the small talk Skyrim starts when two NPCs meet"""
        if line.is_menu_talk:
            return ""
        story = EditableList(self.__lists.story_quests).with_players(self.__config.heard_lines_story_quests)
        chatter = EditableList(self.__lists.chatter_quests).with_players(self.__config.heard_lines_chatter_quests)
        for what, key, story_entries, chatter_entries in (("its scene", line.scene_key, story.scenes(), chatter.scenes()),
                                                          ("it", line.quest.id if line.quest else "", story.quests(), chatter.quests())):
            if not key:
                continue
            if story_entries.players_match(key):
                return ""
            if chatter_entries.players_match(key):
                return f"{what} is on Quests That Are Background Chatter"
            if story_entries.ours_match(key):
                return ""
            if chatter_entries.ours_match(key):
                return f"{what} is on Mantella's own Quests That Are Background Chatter"
        if line.quest and line.quest.is_small_talk_when_npcs_meet and not self.__config.heard_lines_npcs_meet:
            return "it starts when two NPCs meet, and Remember Scenes When NPCs Meet is off"
        return ""

    @staticmethod
    def __matches(text: str, patterns: list[str]) -> bool:
        """Matched like Ignored In-Game Events: the whole text, any case, * for any text"""
        text = text.strip().lower()
        return any(fnmatch.fnmatchcase(text, pattern) for pattern in patterns)
