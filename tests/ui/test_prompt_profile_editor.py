import gradio as gr

from src.config.config_loader import ConfigLoader
from src.config.definitions.game_definitions import GameEnum
from src.config.types.config_value_group import ConfigValueGroup
from src.ui.settings_ui_constructor import SettingsUIConstructor


def test_load_from_prompts_tab_fills_editor_without_saving(tmp_path):
    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.definitions.get_config_value_definition("memory_prompt").value = "Edited on the Prompts tab for {name}."
    constructor = SettingsUIConstructor(config)
    with gr.Blocks() as demo:
        constructor.render_prompt_profile_editor(ConfigValueGroup("prompt_profiles", "Prompt Profiles", "", []))
    handler = next(d.fn for d in demo.fns if getattr(d.fn, "__name__", "") == "on_load_prompts_tab")

    text, _error, status = handler("Memory Prompt")

    assert text == "Edited on the Prompts tab for {name}."
    assert "Save" in status
    assert config.prompt_profile_manager.get_profile_names("memory_prompt") == []
    assert not (tmp_path / "prompt_profiles.json").exists()


def _handler(demo, name):
    return next(d.fn for d in demo.fns if getattr(d.fn, "__name__", "") == name)


def test_preview_shows_a_merged_prompt_as_one_situation(tmp_path):
    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    constructor = SettingsUIConstructor(config)
    with gr.Blocks() as demo:
        constructor.render_prompt_profile_editor(ConfigValueGroup("prompt_profiles", "Prompt Profiles", "", []))
    refresh = _handler(demo, "refresh_preview")
    text = "Start\n{if shout}\nShouting {nearby}\n{else if npc_event}\nWatching\n{else}\nGroup\n{end if}\nEnd"
    assert refresh("Skyrim Game Master Prompt", text, "Shout") == "Start\nShouting {nearby}\nEnd"
    assert refresh("Skyrim Game Master Prompt", text, "Watching an NPC Event") == "Start\nWatching\nEnd"
    assert refresh("Skyrim Game Master Prompt", text, "Group conversation") == "Start\nGroup\nEnd"
    assert refresh("Skyrim Game Master Prompt", "{if shout}A", "Shout") == "No preview: {if shout} has no matching {end if}."


def test_preview_expands_shared_sections_in_the_profile_editor(tmp_path):
    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    assert config.prompt_profile_manager.save_section("crowd", "{if shout}\nPeople can hear you.\n{end if}")[0]
    constructor = SettingsUIConstructor(config)
    with gr.Blocks() as demo:
        constructor.render_prompt_profile_editor(ConfigValueGroup("prompt_profiles", "Prompt Profiles", "", []))
    refresh = _handler(demo, "refresh_preview")
    assert refresh("Skyrim Game Master Prompt", "A\n{crowd}\nB", "Shout") == "A\nPeople can hear you.\nB"
    assert refresh("Skyrim Game Master Prompt", "A\n{crowd}\nB", "Group conversation") == "A\nB" # The section's lines go, so no gap is left


def test_preview_is_only_shown_for_merged_prompts(tmp_path):
    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    constructor = SettingsUIConstructor(config)
    with gr.Blocks() as demo:
        constructor.render_prompt_profile_editor(ConfigValueGroup("prompt_profiles", "Prompt Profiles", "", []))
    on_type = _handler(demo, "on_preview_type_change")
    accordion, situations = on_type("Vision Prompt")
    assert accordion["visible"] is False and situations["choices"] == []
    accordion, situations = on_type("Skyrim Talk/Nearby/Random Event Generator Prompt")
    assert accordion["visible"] is True
    assert situations["choices"] == ["Talk Event", "Nearby Event", "Random Event", "Talk Event, Director Mode", "Nearby Event, Director Mode",
                                     "Random Event, Director Mode"] and situations["value"] == "Talk Event"


def test_every_merged_prompt_has_preview_situations_for_all_its_conditions():
    from src.config.definitions.prompt_definitions import PromptDefinitions
    for prompt_type, conditions in PromptDefinitions.PROMPT_CONDITIONS.items():
        situations = PromptDefinitions.PREVIEW_SITUATIONS[prompt_type]
        used = set().union(*(active for _label, active in situations))
        assert used == set(conditions) - {"say_to_room"} or used == set(conditions), prompt_type


def test_prompts_tab_shows_a_preview_under_each_merged_prompt(tmp_path):
    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    constructor = SettingsUIConstructor(config)
    prompts_group = next(group for group in config.definitions.base_groups if group.name == "Prompts")
    with gr.Blocks() as demo:
        prompts_group.accept_visitor(constructor)
    previews = [d for d in demo.fns if getattr(d.fn, "__name__", "") == "refresh"]
    assert len(previews) == 2 * 13 # The situation and the text each refresh the preview, for each of the 13 merged prompts
    textboxes = [block for block in demo.blocks.values() if isinstance(block, gr.Textbox) and block.label and block.label.startswith("Text sent in this situation")]
    assert len(textboxes) == 13
    assert any("You are the game master of a group conversation" in box.value for box in textboxes)


def test_shared_sections_are_their_own_box_with_a_folded_note(tmp_path):
    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    with gr.Blocks() as demo:
        SettingsUIConstructor(config).render_prompt_profile_editor(ConfigValueGroup("prompt_profiles", "Prompt Profiles", "", []))
    box = next(b for b in demo.blocks.values() if "prompt-editor-subsection" in (getattr(b, "elem_classes", None) or []))
    inside = [b for b in demo.blocks.values() if b is not box and _is_inside(b, box)]
    note = next(b for b in inside if isinstance(b, gr.Accordion) and b.label == "How shared sections work")
    assert note.open is False
    assert any(isinstance(b, gr.Dropdown) and b.label == "Edit shared section" for b in inside)
    assert not any(isinstance(b, gr.Textbox) and b.label == "Prompt text" for b in inside) # The profile editor stays above it


def test_the_two_event_generators_have_names_that_tell_them_apart():
    from src.prompt_profile_manager import PromptProfileManager
    names = PromptProfileManager.display_names()
    assert "Skyrim NPC Event Generator Prompt" in names and "Skyrim Talk/Nearby/Random Event Generator Prompt" in names
    assert "Skyrim Event Generator Prompt" not in names


def _is_inside(block, container) -> bool:
    parent = getattr(block, "parent", None)
    while parent is not None:
        if parent is container:
            return True
        parent = getattr(parent, "parent", None)
    return False
