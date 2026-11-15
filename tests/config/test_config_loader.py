from src.config.config_loader import ConfigLoader
import os
import sys
import json
import configparser
from unittest.mock import patch
from src.config.definitions.game_definitions import GameEnum
from src.conversation.action import Action


def test_init_new_config_ini(tmp_path):
    '''Ensure ConfigLoader can create a new config file for paths with non-existent config file'''
    ConfigLoader(tmp_path)
    # Verify the config file was created
    assert os.path.exists(os.path.join(tmp_path, 'config.ini'))


def test_init_with_existing_config_ini(tmp_path):
    '''Test ConfigLoader initialization with an existing config.ini file'''
    # First create a generic config file
    config = configparser.ConfigParser()
    config['DEFAULT'] = {'game': 'SKYRIM'}
    
    config_path = os.path.join(tmp_path, 'config.ini')
    with open(config_path, 'w', encoding='utf-8') as f:
        config.write(f)
    
    # Now initialize ConfigLoader with the existing file
    ConfigLoader(tmp_path)
    
    # Verify the file wasn't overwritten (by checking if our original file still exists)
    assert os.path.exists(config_path)


def test_has_any_config_value_changed(tmp_path):
    '''Test the has_any_config_value_changed property'''
    config_loader = ConfigLoader(tmp_path)
    
    # Initially it should be false
    assert config_loader.has_any_config_value_changed == False
    
    # Trigger a change by accessing a config value and modifying it
    config_loader.definitions.get_config_value_definition('game').value = GameEnum.FALLOUT4
    
    # Now it should be true
    assert config_loader.has_any_config_value_changed == True
    
    # Reset the flag
    config_loader.update_config_loader_with_changed_config_values()
    
    # Now it should be false again
    assert config_loader.has_any_config_value_changed == False


def test_update_config_loader_with_changed_config_values(tmp_path):
    '''Test that update_config_loader_with_changed_config_values updates internal state'''
    config_loader = ConfigLoader(tmp_path)
    
    # Trigger a change by accessing a config value and modifying it
    config_loader.definitions.get_config_value_definition('game').value = GameEnum.FALLOUT4
    
    # Verify the change flag is set
    assert config_loader.has_any_config_value_changed == True
    
    # Update the config loader with the changes
    config_loader.update_config_loader_with_changed_config_values()
    
    # Verify the change flag is reset
    assert config_loader.has_any_config_value_changed == False
    
    # Verify that the game property reflects the new value
    assert config_loader.game == GameEnum.FALLOUT4


def test_debug_mode_applies_without_restart(tmp_path):
    '''Turning Debug Mode on or off changes which logs are let through right away'''
    import logging
    config_loader = ConfigLoader(tmp_path)
    root_logger = logging.getLogger()
    original_level = root_logger.level
    try:
        root_logger.setLevel(logging.INFO)
        config_loader.definitions.get_config_value_definition('advanced_logs').value = True
        config_loader.update_config_loader_with_changed_config_values()
        assert root_logger.isEnabledFor(15)

        config_loader.definitions.get_config_value_definition('advanced_logs').value = False
        config_loader.update_config_loader_with_changed_config_values()
        assert not root_logger.isEnabledFor(15)
    finally:
        root_logger.setLevel(original_level)


def test_definitions_property(tmp_path):
    '''Test that the definitions property returns a valid config value'''
    config_loader = ConfigLoader(tmp_path)
    
    # Verify we can access specific definitions
    game_def = config_loader.game
    assert game_def is not None


def test_have_all_config_values_loaded_correctly(default_config: ConfigLoader):
    '''Test the have_all_config_values_loaded_correctly property'''
    # For a newly created config with default values, this should be true
    assert default_config.have_all_config_values_loaded_correctly == True


def test_drop_last_sentences_single_defaults_to_zero(default_config: ConfigLoader):
    assert default_config.drop_last_sentences_single == 0


def test_replace_braille_blank_defaults_to_off(default_config: ConfigLoader):
    assert default_config.replace_braille_blank is False


def test_turn_thoughts_to_keep_defaults_to_one(default_config: ConfigLoader):
    assert default_config.turn_thoughts_to_keep == 1


