"""Conditional blocks in user-edited prompts: profiles, the Prompts tab and shared sections"""
import pytest

from src.config.config_loader import ConfigLoader
from src.config.definitions.game_definitions import GameEnum
from src.config.definitions.prompt_definitions import PromptDefinitions
from src.prompt_profile_manager import PromptConditionError, PromptProfileManager, PromptSectionError

GAME_MASTER = "skyrim_game_master_prompt"
NPC = "skyrim_game_master_npc_prompt"


def manager(tmp_path) -> PromptProfileManager:
    return PromptProfileManager(str(tmp_path / "prompt_profiles.json"))


def test_an_active_profile_with_tags_sends_the_right_branch(tmp_path):
    profiles = manager(tmp_path)
    text = "Scene in {location}.\n{if shout}\nPeople nearby: {nearby}\n{else}\nJust the group.\n{end if}"
    assert profiles.save_profile(GAME_MASTER, "Mine", text)[0]
    assert profiles.set_active(GAME_MASTER, "Mine")[0]
    resolved = profiles.resolve_prompt(GAME_MASTER, text)
    assert PromptDefinitions.apply_prompt_conditions(GAME_MASTER, resolved, {"shout"}) == "Scene in {location}.\nPeople nearby: {nearby}"
    assert PromptDefinitions.apply_prompt_conditions(GAME_MASTER, resolved, set()) == "Scene in {location}.\nJust the group."


def test_tags_pass_the_profile_check_and_the_prompts_tab_check(tmp_path):
    text = "{if director}\nYou direct {names}.\n{else}\nYou talk with {names_w_player}.\n{end if}"
    assert manager(tmp_path).validate_text(GAME_MASTER, text).is_success
    assert PromptDefinitions.get_skyrim_game_master_prompt_config_value().does_value_cause_error(text).is_success


def test_variables_in_every_branch_are_checked(tmp_path):
    result = manager(tmp_path).validate_text(GAME_MASTER, "{if shout}\n{nearby}\n{else}\n{unknown_thing}\n{end if}")
    assert not result.is_success and "unknown_thing" in result.error_message


@pytest.mark.parametrize("prompt_type, text, message", [
    (GAME_MASTER, "{if shout}\nA", "{if shout} has no matching {end if}."),
    (GAME_MASTER, "{if talk_event}A{end if}", "'talk_event' is not a condition this prompt knows. Use one of: director, npc_event, say_to_room, shout."),
    (NPC, "{if say_to_room}A{end if}", "'say_to_room' is never true in this prompt: whoever answers has joined the conversation"),
    ("skyrim_prompt", "{if shout}A{end if}", "Conditional blocks like {if ...} only work in the Game Master"),
    ("resummarize_prompt", "{if after_event}A{end if}", "Conditional blocks like {if ...} only work in the Game Master"),
])
def test_broken_tags_and_wrong_conditions_are_explained(tmp_path, prompt_type, text, message):
    result = manager(tmp_path).validate_text(prompt_type, text)
    assert not result.is_success
    assert message in result.error_message
    assert "Broken prompt variable" not in result.error_message


def test_a_profile_with_broken_tags_is_not_saved(tmp_path):
    profiles = manager(tmp_path)
    ok, message = profiles.save_from_editor(GAME_MASTER, None, "Broken", "{if shout}\nA")[:2]
    assert not ok and "has no matching {end if}" in message
    assert profiles.get_profile_names(GAME_MASTER) == []


def test_a_broken_tag_refuses_the_llm_call_with_its_own_message(tmp_path):
    profiles = manager(tmp_path)
    profiles._profiles[GAME_MASTER]["Broken"] = "{if shout}\nA" # Edited outside the editor
    profiles._active[GAME_MASTER] = "Broken"
    with pytest.raises(PromptConditionError) as error:
        profiles.resolve_prompt(GAME_MASTER, "{if shout}\nA")
    assert isinstance(error.value, PromptSectionError) # Every place that refuses the call for a broken prompt catches it
    assert str(error.value) == "Refusing LLM call. The prompt's conditional blocks are broken: {if shout} has no matching {end if}."


