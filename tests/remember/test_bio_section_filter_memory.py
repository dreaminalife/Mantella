"""Bio section filter can also apply to summary / thoughts / reflection prompts."""
import os
from unittest.mock import MagicMock

from src.characters_manager import Characters
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage, join_message
from src.remember.inner_monologue import InnerMonologue
from src.remember.personal_reflection import PersonalReflection
from src.remember.summaries import Summaries, format_character_bios_for_memory, thread_contains_player

SECTIONED_BIO = """## Race
- Nord

## Appearance
- Tall, grey eyes

## Personal History
- Grew up in Whiterun
"""


def _thread_with_named_player(config, npc, player_name: str = "Dragonborn") -> message_thread:
    thread = message_thread(config, None)
    thread.add_message(join_message(npc, config))
    for i in range(5):
        thread.add_message(UserMessage(config, f"Player line number {i} with extra words.", player_name))
    return thread


def _radiant_thread(config, npc) -> message_thread:
    thread = message_thread(config, None)
    thread.add_message(join_message(npc, config))
    thread.add_message(UserMessage(config, "Start talking among yourselves.", "", True))
    for i in range(5):
        thread.add_message(UserMessage(config, f"Continue the conversation {i}.", "", True))
    return thread


def _system_prompt(mock_client) -> str:
    thread = mock_client.request_call.call_args[0][0]
    return thread.get_openai_messages()[0]["content"]


def _enable_memory_filter(config) -> None:
    config.enable_bio_section_filter = True
    config.enable_bio_section_filter_for_memory = True
    config.bio_sections_to_exclude_list = ["personal history"]


def _mock_llm(text: str = "They talked at the gate.") -> MagicMock:
    mock_client = MagicMock()
    mock_client.request_call.return_value = text
    mock_client.token_limit = 4096
    mock_client.get_count_tokens.return_value = 1
    return mock_client


def test_thread_contains_player_detects_named_user_and_skips_radiant(default_config, example_skyrim_npc_character):
    assert thread_contains_player(_thread_with_named_player(default_config, example_skyrim_npc_character)) is True
    assert thread_contains_player(_radiant_thread(default_config, example_skyrim_npc_character)) is False


def test_summary_prompt_filters_when_memory_toggle_on(
    skyrim,
    default_config,
    llm_client,
    english_language_info,
    example_skyrim_npc_character,
    monkeypatch,
):
    monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
    _enable_memory_filter(default_config)
    example_skyrim_npc_character.bio = SECTIONED_BIO
    mock_client = _mock_llm()
    rememberer = Summaries(
        skyrim,
        default_config,
        llm_client,
        english_language_info["language"],
        summary_client=mock_client,
    )
    thread = _thread_with_named_player(default_config, example_skyrim_npc_character)
    characters = Characters()
    characters.add_or_update_character(example_skyrim_npc_character)
    rememberer.save_conversation_state(thread, characters, "bio-filter-summary", save_timestamp=12345)

    prompt = _system_prompt(mock_client)
    assert "## Personal History" not in prompt
    assert "Grew up in Whiterun" not in prompt
    assert "## Appearance" in prompt
    assert "Tall, grey eyes" in prompt
    assert example_skyrim_npc_character.bio == SECTIONED_BIO


def test_summary_prompt_keeps_full_bio_when_memory_toggle_off(
    skyrim,
    default_config,
    llm_client,
    english_language_info,
    example_skyrim_npc_character,
    monkeypatch,
):
    monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
    default_config.enable_bio_section_filter = True
    default_config.enable_bio_section_filter_for_memory = False
    default_config.bio_sections_to_exclude_list = ["personal history"]
    example_skyrim_npc_character.bio = SECTIONED_BIO
    mock_client = _mock_llm()
    rememberer = Summaries(
        skyrim,
        default_config,
        llm_client,
        english_language_info["language"],
        summary_client=mock_client,
    )
    thread = _thread_with_named_player(default_config, example_skyrim_npc_character)
    characters = Characters()
    characters.add_or_update_character(example_skyrim_npc_character)
    rememberer.save_conversation_state(thread, characters, "bio-filter-summary-off", save_timestamp=12345)

    prompt = _system_prompt(mock_client)
    assert "## Personal History" in prompt
    assert "Grew up in Whiterun" in prompt


def test_memory_bios_skip_filter_when_no_player(default_config, example_skyrim_npc_character):
    _enable_memory_filter(default_config)
    example_skyrim_npc_character.bio = SECTIONED_BIO
    bios = format_character_bios_for_memory(
        [example_skyrim_npc_character],
        "the player",
        default_config,
        contains_player=False,
    )
    assert "## Personal History" in bios
    assert "Grew up in Whiterun" in bios


def test_memory_bios_divider_between_characters(default_config):
    from types import SimpleNamespace
    characters = [SimpleNamespace(name="Lydia", bio="Housecarl."), SimpleNamespace(name="Faendal", bio="Hunter.")]
    bios = format_character_bios_for_memory(characters, "the player", default_config, contains_player=False)
    assert bios == "Lydia: Housecarl.\n\n==========\n\nFaendal: Hunter."


def test_inner_thoughts_prompt_filters_when_memory_toggle_on(
    skyrim,
    default_config,
    llm_client,
    english_language_info,
    example_skyrim_npc_character,
    monkeypatch,
):
    monkeypatch.setattr("src.remember.inner_monologue.time.sleep", lambda *_args, **_kwargs: None)
    _enable_memory_filter(default_config)
    example_skyrim_npc_character.bio = SECTIONED_BIO
    mock_client = _mock_llm("I will keep an eye on this stranger.")
    inner = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
    rememberer = Summaries(
        skyrim,
        default_config,
        llm_client,
        english_language_info["language"],
        summary_client=mock_client,
        inner_monologue=inner,
    )
    thread = _thread_with_named_player(default_config, example_skyrim_npc_character)
    characters = Characters()
    characters.add_or_update_character(example_skyrim_npc_character)
    rememberer.save_thoughts_only(thread, characters, "bio-filter-thoughts", save_timestamp=12345)

    prompt = _system_prompt(mock_client)
    assert "## Personal History" not in prompt
    assert "Grew up in Whiterun" not in prompt
    assert "## Appearance" in prompt


