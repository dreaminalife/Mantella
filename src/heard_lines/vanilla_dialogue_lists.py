"""Vanilla Dialogues: the lists Mantella ships in data/Skyrim/vanilla_dialogues, and the player's own entries from the settings.

Each shipped file holds one entry per line. Lines starting with # are notes. Mantella's updates replace these files, so the lists can
improve without touching anyone's settings. The player's entries come from the matching setting (comma separated) and win over the
files: an entry adds to the list, and an entry starting with - switches off the file's entries for everything it matches.
All matching is like Ignored In-Game Events: the whole text, any case, * for any text.
The two quest lists also take scene entries (<file>:<6-hex form id>, as the Vanilla Dialogues log at Full shows them), which name one
scene of a quest.
"""
from __future__ import annotations
from dataclasses import dataclass
import fnmatch
import os
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VANILLA_DIALOGUES)

LISTS_FOLDER = os.path.join("data", "Skyrim", "vanilla_dialogues") # Next to Mantella.exe, like the other data files
MENU_CHOICES_FILE = "ignored_dialogue_menu_choices.txt"
STORY_QUESTS_FILE = "quests_that_arent_background_chatter.txt"
CHATTER_QUESTS_FILE = "quests_that_are_background_chatter.txt"
SCENE_MARK = ":" # A scene entry is <file>:<6-hex form id>, such as Skyrim.esm:092A31. Quest entries are editor ids, which have no colon


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    text = text.strip().lower()
    return any(fnmatch.fnmatchcase(text, pattern) for pattern in patterns)


@dataclass(frozen=True)
class EditableList:
    """One of Mantella's lists with the player's entries on top"""
    ours: tuple[str, ...] # From the shipped file, lowercased
    added: tuple[str, ...] = () # The player's entries, lowercased
    removed: tuple[str, ...] = () # The player's entries that started with -, without it, lowercased

    def players_match(self, text: str) -> bool:
        """The player put it on this list"""
        return _matches(text, self.added)

    def ours_match(self, text: str) -> bool:
        """Mantella's file has it, and the player didn't switch that off"""
        return not _matches(text, self.removed) and _matches(text, self.ours)

    def matches(self, text: str) -> bool:
        return self.players_match(text) or self.ours_match(text)

    def scenes(self) -> EditableList:
        """Only the scene entries (Skyrim.esm:092A31): an entry with a colon, which no quest's editor id has. A - entry still switches off
        both kinds, so -* switches off all of Mantella's entries"""
        return EditableList(tuple(e for e in self.ours if SCENE_MARK in e), tuple(e for e in self.added if SCENE_MARK in e), self.removed)

    def quests(self) -> EditableList:
        """Only the quest entries, so a wildcard quest entry like * never decides a scene ahead of the quest entries"""
        return EditableList(tuple(e for e in self.ours if SCENE_MARK not in e), tuple(e for e in self.added if SCENE_MARK not in e), self.removed)

    def with_players(self, entries: list[str]) -> EditableList:
        """With the player's entries from a setting: lowercased, already split at commas"""
        added = tuple(e for e in entries if not e.startswith("-"))
        removed = tuple(e[1:].strip() for e in entries if e.startswith("-") and e[1:].strip())
        return EditableList(self.ours, added, removed)


@dataclass(frozen=True)
class ShippedLists:
    """Mantella's own entries for Ignored Dialogue Menu Choices, Quests That Aren't Background Chatter and Quests That Are Background Chatter"""
    menu_choices: tuple[str, ...] = ()
    story_quests: tuple[str, ...] = ()
    chatter_quests: tuple[str, ...] = ()

    @staticmethod
    def load(folder: str = LISTS_FOLDER) -> ShippedLists:
        """Read once, when Mantella starts. A missing or unreadable file is logged and counts as empty"""
        return ShippedLists(read_list(os.path.join(folder, MENU_CHOICES_FILE)), read_list(os.path.join(folder, STORY_QUESTS_FILE)),
                            read_list(os.path.join(folder, CHATTER_QUESTS_FILE)))


def read_list(path: str) -> tuple[str, ...]:
    """One entry per line, lowercased. Blank lines and lines starting with # are left out"""
    try:
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except OSError as e:
        log.warning(f"Vanilla Dialogues: could not read {path}, so Mantella's own entries of that list are left out ({e})")
        return ()
    return tuple(line.strip().lower() for line in lines if line.strip() and not line.strip().startswith("#"))
