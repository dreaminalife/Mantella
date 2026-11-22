"""Vanilla Dialogues: sorts the lines a request brought. A line heard by someone in the running conversation goes into it, once.
Everyone else who heard it keeps it as a heard line"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import replace
import fnmatch
import logging
from typing import Any, TYPE_CHECKING
from src.config.config_loader import ConfigLoader
from src.heard_lines.heard_line import HandOver, HeardLine, HeardPerson
from src.heard_lines.heard_lines_store import HeardLinesStore
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
    def __init__(self, config: ConfigLoader, store: HeardLinesStore):
        self.__config: ConfigLoader = config
        self.__store: HeardLinesStore = store

    def receive(self, hand_over_text: Any, world_id: str, conversation: Conversation | None) -> None:
        hand_over = HandOver.parse(hand_over_text)
        if not hand_over:
            return
        if hand_over.now:
            self.__store.note_now(world_id, hand_over.now)
        lines = [line for line in hand_over.lines if not self.__only_opens_menu(line)]
        is_live = conversation is not None and not conversation.has_already_ended and not conversation.is_stopping and conversation.passes_ingame_events()
        full = log.isEnabledFor(logging.DEBUG) # Each line is written out only at Full
        for line in hand_over.lines:
            if full and line not in lines:
                log.debug(f"Vanilla Dialogues: {self.__describe(line)} Left out: the pick only opens a menu (Lines That Only Open a Menu)")
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
        if line.is_menu_talk:
            said = f'{line.speaker.name}, answering {line.player}\'s "{line.asked}": "{line.words}"'
        elif line.to:
            said = f'{line.speaker.name} to {line.to.name}: "{line.words}"'
        else:
            said = f'{line.speaker.name}: "{line.words}"'
        where = f" in {line.place}" if line.place else ""
        names = ", ".join(person.name for person in line.heard_by)
        there = f"{line.player} was there" if line.player_there else f"{line.player} wasn't there"
        return f"{said}{where}. Heard by {len(line.heard_by)} ({names}). {there}."

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

    def __only_opens_menu(self, line: HeardLine) -> bool:
        """Menu talk whose pick only opens a menu (Lines That Only Open a Menu), matched like Ignored In-Game Events"""
        if not line.is_menu_talk or not line.asked:
            return False
        text = line.asked.strip().lower()
        return any(fnmatch.fnmatchcase(text, pattern) for pattern in self.__config.heard_lines_menu_only)