def test_load_actions_from_json(tmp_path):
    '''Test loading actions from JSON files'''
    # Create a test actions folder
    actions_folder = os.path.join(tmp_path, 'actions')
    os.makedirs(actions_folder, exist_ok=True)
    
    # Create a test action JSON file
    action_data = {
        "identifier": "mantella_test_action",
        "name": "Test",
        "key": "Test",
        "description": "A test action",
        "prompt": "If the player asks you to use the Test keyword, begin your response with 'Test:'",
        "is-interrupting": False,
        "one-on-one": True,
        "multi-npc": False,
        "radiant": False,
        "info-text": "Test action completed"
    }
    
    action_file_path = os.path.join(actions_folder, 'test_action.json')
    with open(action_file_path, 'w') as f:
        json.dump(action_data, f)

    # Call the method directly
    actions = ConfigLoader.load_actions_from_json(actions_folder)
    
    # Verify an action was loaded
    assert len(actions) == 1
    
    # Verify the action has the correct properties
    action: Action = actions[0]
    assert action.identifier == "mantella_test_action"
    assert action.name == "Test"
    assert action.keyword == "Test"

def test_load_actions_from_json_wrong_file_type(tmp_path):
    '''Test loading actions when file type is incorrect'''
    # Create a test actions folder
    actions_folder = os.path.join(tmp_path, 'actions')
    os.makedirs(actions_folder, exist_ok=True)
    
    # Create a test action JSON file
    action_data = {
        "identifier": "mantella_test_action",
        "name": "Test",
        "key": "Test",
        "description": "A test action",
        "prompt": "If the player asks you to use the Test keyword, begin your response with 'Test:'",
        "is-interrupting": False,
        "one-on-one": True,
        "multi-npc": False,
        "radiant": False,
        "info-text": "Test action completed"
    }
    
    action_file_path = os.path.join(actions_folder, 'test_action.txt')
    with open(action_file_path, 'w') as f:
        json.dump(action_data, f)

    # Call the method directly
    actions = ConfigLoader.load_actions_from_json(actions_folder)
    
    # Verify no actions were loaded
    assert len(actions) == 0

def test_load_actions_from_json_no_files(tmp_path):
    '''Test loading actions when there are no files'''
    actions = ConfigLoader.load_actions_from_json(tmp_path)
    
    # Verify no actions were loaded
    assert len(actions) == 0


def test_bio_section_filter_config_parsing(tmp_path):
    config_loader = ConfigLoader(tmp_path)
    config_loader.definitions.get_config_value_definition("enable_bio_section_filter").value = True
    config_loader.definitions.get_config_value_definition(
        "bio_sections_to_exclude"
    ).value = "Personal History, Race ,   Appearance"
    config_loader.update_config_loader_with_changed_config_values()

    assert config_loader.enable_bio_section_filter is True
    assert config_loader.enable_bio_section_filter_for_memory is False
    assert config_loader.bio_sections_to_exclude_list == [
        "personal history",
        "race",
        "appearance",
    ]


def test_bio_section_filter_config_parsing_strips_quotes(tmp_path):
    config_loader = ConfigLoader(tmp_path)
    config_loader.definitions.get_config_value_definition("enable_bio_section_filter").value = True
    config_loader.definitions.get_config_value_definition(
        "bio_sections_to_exclude"
    ).value = "\"Personal History\", 'Race'"
    config_loader.update_config_loader_with_changed_config_values()

    assert config_loader.bio_sections_to_exclude_list == [
        "personal history",
        "race",
    ]


def test_bio_section_filter_for_memory_config_parsing(tmp_path):
    config_loader = ConfigLoader(tmp_path)
    assert config_loader.enable_bio_section_filter_for_memory is False

    config_loader.definitions.get_config_value_definition("enable_bio_section_filter").value = True
    config_loader.definitions.get_config_value_definition("enable_bio_section_filter_for_memory").value = True
    config_loader.update_config_loader_with_changed_config_values()

    assert config_loader.enable_bio_section_filter is True
    assert config_loader.enable_bio_section_filter_for_memory is True


