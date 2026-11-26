"""The Vanilla Dialogues settings, in their own section of the Conversation page"""
from src.config.config_loader import ConfigLoader
from src.config.definitions.conversation_definitions import CONVERSATION_PAGE
from src.config.definitions.other_definitions import OtherDefinitions
from src.config.mantella_config_value_definitions_new import MantellaConfigValueDefinitionsNew

IDENTIFIERS = ["heard_lines_days_kept", "heard_lines_most_per_npc", "heard_lines_menu_talk", "heard_lines_scenes", "heard_lines_npcs_meet",
               "heard_lines_menu_only", "heard_lines_ignored_npcs", "heard_lines_story_quests", "heard_lines_chatter_quests"]


def test_defaults_and_ranges():
    days = OtherDefinitions.get_heard_lines_days_kept_config_value()
    most = OtherDefinitions.get_heard_lines_most_per_npc_config_value()
    menu = OtherDefinitions.get_heard_lines_menu_only_config_value()
    assert (days.identifier, days.name, days.default_value, days.min_value, days.max_value) == ("heard_lines_days_kept", "Vanilla Dialogue Memory (Days)", 3, 1, 30)
    assert (most.identifier, most.name, most.default_value, most.min_value, most.max_value) == ("heard_lines_most_per_npc", "Most Vanilla Dialogue Lines per NPC", 30, 1, 200)
    assert (menu.identifier, menu.name) == ("heard_lines_menu_only", "Ignored Dialogue Menu Choices")
    menu_talk = OtherDefinitions.get_heard_lines_menu_talk_config_value()
    scenes = OtherDefinitions.get_heard_lines_scenes_config_value()
    ignored = OtherDefinitions.get_heard_lines_ignored_npcs_config_value()
    assert (menu_talk.identifier, menu_talk.name, menu_talk.default_value) == ("heard_lines_menu_talk", "Remember Dialogue Menu Talk", True)
    assert (scenes.identifier, scenes.name, scenes.default_value) == ("heard_lines_scenes", "Remember Scenes", True)
    assert (ignored.identifier, ignored.name, ignored.default_value) == ("heard_lines_ignored_npcs", "Ignored NPCs", "")
    npcs_meet = OtherDefinitions.get_heard_lines_npcs_meet_config_value()
    story = OtherDefinitions.get_heard_lines_story_quests_config_value()
    chatter_quests = OtherDefinitions.get_heard_lines_chatter_quests_config_value()
    assert (npcs_meet.identifier, npcs_meet.name, npcs_meet.default_value) == ("heard_lines_npcs_meet", "Remember Scenes When NPCs Meet", False)
    # The player's own entries: Mantella's are in data/Skyrim/vanilla_dialogues
    assert (menu.default_value, story.default_value, chatter_quests.default_value) == ("", "", "")
    assert (story.identifier, story.name) == ("heard_lines_story_quests", "Quests That Aren't Background Chatter")
    assert (chatter_quests.identifier, chatter_quests.name) == ("heard_lines_chatter_quests", "Quests That Are Background Chatter")


def test_on_the_conversation_page():
    groups = MantellaConfigValueDefinitionsNew.get_config_values(False, [], None).base_groups
    holding = [group for group in groups if any(value.identifier in IDENTIFIERS for value in group.value)]
    assert len(holding) == 1
    section = holding[0]
    assert (section.name, section.page) == ("Vanilla Dialogues", CONVERSATION_PAGE)
    assert [value.identifier for value in section.value] == IDENTIFIERS


def test_a_value_saved_on_the_other_tab_still_loads(tmp_path):
    # Saved before the move, under [Other]
    (tmp_path / "config.ini").write_text("[Other]\nheard_lines_days_kept = 9\nheard_lines_most_per_npc = 50\n", encoding="utf-8")
    config = ConfigLoader(str(tmp_path))
    assert (config.heard_lines_days_kept, config.heard_lines_most_per_npc) == (9, 50)


def test_the_settings_load(default_config):
    assert default_config.heard_lines_days_kept == 3
    assert default_config.heard_lines_most_per_npc == 30
    assert default_config.heard_lines_menu_talk and default_config.heard_lines_scenes
    assert default_config.heard_lines_ignored_npcs == []
    assert not default_config.heard_lines_npcs_meet
    assert default_config.heard_lines_story_quests == [] and default_config.heard_lines_chatter_quests == []


def test_a_list_takes_a_comma_or_a_new_line_between_entries(tmp_path):
    (tmp_path / "config.ini").write_text("[Other]\nheard_lines_ignored_npcs = Serana, Lydia\n  Hulda\n  Saadia,\n", encoding="utf-8")
    config = ConfigLoader(str(tmp_path))
    assert config.heard_lines_ignored_npcs == ["serana", "lydia", "hulda", "saadia"]
