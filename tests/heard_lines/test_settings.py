"""The Vanilla Dialogues settings, in their own section of the Conversation page"""
from src.config.config_loader import ConfigLoader
from src.config.definitions.conversation_definitions import CONVERSATION_PAGE
from src.config.definitions.other_definitions import OtherDefinitions
from src.config.mantella_config_value_definitions_new import MantellaConfigValueDefinitionsNew

IDENTIFIERS = ["heard_lines_days_kept", "heard_lines_most_per_npc", "heard_lines_menu_talk", "heard_lines_scenes", "heard_lines_menu_only",
               "heard_lines_ignored_npcs"]


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
    assert "i want you to.." in default_config.heard_lines_menu_only
    assert default_config.heard_lines_menu_talk and default_config.heard_lines_scenes
    assert default_config.heard_lines_ignored_npcs == []