def test_a_broken_prompts_tab_text_refuses_the_llm_call_too():
    with pytest.raises(PromptConditionError):
        PromptDefinitions.apply_prompt_conditions(GAME_MASTER, "{else}A", {"shout"})


def test_shared_sections_can_hold_tags(tmp_path):
    profiles = manager(tmp_path)
    assert profiles.save_section("crowd", "{if shout or say_to_room}\nPeople nearby: {nearby}\n{end if}")[0]
    text = "Scene.\n{crowd}\nEnd."
    assert profiles.save_profile(GAME_MASTER, "Mine", text)[0]
    assert profiles.set_active(GAME_MASTER, "Mine")[0]
    resolved = profiles.resolve_prompt(GAME_MASTER, text)
    assert PromptDefinitions.apply_prompt_conditions(GAME_MASTER, resolved, {"say_to_room"}) == "Scene.\nPeople nearby: {nearby}\nEnd."
    assert PromptDefinitions.apply_prompt_conditions(GAME_MASTER, resolved, {"npc_event"}) == "Scene.\nEnd."


def test_a_section_with_tags_is_checked_where_it_is_used(tmp_path):
    profiles = manager(tmp_path)
    assert profiles.validate_section_text("{if shout}\n{nearby}\n{end if}").is_success
    assert profiles.save_section("crowd", "{if shout}\n{nearby}\n{end if}")[0]
    assert not profiles.validate_text("skyrim_prompt", "You are {name}.\n{crowd}").is_success # Not a merged prompt


def test_the_merged_prompts_are_the_profile_types_and_the_absorbed_ones_are_gone(tmp_path):
    names = PromptProfileManager.display_names()
    for name in ("Skyrim Game Master Prompt", "Skyrim Game Master NPC Prompt", "Skyrim Talk/Nearby/Random Event Generator Prompt",
                 "Skyrim Game Master Decision Instructions", "Skyrim Game Master Decision Player Criteria", "Skyrim Decision Nearby Criteria"):
        assert name in names
    for name in ("Skyrim Shout NPC Prompt", "Skyrim Say To Room Game Master Prompt", "Skyrim NPC Event Game Master Prompt",
                 "Skyrim Nearby Event Generator Prompt", "Skyrim Game Master Director Prompt"):
        assert name not in names


def test_the_editor_explains_the_conditions_of_each_merged_prompt():
    for prompt_type, conditions in PromptDefinitions.PROMPT_CONDITIONS.items():
        about = PromptProfileManager.about_markdown(prompt_type)
        assert "**Conditional blocks:**" in about, prompt_type
        for condition in conditions:
            assert f"- `{condition}`:" in about, (prompt_type, condition)
    assert "**Conditional blocks:**" not in PromptProfileManager.about_markdown("skyrim_prompt")


def test_a_merged_prompt_edited_on_the_prompts_tab_uses_its_branches(tmp_path):
    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.definitions.get_config_value_definition(NPC).value = "{if npc_event}\nScene\n{else}\nTalk\n{end if}"
    config.update_config_loader_with_changed_config_values()
    assert PromptDefinitions.apply_prompt_conditions(NPC, config.game_master_npc_prompt, {"npc_event"}) == "Scene"


@pytest.mark.parametrize("name", ["else", "Else", "endif", "elif", "end", "if", "fi", "elseif"])
def test_a_shared_section_cannot_take_a_name_read_as_a_tag(tmp_path, name):
    """A section named 'else' would replace every {else} before the conditional blocks are read"""
    result = manager(tmp_path).validate_section_name(name)
    assert not result.is_success and "conditional blocks" in result.error_message


def test_a_typo_in_a_tag_is_explained_in_the_editor_and_on_the_prompts_tab(tmp_path):
    text = "{if shout}\nYou hear {nearby}.\n{endif}"
    assert manager(tmp_path).validate_text(GAME_MASTER, text).error_message == "{endif}: write {end if}."
    assert PromptDefinitions.get_skyrim_game_master_prompt_config_value().does_value_cause_error(text).error_message == "{endif}: write {end if}."


def test_a_typo_in_a_tag_inside_a_shared_section_is_named_as_a_tag(tmp_path):
    result = manager(tmp_path).validate_section_text("Keep it {if director}short{endif}.")
    assert result.error_message == "{endif}: write {end if}."