def test_personal_reflection_prompt_filters_when_memory_toggle_on(
    skyrim,
    default_config,
    llm_client,
    english_language_info,
    example_skyrim_npc_character,
    monkeypatch,
):
    monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
    _enable_memory_filter(default_config)
    example_skyrim_npc_character.bio = SECTIONED_BIO
    mock_client = _mock_llm("I have carried this for a long time.")
    manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
    rememberer = Summaries(
        skyrim,
        default_config,
        llm_client,
        english_language_info["language"],
        summary_client=mock_client,
        personal_reflection=manager,
    )
    folder = os.path.join(
        skyrim.conversation_folder_path,
        "bio-filter-reflections",
        f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
    )
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{example_skyrim_npc_character.name}_summary_1.txt"), "w", encoding="utf-8") as f:
        f.write("ts=1\n- They asked for help.\n\n")
    thread = _thread_with_named_player(default_config, example_skyrim_npc_character)
    characters = Characters()
    characters.add_or_update_character(example_skyrim_npc_character)
    rememberer.save_reflections_only(thread, characters, "bio-filter-reflections", save_timestamp=12345)

    prompt = _system_prompt(mock_client)
    assert "## Personal History" not in prompt
    assert "Grew up in Whiterun" not in prompt
    assert "## Appearance" in prompt


def test_bio_editor_generate_reflection_filters_when_memory_toggle_on(
    skyrim,
    default_config,
    llm_client,
    english_language_info,
    example_skyrim_npc_character,
    monkeypatch,
):
    monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
    _enable_memory_filter(default_config)
    mock_client = _mock_llm("I have grown more cautious.")
    manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
    result = manager.save_for_named_npc(
        example_skyrim_npc_character.name,
        SECTIONED_BIO,
        "ts=50\n- They asked about Whiterun.\n",
        "bio-filter-editor",
        save_timestamp=99,
        player_name="Dragonborn",
    )
    assert result
    prompt = _system_prompt(mock_client)
    assert "## Personal History" not in prompt
    assert "Grew up in Whiterun" not in prompt
    assert "## Appearance" in prompt


def test_bio_editor_generate_reflection_filters_appended_tag_sections(
    skyrim,
    default_config,
    llm_client,
    english_language_info,
    example_skyrim_npc_character,
    monkeypatch,
):
    monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
    _enable_memory_filter(default_config)
    skyrim._Skyrim__bio_template_manager.templates["secret"] = (
        "## Personal History\n- From the tag\n\n## Additional info\nStill visible."
    )
    mock_client = _mock_llm("I have grown more cautious.")
    manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
    result = manager.save_for_named_npc(
        example_skyrim_npc_character.name,
        "## Appearance\n- Tall",
        "ts=50\n- They asked about Whiterun.\n",
        "bio-filter-editor-tags",
        save_timestamp=99,
        player_name="Dragonborn",
        tags="secret",
    )
    assert result
    prompt = _system_prompt(mock_client)
    assert "From the tag" not in prompt
    assert "Still visible." in prompt
    assert "## Appearance" in prompt


def test_bio_editor_generate_reflection_keeps_full_bio_when_memory_toggle_off(
    skyrim,
    default_config,
    llm_client,
    english_language_info,
    example_skyrim_npc_character,
    monkeypatch,
):
    monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
    default_config.enable_bio_section_filter = True
    default_config.enable_bio_section_filter_for_memory = False
    default_config.bio_sections_to_exclude_list = ["personal history"]
    mock_client = _mock_llm("I have grown more cautious.")
    manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
    result = manager.save_for_named_npc(
        example_skyrim_npc_character.name,
        SECTIONED_BIO,
        "ts=50\n- They asked about Whiterun.\n",
        "bio-filter-editor-off",
        save_timestamp=99,
        player_name="Dragonborn",
    )
    assert result
    prompt = _system_prompt(mock_client)
    assert "## Personal History" in prompt
    assert "Grew up in Whiterun" in prompt


def test_bio_editor_generate_uses_synced_definitions_without_full_reload(
    skyrim,
    default_config,
    llm_client,
    english_language_info,
    example_skyrim_npc_character,
    monkeypatch,
):
    monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
    default_config.definitions.get_config_value_definition("enable_bio_section_filter").value = True
    default_config.definitions.get_config_value_definition("enable_bio_section_filter_for_memory").value = True
    default_config.definitions.get_config_value_definition("bio_sections_to_exclude").value = "Personal History"
    assert default_config.enable_bio_section_filter is False
    assert default_config.enable_bio_section_filter_for_memory is False

    default_config.sync_bio_section_filter_settings_from_definitions()
    mock_client = _mock_llm("I have grown more cautious.")
    manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
    result = manager.save_for_named_npc(
        example_skyrim_npc_character.name,
        SECTIONED_BIO,
        "ts=50\n- They asked about Whiterun.\n",
        "bio-filter-editor-sync",
        save_timestamp=99,
        player_name="Dragonborn",
    )
    assert result
    prompt = _system_prompt(mock_client)
    assert "## Personal History" not in prompt
    assert "Grew up in Whiterun" not in prompt
    assert "## Appearance" in prompt