def test_sync_bio_section_filter_settings_from_definitions_without_full_reload(tmp_path):
    config_loader = ConfigLoader(tmp_path)
    config_loader.definitions.get_config_value_definition("enable_bio_section_filter").value = True
    config_loader.definitions.get_config_value_definition("enable_bio_section_filter_for_memory").value = True
    config_loader.definitions.get_config_value_definition("bio_sections_to_exclude").value = "Personal History"

    assert config_loader.has_any_config_value_changed is True
    assert config_loader.enable_bio_section_filter is False
    assert config_loader.enable_bio_section_filter_for_memory is False
    assert config_loader.bio_sections_to_exclude_list == []

    config_loader.sync_bio_section_filter_settings_from_definitions()

    assert config_loader.enable_bio_section_filter is True
    assert config_loader.enable_bio_section_filter_for_memory is True
    assert config_loader.bio_sections_to_exclude_list == ["personal history"]
    assert config_loader.has_any_config_value_changed is True


def test_sync_save_summary_now_toggles_from_definitions_without_full_reload(tmp_path):
    config_loader = ConfigLoader(tmp_path)
    assert config_loader.save_summary_now_also_save_inner_thoughts is True
    assert config_loader.save_summary_now_also_save_personal_reflection is True

    config_loader.definitions.get_config_value_definition("save_summary_now_also_save_inner_thoughts").value = False
    config_loader.definitions.get_config_value_definition("save_summary_now_also_save_personal_reflection").value = False

    assert config_loader.has_any_config_value_changed is True
    assert config_loader.save_summary_now_also_save_inner_thoughts is True
    assert config_loader.save_summary_now_also_save_personal_reflection is True

    config_loader.sync_save_summary_now_toggles_from_definitions()

    assert config_loader.save_summary_now_also_save_inner_thoughts is False
    assert config_loader.save_summary_now_also_save_personal_reflection is False
    assert config_loader.has_any_config_value_changed is True


def test_sync_prompt_values_from_definitions_without_full_reload(tmp_path):
    config_loader = ConfigLoader(tmp_path, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config_loader.definitions.get_config_value_definition("personal_reflection_prompt").value = "edited reflection {name}"
    config_loader.definitions.get_config_value_definition("inner_monologue_prompt").value = "edited monologue {name}"

    assert config_loader.has_any_config_value_changed is True
    assert config_loader.personal_reflection_prompt != "edited reflection {name}"
    assert config_loader.inner_monologue_prompt != "edited monologue {name}"

    config_loader.sync_prompt_values_from_definitions()

    assert config_loader.personal_reflection_prompt == "edited reflection {name}"
    assert config_loader.inner_monologue_prompt == "edited monologue {name}"
    assert config_loader.has_any_config_value_changed is True


def test_sequential_llm_pool_config_defaults_and_parsing(tmp_path):
    config_loader = ConfigLoader(tmp_path)
    assert config_loader.sequential_llm_one_on_one_per_request_enabled is False
    assert config_loader.sequential_llm_multi_npc_per_request_enabled is False
    assert config_loader.sequential_llm_pool_one_on_one == []
    assert config_loader.sequential_llm_pool_multi_npc == []

    pool = '[{"service": "OpenRouter", "model": "google/gemini-3-flash"}]'
    config_loader.definitions.get_config_value_definition("sequential_llm_one_on_one_per_request_enabled").value = True
    config_loader.definitions.get_config_value_definition("sequential_llm_pool_one_on_one").value = pool
    config_loader.update_config_loader_with_changed_config_values()

    assert config_loader.sequential_llm_one_on_one_per_request_enabled is True
    assert config_loader.sequential_llm_pool_one_on_one == [
        {"service": "OpenRouter", "model": "google/gemini-3-flash"}
    ]


@patch('os.path.exists')
def test_game_detection_when_integrated(mock_exists, tmp_path):
    '''Test game detection when running in integrated mode'''
    # Mock os.path.exists to always return True for exists checks
    mock_exists.return_value = True
    
    # Mock sys.argv to include --integrated
    with patch.object(sys, 'argv', ['main.py', '--integrated']):
        # Create a ConfigLoader instance with game_override to ensure it's not used
        config_loader = ConfigLoader(tmp_path, game_override=GameEnum.FALLOUT4_VR)

        assert config_loader.game != GameEnum.FALLOUT4_VR
        
        # In integrated mode, paths should be set to relative paths
        assert hasattr(config_loader, 'game_path')
        assert hasattr(config_loader, 'mod_path')
        assert hasattr(config_loader, 'piper_path')