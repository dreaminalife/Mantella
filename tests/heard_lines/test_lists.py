"""Mantella's own list files in data/Skyrim/vanilla_dialogues, and the player's entries on top of them"""
import logging
import os

from src.heard_lines.vanilla_dialogue_lists import LISTS_FOLDER, EditableList, ShippedLists, read_list

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_one_entry_per_line_without_notes_or_blank_lines(tmp_path):
    path = tmp_path / "list.txt"
    path.write_text("﻿# A note\n\nI want you to..\n   # An indented note\n  Rent a room*  \nWho, me?\n", encoding="utf-8")
    assert read_list(str(path)) == ("i want you to..", "rent a room*", "who, me?") # Commas belong to the entry


def test_a_missing_file_is_logged_and_empty(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="mantella.vanilla_dialogues")
    assert read_list(str(tmp_path / "missing.txt")) == ()
    assert any("missing.txt" in r.getMessage() for r in caplog.records)


def test_mantellas_files_ship_with_the_corrections():
    lists = ShippedLists.load(os.path.join(REPO, LISTS_FOLDER))
    assert lists.menu_choices == ("i want you to..", "goodbye. (remove from mantella conversation)")
    assert lists.story_quests == ("mq201party*", "mqsovngardeconv*", "da11convo*", "tgdialoguehqscene*", "dialoguesaarthal*", "dialogueskyhaventemple*",
                                  "dialoguecidhnamine*", "dlc2rrseverinmanorscene*", "dlc2mh*", "dlc2rrcresciushousescene*", "dialogueheartwoodmillscene01",
                                  "dragonborn.esm:03a320")
    for quest in ("dialoguewhiterunheimskrpreachscene", "bardsongs", "witavern", "wicraftitem01", "followercommentary01", "darkbrotherhoodsanctuaryconvsystem",
                  "byohrelationshipadoption", "dlc1npccommentary01", "dlc1npccommentary02", "dlc1npccommentary03", "dlc2duninstrumentalqst",
                  "dgintimidatequest", "mqskyhavensparring", "dialoguesolitudepalacescene1", "dialoguesolitudepalacescene10"):
        assert quest in lists.chatter_quests
    assert len([quest for quest in lists.chatter_quests if quest.startswith("dialoguesolitudepalacescene")]) == 10
    # Chatter scenes from town quests that also hold a story scene: Mikael's song, the feud, Kleppr and Frabbi
    assert [entry for entry in lists.chatter_quests if ":" in entry] == ["skyrim.esm:092a31", "skyrim.esm:0e3de3", "skyrim.esm:0e3de4", "skyrim.esm:035f13"]
    # Quests that also hold a story scene, a favor you can do, or a scripted encounter aimed at you, are never on it
    for quest in ("dialoguewhiterun", "dialoguewindhelm", "wigameshideandseek", "wichangelocation04", "wicastmagic02", "wiadditem03"):
        assert quest not in lists.chatter_quests
    assert not any("*" in quest for quest in lists.chatter_quests) # Each named: a wildcard could catch a mod's story quest


def test_mantellas_files_are_shipped_and_tracked():
    """git ignores the text files in data unless .gitignore lets these through, and build-addon.ps1 ships tracked data files"""
    with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as f:
        assert "!data/Skyrim/vanilla_dialogues/*.txt" in f.read().splitlines()


def test_the_players_entries_add_and_a_minus_switches_mantellas_off():
    menu = EditableList(("i want you to..", "goodbye*")).with_players(["show me your wares.", "-goodbye*", "-"])
    assert menu.matches("Show me your wares.") and menu.players_match("Show me your wares.")
    assert menu.matches("I want you to..") and menu.ours_match("I want you to..")
    assert not menu.matches("Goodbye. (Remove from Mantella conversation)")
    assert menu.removed == ("goodbye*",) # A lone - switches nothing off


def test_scene_entries_and_quest_entries_are_told_apart_by_the_colon():
    entries = EditableList(("skyrim.esm:092a31", "bardsongs")).with_players(["dragonborn.esm:03a320", "*", "-bard*"])
    assert entries.scenes().ours == ("skyrim.esm:092a31",) and entries.scenes().added == ("dragonborn.esm:03a320",)
    assert entries.quests().ours == ("bardsongs",) and entries.quests().added == ("*",)
    assert not entries.scenes().matches("BardSongs") # A quest is never matched by a scene entry
    assert entries.scenes().removed == entries.quests().removed == ("bard*",) # A minus switches off both kinds
    assert not entries.quests().ours_match("BardSongs")


def test_a_minus_never_switches_off_the_players_own_entry():
    menu = EditableList(()).with_players(["rent a room*", "-rent a room*"])
    assert menu.matches("Rent a room (10 gold)")
