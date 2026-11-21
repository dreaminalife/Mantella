"""The three Vanilla Dialogues settings on the Other tab"""
from src.config.definitions.other_definitions import OtherDefinitions
from src.config.types.config_value import ConfigValueTag


def test_defaults_and_ranges():
    days = OtherDefinitions.get_heard_lines_days_kept_config_value()
    most = OtherDefinitions.get_heard_lines_most_per_npc_config_value()
    menu = OtherDefinitions.get_heard_lines_menu_only_config_value()
    assert (days.identifier, days.name, days.default_value, days.min_value, days.max_value) == ("heard_lines_days_kept", "Vanilla Dialogue Memory (Days)", 3, 1, 30)
    assert (most.identifier, most.name, most.default_value, most.min_value, most.max_value) == ("heard_lines_most_per_npc", "Most Vanilla Dialogue Lines per NPC", 30, 1, 200)
    assert (menu.identifier, menu.name) == ("heard_lines_menu_only", "Lines That Only Open a Menu")
    assert all(ConfigValueTag.advanced in value.tags for value in (days, most, menu))


def test_the_settings_load(default_config):
    assert default_config.heard_lines_days_kept == 3
    assert default_config.heard_lines_most_per_npc == 30
    assert "stage1hello" in default_config.heard_lines_menu_only
