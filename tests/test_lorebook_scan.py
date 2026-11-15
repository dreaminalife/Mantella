"""What the lorebook searches: the variables in the text that is actually sent. Shared sections are expanded first,
and the branches of a merged prompt that are left out are not searched"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.conversation.bystanders import ShoutState
from tests.test_prompt_snapshots import PLAYER, character, lorebook_echo

GAME_MASTER = "skyrim_game_master_prompt"


class LorebookStop(Exception):
    pass


@pytest.fixture
def context(default_context):
    default_context._context__lorebook_manager = SimpleNamespace(render_lorebook_entries=lorebook_echo)
    default_context.location = "Riverwood"
    default_context.npcs_in_conversation.add_or_update_character(PLAYER)
    default_context.npcs_in_conversation.add_or_update_character(character("Lydia", "000200"))
    return default_context


def searched(text: str) -> str:
    return text.split("LOREBOOK SEARCHED: ", 1)[1]


def test_a_variable_in_a_branch_left_out_is_not_searched(context):
    prompt = "{if shout}\n{location}\n{end if}\n{name}\n{lorebook}"
    assert "Riverwood" not in searched(context.generate_system_message(prompt, [], prompt_type=GAME_MASTER, conditions=set()))
    assert "Riverwood" in searched(context.generate_system_message(prompt, [], prompt_type=GAME_MASTER, conditions={"shout"}))


def test_nearby_bios_in_a_branch_left_out_are_never_built(context):
    context.shout = ShoutState()
    with patch.object(type(context), "_context__get_nearby_bios_text", return_value="NEARBY BIOS") as build:
        text = context.generate_system_message("{if shout}\n{nearby_bios}\n{end if}\nEnd", [], prompt_type=GAME_MASTER, conditions=set())
        build.assert_not_called()
        assert text == "End"
        assert "NEARBY BIOS" in context.generate_system_message("{if shout}\n{nearby_bios}\n{end if}", [], prompt_type=GAME_MASTER, conditions={"shout"})
        build.assert_called_once()


def test_a_variable_only_inside_a_shared_section_is_searched(context, default_config):
    manager = default_config.prompt_profile_manager
    assert manager.save_section("where", "You are in {location}.")[0]
    assert manager.save_profile("skyrim_prompt", "Mine", "You are {name}.\n{where}\n{lorebook}")[0]
    assert manager.set_active("skyrim_prompt", "Mine")[0]
    text = context.generate_system_message("You are {name}.\n{where}\n{lorebook}", [], prompt_type="skyrim_prompt")
    assert "You are in Riverwood." in text
    assert "Riverwood" in searched(text)


def test_a_prompt_without_sections_searches_the_same_as_before(context):
    text = context.generate_system_message("You are {name} in {location}.\n{lorebook}", [], prompt_type="skyrim_prompt")
    assert searched(text) == "Lydia | Riverwood" # The name and the location


def test_the_memory_prompt_searches_the_variables_inside_shared_sections(default_rememberer, default_config):
    manager = default_config.prompt_profile_manager
    assert manager.save_section("who", "Remember {player_name}.")[0]
    assert manager.save_profile("memory_prompt", "Mine", "Summarize for {name}.\n{who}\n{lorebook}")[0]
    assert manager.set_active("memory_prompt", "Mine")[0]
    default_config.memory_prompt = "Summarize for {name}.\n{who}\n{lorebook}"
    messages = MagicMock()
    messages.transform_to_text.return_value = ""
    templates: list[str] = []
    def build(template, *_args, **_kwargs):
        templates.append(template)
        raise LorebookStop()
    with patch("src.remember.summaries.player_name_from_thread", return_value="Dragonborn"), \
         patch("src.remember.summaries.format_character_bios_for_memory", return_value=""), \
         patch("src.remember.summaries.thread_contains_player", return_value=True), \
         patch.object(type(default_rememberer), "_Summaries__build_lorebook_text_for_prompt", side_effect=build), \
         pytest.raises(LorebookStop):
        default_rememberer._Summaries__create_new_conversation_summary(SimpleNamespace(characters=[], messages=messages), "1")
    assert templates == ["Summarize for {name}.\nRemember {player_name}.\n{lorebook}"] # The section is expanded before the search
