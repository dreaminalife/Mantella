from unittest.mock import MagicMock

import pytest

from src.character_manager import Character
from src.config.definitions.prompt_definitions import PromptDefinitions
from src.conversation.context import context as Context
from src.games.equipment import Equipment
from src.prompt_profile_manager import PromptProfileManager

NAME_VARIABLES_PROMPT = "name={name}|names={names}|all={all_names_without_player}|w_player={names_w_player}"


def _npc(name: str, ref_id: str) -> Character:
    return Character(
        base_id=ref_id,
        ref_id=ref_id,
        name=name,
        gender=1,
        race="Nord",
        is_player_character=False,
        bio="Housecarl.",
        is_in_combat=False,
        is_enemy=False,
        relationship_rank=0,
        is_generic_npc=False,
        ingame_voice_model="FemaleEvenToned",
        tts_voice_model="FemaleEvenToned",
        csv_in_game_voice_model="FemaleEvenToned",
        advanced_voice_model="FemaleEvenToned",
        voice_accent="en",
        equipment=Equipment({}),
        custom_character_values={},
    )


def _group_context(default_context: Context, player: Character, guard: Character) -> Context:
    default_context.npcs_in_conversation.add_or_update_character(player)
    default_context.npcs_in_conversation.add_or_update_character(guard)
    default_context.npcs_in_conversation.add_or_update_character(_npc("Lydia", "abc"))
    return default_context


def test_names_cover_all_npcs_without_focus_character(default_context, example_skyrim_player_character):
    context = _group_context(default_context, example_skyrim_player_character, _npc("Guard", "def"))
    prompt = context.generate_system_message("name={name}|names={names}|w_player={names_w_player}", [])
    assert prompt == "name=Lydia|names=Guard and Lydia|w_player=Dragonborn, Guard and Lydia"


def test_names_is_only_the_focus_character_in_game_master_npc_prompt(default_context, example_skyrim_player_character):
    guard = _npc("Guard", "def")
    context = _group_context(default_context, example_skyrim_player_character, guard)
    prompt = context.generate_system_message(NAME_VARIABLES_PROMPT, [], focus_character=guard)
    assert prompt == "name=Guard|names=Guard|all=Guard and Lydia|w_player=Dragonborn, Guard and Lydia"


def test_supported_variables_explain_names_for_game_master_npc_prompt():
    text = PromptProfileManager.supported_variables_markdown("skyrim_game_master_npc_prompt")
    assert "`{all_names_without_player}`" in text
    assert "`{name}`, `{names}`: the name of the NPC who is speaking" in text


def test_supported_variables_explain_bios_for_game_master_prompt():
    text = PromptProfileManager.supported_variables_markdown("skyrim_game_master_prompt")
    for variable in ("`{bio}`, `{bios}`:", "`{bios_and_summaries}`:", "`{nearby_bios}`:", "`{nearby}`:", "`{event}`:"):
        assert variable in text


def test_situation_only_variables_say_when_they_are_filled():
    for prompt_type in ("skyrim_game_master_prompt", "skyrim_game_master_npc_prompt"):
        text = PromptProfileManager.supported_variables_markdown(prompt_type)
        assert "Filled in NPC Events, and in Shout when the conversation started as an NPC Event you joined" in text, prompt_type


def test_bios_note_is_only_shown_for_the_game_master_prompt():
    for prompt_type in ("skyrim_multi_npc_prompt", "skyrim_game_master_npc_prompt"):
        assert "`{nearby_bios}`:" not in PromptProfileManager.supported_variables_markdown(prompt_type), prompt_type


def _one_npc_context_with_private_thought(default_config, llm_client, english_language_info, player: Character) -> Context:
    rememberer = MagicMock()
    rememberer.get_character_summary.return_value = "They guarded the gate."
    rememberer.get_character_private_thought.return_value = "I distrust the guard."
    rememberer.get_character_personal_reflection.return_value = ""
    rememberer.get_prompt_text.return_value = ""
    rememberer.get_private_thoughts_text.return_value = ""
    default_config.multi_npc_bios_only = False
    context = Context("1", default_config, llm_client, rememberer, english_language_info)
    context.npcs_in_conversation.add_or_update_character(player)
    context.npcs_in_conversation.add_or_update_character(_npc("Lydia", "abc"))
    return context


@pytest.mark.parametrize("prompt_type", PromptDefinitions.GAME_MASTER_PROMPT_TYPES)
def test_game_master_bios_and_summaries_has_private_thoughts_with_one_npc(prompt_type, default_config, llm_client, english_language_info, example_skyrim_player_character):
    context = _one_npc_context_with_private_thought(default_config, llm_client, english_language_info, example_skyrim_player_character)
    prompt = context.generate_system_message("{bios_and_summaries}", [], prompt_type=prompt_type)
    assert "They guarded the gate." in prompt
    assert "I distrust the guard." in prompt


@pytest.mark.parametrize("prompt_type", ["skyrim_prompt", "skyrim_game_master_npc_prompt"])
def test_other_prompts_keep_the_one_npc_bios_and_summaries(prompt_type, default_config, llm_client, english_language_info, example_skyrim_player_character):
    context = _one_npc_context_with_private_thought(default_config, llm_client, english_language_info, example_skyrim_player_character)
    prompt = context.generate_system_message("{bios_and_summaries}", [], prompt_type=prompt_type)
    assert "They guarded the gate." in prompt
    assert "I distrust the guard." not in prompt


def test_default_game_master_director_prompts_leave_out_the_player(default_context, example_skyrim_player_character):
    guard = _npc("Guard", "def")
    context = _group_context(default_context, example_skyrim_player_character, guard)
    game_master_prompt = PromptDefinitions.get_skyrim_game_master_prompt_config_value().default_value
    npc_prompt = PromptDefinitions.get_skyrim_game_master_npc_prompt_config_value().default_value
    director = {"director"}
    assert "Dragonborn" not in context.generate_system_message(game_master_prompt, [], prompt_type="skyrim_game_master_prompt", conditions=director)
    npc_text = context.generate_system_message(npc_prompt, [], prompt_type="skyrim_game_master_npc_prompt", focus_character=guard, conditions=director)
    # The NPC prompt says the player is not around, but the player is not one of the people talking
    assert "between these characters: Guard and Lydia." in npc_text
    assert "Dragonborn is not around" in npc_text


def test_all_names_without_player_is_only_supported_in_game_master_npc_prompt():
    for prompt_type in ("skyrim_prompt", "skyrim_multi_npc_prompt", "skyrim_multi_npc_director_prompt", "skyrim_game_master_prompt", "skyrim_radiant_prompt"):
        text = PromptProfileManager.supported_variables_markdown(prompt_type)
        assert "all_names_without_player" not in text, prompt_type
