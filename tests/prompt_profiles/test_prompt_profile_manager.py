from src.prompt_profile_manager import PromptProfileManager
from src.config.config_loader import ConfigLoader
from src.config.definitions.game_definitions import GameEnum


def test_save_load_rename_delete(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))

    ok, msg = manager.save_profile("memory_prompt", "Concise", "Summarize {name} in {language}.")
    assert ok, msg
    assert manager.get_profile_names("memory_prompt") == ["Concise"]
    assert manager.get_profile_text("memory_prompt", "Concise") == "Summarize {name} in {language}."

    reloaded = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    assert reloaded.get_profile_text("memory_prompt", "Concise") == "Summarize {name} in {language}."

    ok, msg = manager.rename_profile("memory_prompt", "Concise", "Short")
    assert ok, msg
    assert manager.get_profile_names("memory_prompt") == ["Short"]
    assert manager.get_profile_text("memory_prompt", "Concise") is None

    ok, msg = manager.delete_profile("memory_prompt", "Short")
    assert ok, msg
    assert manager.get_profile_names("memory_prompt") == []


def test_duplicate_name_rejected_when_not_overwriting(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    assert manager.save_profile("memory_prompt", "Concise", "Summarize {name}.")[0]
    ok, msg = manager.save_profile("memory_prompt", "Concise", "Other {name}.", overwrite=False)
    assert not ok
    assert "already exists" in msg
    assert manager.get_profile_text("memory_prompt", "Concise") == "Summarize {name}."


def test_same_name_allowed_on_different_types(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    assert manager.save_profile("memory_prompt", "Default", "Summarize {name}.")[0]
    assert manager.save_profile("skyrim_prompt", "Default", "You are {name}.")[0]
    assert manager.get_profile_text("memory_prompt", "Default") == "Summarize {name}."
    assert manager.get_profile_text("skyrim_prompt", "Default") == "You are {name}."


def test_invalid_variable_rejected(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg = manager.save_profile("memory_prompt", "Bad", "Use {not_a_real_variable}.")
    assert not ok
    assert "not_a_real_variable" in msg


def test_radiant_start_rejects_any_variable(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg = manager.save_profile("radiant_start_prompt", "Bad", "Hello {name}.")
    assert not ok
    assert "No variables allowed" in msg


def test_vision_allows_game_variable(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg = manager.save_profile("vision_prompt", "Scene", "Describe this {game} scene.")
    assert ok, msg


def test_vision_rejects_unknown_variable(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg = manager.save_profile("vision_prompt", "Bad", "Describe this {name} scene.")
    assert not ok
    assert "name" in msg


def test_default_edit_name_prefers_active_profile(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    manager.save_profile("skyrim_prompt", "Alpha", "You are {name} alpha.")
    manager.save_profile("skyrim_prompt", "Zeta", "You are {name} zeta.")
    assert manager.get_default_edit_name("skyrim_prompt") == "Alpha"

    manager.set_active("skyrim_prompt", "Zeta")
    assert manager.get_default_edit_name("skyrim_prompt") == "Zeta"

    reloaded = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    assert reloaded.get_active_name("skyrim_prompt") == "Zeta"
    assert reloaded.get_default_edit_name("skyrim_prompt") == "Zeta"


def test_default_edit_name_is_none_when_empty(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    assert manager.get_default_edit_name("skyrim_prompt") is None


def test_activate_and_delete_active_falls_back(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    manager.save_profile("memory_prompt", "Concise", "Summarize {name}.")
    ok, msg, changed = manager.set_active("memory_prompt", "Concise")
    assert ok, msg
    assert changed
    assert manager.get_active_text("memory_prompt") == "Summarize {name}."

    ok, msg = manager.delete_profile("memory_prompt", "Concise")
    assert ok, msg
    assert manager.get_active_name("memory_prompt") is None
    assert manager.get_active_text("memory_prompt") is None


def test_rename_keeps_active_profile(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    manager.save_profile("memory_prompt", "Concise", "Summarize {name}.")
    manager.set_active("memory_prompt", "Concise")
    ok, msg = manager.rename_profile("memory_prompt", "Concise", "Short")
    assert ok, msg
    assert manager.get_active_name("memory_prompt") == "Short"
    assert manager.get_active_text("memory_prompt") == "Summarize {name}."


def test_set_active_none_uses_prompts_tab(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    manager.save_profile("memory_prompt", "Concise", "Summarize {name}.")
    manager.set_active("memory_prompt", "Concise")
    ok, msg, changed = manager.set_active("memory_prompt", None)
    assert ok, msg
    assert changed
    assert manager.get_active_text("memory_prompt") is None


def test_unknown_active_name_is_ignored(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg, changed = manager.set_active("memory_prompt", "Missing")
    assert not ok
    assert not changed


def test_config_overlay_uses_active_profile(tmp_path):
    profiles_path = tmp_path / "prompt_profiles.json"
    manager = PromptProfileManager(str(profiles_path))
    manager.save_profile("memory_prompt", "Concise", "Summarize {name} briefly.")
    manager.set_active("memory_prompt", "Concise")

    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(profiles_path))
    tab_value = config.definitions.get_string_value("memory_prompt")
    assert config.memory_prompt == "Summarize {name} briefly."
    assert tab_value != config.memory_prompt


def test_config_overlay_does_not_change_prompts_tab(tmp_path):
    profiles_path = tmp_path / "prompt_profiles.json"
    manager = PromptProfileManager(str(profiles_path))
    original = None
    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(profiles_path))
    original = config.definitions.get_string_value("skyrim_prompt")
    manager.save_profile("skyrim_prompt", "Roleplay", "You are {name}.")
    manager.set_active("skyrim_prompt", "Roleplay")
    config.notify_prompt_profiles_changed()

    assert config.prompt == "You are {name}."
    assert config.definitions.get_string_value("skyrim_prompt") == original
    assert config.has_any_config_value_changed is True
    assert config.consume_log_next_system_prompt() is True
    assert config.consume_log_next_system_prompt() is False


def test_prompt_profile_change_keeps_log_flag_across_config_update(tmp_path):
    profiles_path = tmp_path / "prompt_profiles.json"
    manager = PromptProfileManager(str(profiles_path))
    manager.save_profile("skyrim_prompt", "Roleplay", "You are {name}.")
    manager.set_active("skyrim_prompt", "Roleplay")
    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(profiles_path))
    config.notify_prompt_profiles_changed()

    config.update_config_loader_with_changed_config_values()

    assert config.has_any_config_value_changed is False
    assert config.consume_log_next_system_prompt() is True


def test_inactive_types_keep_prompts_tab_values(tmp_path):
    profiles_path = tmp_path / "prompt_profiles.json"
    manager = PromptProfileManager(str(profiles_path))
    manager.save_profile("memory_prompt", "Concise", "Summarize {name}.")
    manager.set_active("memory_prompt", "Concise")

    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(profiles_path))
    assert config.memory_prompt == "Summarize {name}."
    assert config.prompt == config.definitions.get_string_value("skyrim_prompt")


def test_skyrim_prompt_overlay_is_ignored_for_fallout(tmp_path):
    profiles_path = tmp_path / "prompt_profiles.json"
    manager = PromptProfileManager(str(profiles_path))
    manager.save_profile("skyrim_prompt", "Roleplay", "You are {name}.")
    manager.set_active("skyrim_prompt", "Roleplay")

    config = ConfigLoader(str(tmp_path), game_override=GameEnum.FALLOUT4, prompt_profiles_path=str(profiles_path))
    assert config.prompt == config.definitions.get_string_value("fallout4_prompt")
    assert config.prompt != "You are {name}."


def test_inner_monologue_overlay_applies_for_fallout(tmp_path):
    profiles_path = tmp_path / "prompt_profiles.json"
    manager = PromptProfileManager(str(profiles_path))
    manager.save_profile("inner_monologue_prompt", "Short", "You are {name}. Write a brief thought.")
    manager.set_active("inner_monologue_prompt", "Short")

    config = ConfigLoader(str(tmp_path), game_override=GameEnum.FALLOUT4, prompt_profiles_path=str(profiles_path))
    assert config.inner_monologue_prompt == "You are {name}. Write a brief thought."


def test_save_from_editor_updates_selected_profile(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    manager.save_profile("memory_prompt", "Concise", "Summarize {name}.")

    ok, msg, created = manager.save_from_editor("memory_prompt", "Concise", "Concise", "Summarize {name} briefly.")
    assert ok, msg
    assert created is False
    assert manager.get_profile_text("memory_prompt", "Concise") == "Summarize {name} briefly."
    assert manager.get_active_name("memory_prompt") is None


def test_save_from_editor_creates_when_name_is_new(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    manager.save_profile("memory_prompt", "Concise", "Summarize {name}.")

    ok, msg, created = manager.save_from_editor("memory_prompt", "Concise", "Short", "Summarize {name} in {language}.")
    assert ok, msg
    assert created is True
    assert manager.get_profile_text("memory_prompt", "Concise") == "Summarize {name}."
    assert manager.get_profile_text("memory_prompt", "Short") == "Summarize {name} in {language}."
    assert manager.get_active_name("memory_prompt") is None


def test_save_from_editor_rejects_other_existing_name(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    manager.save_profile("memory_prompt", "Concise", "Summarize {name}.")
    manager.save_profile("memory_prompt", "Short", "Old {name}.")

    ok, msg, created = manager.save_from_editor("memory_prompt", "Concise", "Short", "New {name}.")
    assert not ok
    assert created is False
    assert "already exists" in msg
    assert manager.get_profile_text("memory_prompt", "Short") == "Old {name}."
    assert manager.get_profile_text("memory_prompt", "Concise") == "Summarize {name}."


def test_save_from_editor_rejects_invalid_variables_without_writing(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    manager.save_profile("memory_prompt", "Concise", "Summarize {name}.")

    ok, msg, created = manager.save_from_editor("memory_prompt", "Concise", "Concise", "Use {not_a_real_variable}.")
    assert not ok
    assert created is False
    assert "not_a_real_variable" in msg
    assert manager.get_profile_text("memory_prompt", "Concise") == "Summarize {name}."


def test_prompts_tab_edit_persists_while_profile_is_active(tmp_path):
    profiles_path = tmp_path / "prompt_profiles.json"
    manager = PromptProfileManager(str(profiles_path))
    manager.save_profile("skyrim_prompt", "Roleplay", "You are {name}.")
    manager.set_active("skyrim_prompt", "Roleplay")

    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(profiles_path))
    original_tab = config.definitions.get_string_value("skyrim_prompt")
    edited_tab = original_tab + "\nEdited on the Prompts tab."
    config.definitions.get_config_value_definition("skyrim_prompt").value = edited_tab
    config.update_config_loader_with_changed_config_values()

    assert config.definitions.get_string_value("skyrim_prompt") == edited_tab
    assert config.prompt == "You are {name}."
    assert config.prompt_profile_manager.get_active_name("skyrim_prompt") == "Roleplay"


def test_prompts_tab_is_used_when_no_profile_is_active(tmp_path):
    config = ConfigLoader(
        str(tmp_path),
        game_override=GameEnum.SKYRIM,
        prompt_profiles_path=str(tmp_path / "prompt_profiles.json"),
    )
    edited_tab = "You are {name} from the Prompts tab."
    config.definitions.get_config_value_definition("skyrim_prompt").value = edited_tab
    config.update_config_loader_with_changed_config_values()

    assert config.prompt == edited_tab
    assert config.prompt_profile_manager.get_active_name("skyrim_prompt") is None


def test_supported_variables_markdown_lists_allowed_names():
    text = PromptProfileManager.supported_variables_markdown("memory_prompt")
    assert text.startswith("**Supported variables:**")
    assert "`{name}`" in text
    assert "`{language}`" in text
    assert "`{player_name}`" in text
    assert "`{private_thoughts}`" not in text


def test_supported_variables_markdown_for_no_variables():
    text = PromptProfileManager.supported_variables_markdown("radiant_start_prompt")
    assert "none" in text.lower()


def test_supported_variables_markdown_for_vision_prompt():
    text = PromptProfileManager.supported_variables_markdown("vision_prompt")
    assert text == "**Supported variables:** `{game}`"


def test_inner_monologue_prompt_is_a_known_profile_type():
    identifiers = [identifier for identifier, _name, _allowed in PromptProfileManager.prompt_types()]
    assert "inner_monologue_prompt" in identifiers
    assert "Inner Monologue Prompt" in PromptProfileManager.display_names()


def test_inner_monologue_profile_allows_generation_variables(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    text = (
        "You are {name}. Write private thoughts in {language} about {player_name} in {game}. "
        "Bios: {bios}. Names: {names}. Previous: {previous_thoughts}. "
        "Summary: {conversation_summary}. Lore: {lorebook}."
    )
    ok, msg = manager.save_profile("inner_monologue_prompt", "Diary", text)
    assert ok, msg
    assert manager.get_profile_text("inner_monologue_prompt", "Diary") == text


def test_inner_monologue_profile_rejects_conversation_summaries(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg = manager.save_profile("inner_monologue_prompt", "Bad", "History: {conversation_summaries}")
    assert not ok
    assert "conversation_summaries" in msg


def test_inner_monologue_profile_rejects_unknown_variable(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg = manager.save_profile("inner_monologue_prompt", "Bad", "Use {not_a_real_variable}.")
    assert not ok
    assert "not_a_real_variable" in msg


def test_inner_monologue_profile_rejects_conversation_private_thoughts_variable(tmp_path):
    """{private_thoughts} is the single-NPC conversation placeholder, not a generation variable."""
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg = manager.save_profile("inner_monologue_prompt", "Bad", "Reuse {private_thoughts}.")
    assert not ok
    assert "private_thoughts" in msg


def test_skyrim_prompt_profile_allows_private_thoughts(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg = manager.save_profile("skyrim_prompt", "WithThoughts", "You are {name}. {bio} {conversation_summary} {private_thoughts}")
    assert ok, msg


def test_config_overlay_uses_active_inner_monologue_profile(tmp_path):
    profiles_path = tmp_path / "prompt_profiles.json"
    manager = PromptProfileManager(str(profiles_path))
    manager.save_profile("inner_monologue_prompt", "Short", "You are {name}. Write a brief thought.")
    manager.set_active("inner_monologue_prompt", "Short")

    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(profiles_path))
    tab_value = config.definitions.get_string_value("inner_monologue_prompt")
    assert config.inner_monologue_prompt == "You are {name}. Write a brief thought."
    assert tab_value != config.inner_monologue_prompt


def test_inner_monologue_overlay_does_not_change_prompts_tab(tmp_path):
    profiles_path = tmp_path / "prompt_profiles.json"
    manager = PromptProfileManager(str(profiles_path))
    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(profiles_path))
    original = config.definitions.get_string_value("inner_monologue_prompt")
    manager.save_profile("inner_monologue_prompt", "Short", "You are {name}. Write a brief thought.")
    manager.set_active("inner_monologue_prompt", "Short")
    config.notify_prompt_profiles_changed()

    assert config.inner_monologue_prompt == "You are {name}. Write a brief thought."
    assert config.definitions.get_string_value("inner_monologue_prompt") == original
    assert config.has_any_config_value_changed is True


def test_supported_variables_markdown_for_inner_monologue_prompt():
    text = PromptProfileManager.supported_variables_markdown("inner_monologue_prompt")
    assert text.startswith("**Supported variables:**")
    assert "`{previous_thoughts}`" in text
    assert "`{conversation_summary}`" in text
    assert "`{conversation_summaries}`" not in text
    assert "`{name}`" in text
    assert "`{private_thoughts}`" not in text


def test_personal_reflection_prompt_is_a_known_profile_type():
    identifiers = [identifier for identifier, _name, _allowed in PromptProfileManager.prompt_types()]
    assert "personal_reflection_prompt" in identifiers
    assert "Personal Reflection Prompt" in PromptProfileManager.display_names()


def test_personal_reflection_profile_allows_generation_variables(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    text = (
        "You are {name}. Write a reflection in {language} about {player_name} in {game}. "
        "Bios: {bios}. Names: {names}. Previous: {previous_reflection}. "
        "Summaries: {conversation_summary}. Lore: {lorebook}."
    )
    ok, msg = manager.save_profile("personal_reflection_prompt", "LongTerm", text)
    assert ok, msg
    assert manager.get_profile_text("personal_reflection_prompt", "LongTerm") == text


def test_personal_reflection_profile_rejects_conversation_summaries(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg = manager.save_profile("personal_reflection_prompt", "Bad", "History: {conversation_summaries}")
    assert not ok
    assert "conversation_summaries" in msg


def test_personal_reflection_profile_rejects_unknown_variable(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg = manager.save_profile("personal_reflection_prompt", "Bad", "Use {not_a_real_variable}.")
    assert not ok
    assert "not_a_real_variable" in msg


def test_personal_reflection_profile_allows_private_thoughts(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg = manager.save_profile("personal_reflection_prompt", "WithThoughts", "History: {private_thoughts}")
    assert ok, msg
    assert manager.get_profile_text("personal_reflection_prompt", "WithThoughts") == "History: {private_thoughts}"


def test_config_overlay_uses_active_personal_reflection_profile(tmp_path):
    profiles_path = tmp_path / "prompt_profiles.json"
    manager = PromptProfileManager(str(profiles_path))
    manager.save_profile("personal_reflection_prompt", "Short", "You are {name}. Write a brief reflection.")
    manager.set_active("personal_reflection_prompt", "Short")

    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(profiles_path))
    tab_value = config.definitions.get_string_value("personal_reflection_prompt")
    assert config.personal_reflection_prompt == "You are {name}. Write a brief reflection."
    assert tab_value != config.personal_reflection_prompt


def test_supported_variables_markdown_for_personal_reflection_prompt():
    text = PromptProfileManager.supported_variables_markdown("personal_reflection_prompt")
    assert text.startswith("**Supported variables:**")
    assert "`{previous_reflection}`" in text
    assert "`{conversation_summary}`" in text
    assert "`{conversation_summaries}`" not in text
    assert "`{name}`" in text
    assert "`{private_thoughts}`" in text
    assert "`{previous_thoughts}`" not in text


def test_supported_variables_markdown_for_skyrim_prompt_includes_private_thoughts():
    text = PromptProfileManager.supported_variables_markdown("skyrim_prompt")
    assert "`{private_thoughts}`" in text
    assert "`{private_thought}`" not in text
    assert "`{conversation_summary}`" in text


def test_supported_variables_markdown_for_multi_and_radiant_omits_private_thoughts():
    for prompt_type in (
        "skyrim_multi_npc_prompt",
        "skyrim_multi_npc_director_prompt",
        "skyrim_radiant_prompt",
    ):
        text = PromptProfileManager.supported_variables_markdown(prompt_type)
        assert "`{private_thoughts}`" not in text, prompt_type
        assert "`{conversation_summaries}`" in text or "`{bios_and_summaries}`" in text


def test_multi_and_radiant_profiles_reject_private_thoughts(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    for prompt_type in (
        "skyrim_multi_npc_prompt",
        "skyrim_multi_npc_director_prompt",
        "skyrim_radiant_prompt",
    ):
        ok, msg = manager.save_profile(prompt_type, "Bad", "Talk. {private_thoughts}")
        assert not ok, prompt_type
        assert "private_thoughts" in msg


def test_skyrim_prompt_profile_rejects_singular_private_thought(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg = manager.save_profile("skyrim_prompt", "Bad", "You are {name}. {private_thought}")
    assert not ok
    assert "private_thought" in msg


def test_shared_section_expands_in_profile_and_reloads(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    ok, msg, _created = manager.save_section("roleplay_section", "{name} acts on their own. {player_name} can be wrong.")
    assert ok, msg
    ok, msg = manager.save_profile("skyrim_prompt", "Roleplay", "You are {name}.\n{roleplay_section}")
    assert ok, msg
    assert manager.expand_sections("You are {name}.\n{roleplay_section}") == (
        "You are {name}.\n{name} acts on their own. {player_name} can be wrong."
    )

    reloaded = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    assert reloaded.get_section_text("roleplay_section").startswith("{name} acts")
    assert "{roleplay_section}" in reloaded.variables_markdown("skyrim_prompt")


def test_every_prompt_type_has_an_about_note_apart_from_the_variables(tmp_path):
    from src.config.definitions.prompt_definitions import PromptDefinitions
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    profile_types = {identifier for identifier, _name, _allowed in PromptProfileManager.prompt_types()}
    assert set(PromptDefinitions.USAGE_NOTES) == profile_types
    for prompt_type in profile_types:
        about = PromptProfileManager.about_markdown(prompt_type)
        assert about.startswith("**What it does:**"), prompt_type
        assert "**When it is used:**" in about and "**Needs:**" in about, prompt_type
        assert "**Supported variables:**" not in about, prompt_type
        assert manager.variables_markdown(prompt_type).startswith("**Supported variables:**"), prompt_type


def test_game_master_request_examples_mark_the_prompt_once_per_example():
    from src.config.definitions.prompt_request_examples import EXAMPLES, SLOT
    profile_types = {identifier for identifier, _name, _allowed in PromptProfileManager.prompt_types()}
    for prompt_type, examples in EXAMPLES.items():
        assert prompt_type in profile_types, prompt_type
        about = PromptProfileManager.about_markdown(prompt_type)
        assert "**Where it goes in the request.**" in about, prompt_type
        for _title, template, _note in examples:
            assert any(match.group(1) == prompt_type for match in SLOT.finditer(template)), prompt_type
            assert all(match.group(1) in profile_types for match in SLOT.finditer(template))
        assert about.count('<details class="prompt-example">') == len(examples), prompt_type
        marked = sum(match.group(1) == prompt_type for _title, template, _note in examples for match in SLOT.finditer(template))
        assert about.count('class="prompt-example-this"') == 1 + marked, prompt_type
        assert "{{" not in about, prompt_type
    # Prompts that are not part of a Game Master request have no example
    assert "Where it goes" not in PromptProfileManager.about_markdown("memory_prompt")


def test_shared_section_rejects_nested_section_and_builtin_name(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    assert manager.save_section("roleplay_section", "Stay in character.")[0]
    ok, msg, _created = manager.save_section("other_section", "See {roleplay_section}.")
    assert not ok
    assert "roleplay_section" in msg

    ok, msg, _created = manager.save_section("name", "nope")
    assert not ok
    assert "built-in" in msg

    ok, msg, _created = manager.save_section("roleplay section", "nope")
    assert not ok


def test_profile_save_rejects_section_variable_the_type_does_not_allow(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    assert manager.save_section("roleplay_section", "{name} knows {player_name}.")[0]
    ok, msg = manager.save_profile("radiant_start_prompt", "Steer", "Begin.\n{roleplay_section}")
    assert not ok
    assert "name" in msg or "player_name" in msg

    ok, msg = manager.save_profile("vision_prompt", "Scene", "Look. {roleplay_section}")
    assert not ok


def test_deleted_section_makes_profile_invalid_and_resolve_names_it(tmp_path):
    from src.prompt_profile_manager import PromptSectionError

    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    assert manager.save_section("roleplay_section", "Stay in character.")[0]
    assert manager.save_profile("skyrim_prompt", "Roleplay", "You are {name}.\n{roleplay_section}")[0]
    assert manager.set_active("skyrim_prompt", "Roleplay")[0]
    assert manager.delete_section("roleplay_section")[0]

    result = manager.validate_text("skyrim_prompt", manager.get_profile_text("skyrim_prompt", "Roleplay"))
    assert not result.is_success
    assert "roleplay_section" in result.error_message

    try:
        manager.resolve_prompt("skyrim_prompt", "You are {name}.\n{roleplay_section}")
        assert False, "expected PromptSectionError"
    except PromptSectionError as error:
        assert error.variable == "roleplay_section"
        assert "{roleplay_section}" in str(error)


def test_resolve_skips_prompts_tab_when_no_profile_is_active(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    assert manager.save_section("roleplay_section", "Stay in character.")[0]
    resolved = manager.resolve_prompt("skyrim_prompt", "You are {name}. {roleplay_section}")
    assert resolved == "You are {name}. {roleplay_section}"


def test_decensor_prompts_are_profile_types_and_overlay_the_prompts_tab(tmp_path):
    identifiers = [identifier for identifier, _name, _allowed in PromptProfileManager.prompt_types()]
    assert "decensor_handshake_prompt" in identifiers
    assert "decensor_assistant_prompt" in identifiers
    assert "Decensor Handshake Prompt" in PromptProfileManager.display_names()
    assert "Decensor Assistant Prompt" in PromptProfileManager.display_names()

    profiles_path = tmp_path / "prompt_profiles.json"
    manager = PromptProfileManager(str(profiles_path))
    assert manager.save_profile("decensor_handshake_prompt", "Short", "Ready to roleplay.")[0]
    assert manager.set_active("decensor_handshake_prompt", "Short")[0]
    assert manager.save_profile("decensor_assistant_prompt", "Short", "I will stay in character.")[0]
    assert manager.set_active("decensor_assistant_prompt", "Short")[0]

    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(profiles_path))
    assert config.decensor_handshake_prompt == "Ready to roleplay."
    assert config.decensor_assistant_prompt == "I will stay in character."
    assert config.definitions.get_string_value("decensor_handshake_prompt") != config.decensor_handshake_prompt
    assert config.enable_decensor_bypass is False


def test_rename_section_does_not_rewrite_profiles(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    assert manager.save_section("roleplay_section", "Stay in character.")[0]
    assert manager.save_profile("memory_prompt", "Short", "Summarize {name}. {roleplay_section}")[0]
    ok, msg = manager.rename_section("roleplay_section", "guidelines")
    assert ok, msg
    assert manager.get_profile_text("memory_prompt", "Short") == "Summarize {name}. {roleplay_section}"
    assert not manager.validate_text("memory_prompt", "Summarize {name}. {roleplay_section}").is_success
