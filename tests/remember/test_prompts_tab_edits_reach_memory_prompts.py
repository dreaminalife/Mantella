"""Prompts tab edits to the Inner Monologue / Personal Reflection prompts reach the LLM call."""
import os
from unittest.mock import MagicMock

import pytest

from src.characters_manager import Characters
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage, join_message
from src.remember.inner_monologue import InnerMonologue
from src.remember.personal_reflection import PersonalReflection
from src.remember.summaries import Summaries

WORLD_ID = "prompts-tab-edits"


def _thread(config, npc) -> message_thread:
    thread = message_thread(config, None)
    thread.add_message(join_message(npc, config))
    for i in range(5):
        thread.add_message(UserMessage(config, f"Player line number {i} with extra words.", "Dragonborn"))
    return thread


def _system_prompts(mock_client) -> list[str]:
    return [c[0][0].get_openai_messages()[0]["content"] for c in mock_client.request_call.call_args_list]


@pytest.fixture
def setup(skyrim, default_config, llm_client, english_language_info, example_skyrim_npc_character, monkeypatch):
    monkeypatch.setattr("src.remember.inner_monologue.time.sleep", lambda *_a, **_k: None)
    monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_a, **_k: None)
    mock_client = MagicMock()
    mock_client.request_call.return_value = "Some generated text."
    mock_client.token_limit = 4096
    mock_client.get_count_tokens.return_value = 1
    language = english_language_info["language"]
    rememberer = Summaries(
        skyrim,
        default_config,
        llm_client,
        language,
        summary_client=mock_client,
        inner_monologue=InnerMonologue(skyrim, default_config, llm_client, language, mock_client),
        personal_reflection=PersonalReflection(skyrim, default_config, llm_client, language, mock_client),
    )
    npc = example_skyrim_npc_character
    folder = os.path.join(skyrim.conversation_folder_path, WORLD_ID, f"{npc.name} - {npc.ref_id}")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{npc.name}_summary_1.txt"), "w", encoding="utf-8") as f:
        f.write("ts=1\n- They asked for help.\n\n")
    characters = Characters()
    characters.add_or_update_character(npc)
    return default_config, rememberer, mock_client, _thread(default_config, npc), characters


def _edit_prompts_tab(config):
    config.definitions.get_config_value_definition("inner_monologue_prompt").value = "TAB_MONOLOGUE {name}"
    config.definitions.get_config_value_definition("personal_reflection_prompt").value = "TAB_REFLECTION {name}"
    config.definitions.get_config_value_definition("inner_monologue_enabled").value = True
    config.definitions.get_config_value_definition("personal_reflection_enabled").value = True


def test_conversation_end_uses_prompts_tab_after_game_request(setup):
    config, rememberer, mock_client, thread, characters = setup
    _edit_prompts_tab(config)
    # Every game request runs this before handling the request (routeable._can_route_be_used)
    config.update_config_loader_with_changed_config_values()

    rememberer.save_conversation_state(thread, characters, WORLD_ID, save_timestamp=100)

    prompts = _system_prompts(mock_client)
    assert any(p.startswith("TAB_MONOLOGUE") for p in prompts)
    assert any(p.startswith("TAB_REFLECTION") for p in prompts)


def test_manual_saves_use_prompts_tab_without_game_request(setup):
    config, rememberer, mock_client, thread, characters = setup
    _edit_prompts_tab(config)
    # What GameStateManager.refresh_summary_client_from_ui_config does before manual saves
    config.sync_prompt_values_from_definitions()

    rememberer.save_thoughts_only(thread, characters, WORLD_ID, save_timestamp=100)
    rememberer.save_reflections_only(thread, characters, WORLD_ID, save_timestamp=101)

    prompts = _system_prompts(mock_client)
    assert any(p.startswith("TAB_MONOLOGUE") for p in prompts)
    assert any(p.startswith("TAB_REFLECTION") for p in prompts)


def test_save_summary_now_uses_prompts_tab_memory_prompt_without_game_request(setup):
    config, rememberer, mock_client, thread, characters = setup
    config.definitions.get_config_value_definition("memory_prompt").value = "TAB_MEMORY {name}"
    # What GameStateManager.refresh_summary_client_from_ui_config does before manual saves
    config.sync_prompt_values_from_definitions()

    rememberer.save_conversation_state(thread, characters, WORLD_ID, save_timestamp=100, save_thoughts=False, save_reflections=False)

    prompts = _system_prompts(mock_client)
    assert any(p.startswith("TAB_MEMORY") for p in prompts)


def test_active_profile_overrides_prompts_tab(setup):
    config, rememberer, mock_client, thread, characters = setup
    manager = config.prompt_profile_manager
    assert manager.save_profile("inner_monologue_prompt", "p", "PROFILE_MONOLOGUE {name}")[0]
    assert manager.set_active("inner_monologue_prompt", "p")[0]
    assert manager.save_profile("personal_reflection_prompt", "p", "PROFILE_REFLECTION {name}")[0]
    assert manager.set_active("personal_reflection_prompt", "p")[0]
    _edit_prompts_tab(config)
    config.update_config_loader_with_changed_config_values()

    rememberer.save_conversation_state(thread, characters, WORLD_ID, save_timestamp=100)

    prompts = _system_prompts(mock_client)
    assert any(p.startswith("PROFILE_MONOLOGUE") for p in prompts)
    assert any(p.startswith("PROFILE_REFLECTION") for p in prompts)
    assert not any("TAB_MONOLOGUE" in p or "TAB_REFLECTION" in p for p in prompts)
