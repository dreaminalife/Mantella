"""Memory and inner-thought prompts should resolve {player_name} inside bios."""
import os
from unittest.mock import MagicMock

from src.characters_manager import Characters
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage, join_message
from src.remember.inner_monologue import InnerMonologue
from src.remember.summaries import Summaries, player_name_from_thread


def _thread_with_named_player(config, npc, player_name: str = "") -> message_thread:
    thread = message_thread(config, None)
    thread.add_message(join_message(npc, config))
    for i in range(5):
        thread.add_message(UserMessage(config, f"Player line number {i} with extra words.", player_name))
    return thread


def _system_prompt(mock_client) -> str:
    thread = mock_client.request_call.call_args[0][0]
    return thread.get_openai_messages()[0]["content"]


def test_player_name_from_thread_uses_latest_user_message(default_config, example_skyrim_npc_character):
    thread = _thread_with_named_player(default_config, example_skyrim_npc_character, "Dragonborn")
    assert player_name_from_thread(thread) == "Dragonborn"


def test_player_name_from_thread_falls_back_when_missing(default_config, example_skyrim_npc_character):
    thread = _thread_with_named_player(default_config, example_skyrim_npc_character, "")
    assert player_name_from_thread(thread) == "the player"


def test_memory_prompt_resolves_player_name_in_bios(
    skyrim,
    default_config,
    llm_client,
    english_language_info,
    example_skyrim_npc_character,
    monkeypatch,
):
    monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
    example_skyrim_npc_character.bio = "Sworn to carry {player_name}'s burdens."
    mock_client = MagicMock()
    mock_client.request_call.return_value = "The player asked the guard for directions."
    mock_client.token_limit = 4096
    mock_client.get_count_tokens.return_value = 1
    rememberer = Summaries(
        skyrim,
        default_config,
        llm_client,
        english_language_info["language"],
        summary_client=mock_client,
    )
    thread = _thread_with_named_player(default_config, example_skyrim_npc_character, "Dragonborn")
    characters = Characters()
    characters.add_or_update_character(example_skyrim_npc_character)
    rememberer.save_conversation_state(thread, characters, "player-name-memory", save_timestamp=12345)

    prompt = _system_prompt(mock_client)
    assert "Sworn to carry Dragonborn's burdens." in prompt
    assert "{player_name}" not in prompt
    assert example_skyrim_npc_character.bio == "Sworn to carry {player_name}'s burdens."


def test_memory_prompt_falls_back_to_the_player_in_bios(
    skyrim,
    default_config,
    llm_client,
    english_language_info,
    example_skyrim_npc_character,
    monkeypatch,
):
    monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
    example_skyrim_npc_character.bio = "Sworn to carry {player_name}'s burdens."
    mock_client = MagicMock()
    mock_client.request_call.return_value = "The player asked the guard for directions."
    mock_client.token_limit = 4096
    mock_client.get_count_tokens.return_value = 1
    rememberer = Summaries(
        skyrim,
        default_config,
        llm_client,
        english_language_info["language"],
        summary_client=mock_client,
    )
    thread = _thread_with_named_player(default_config, example_skyrim_npc_character, "")
    characters = Characters()
    characters.add_or_update_character(example_skyrim_npc_character)
    rememberer.save_conversation_state(thread, characters, "player-name-memory-fallback", save_timestamp=12345)

    prompt = _system_prompt(mock_client)
    assert "Sworn to carry the player's burdens." in prompt
    assert "{player_name}" not in prompt


def test_inner_monologue_prompt_resolves_player_name_in_bios(
    skyrim,
    default_config,
    llm_client,
    english_language_info,
    example_skyrim_npc_character,
    monkeypatch,
):
    monkeypatch.setattr("src.remember.inner_monologue.time.sleep", lambda *_args, **_kwargs: None)
    example_skyrim_npc_character.bio = "Sworn to carry {player_name}'s burdens."
    mock_client = MagicMock()
    mock_client.request_call.return_value = "I will keep an eye on this stranger."
    mock_client.token_limit = 4096
    mock_client.get_count_tokens.return_value = 1
    inner = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
    rememberer = Summaries(
        skyrim,
        default_config,
        llm_client,
        english_language_info["language"],
        summary_client=mock_client,
        inner_monologue=inner,
    )
    thread = _thread_with_named_player(default_config, example_skyrim_npc_character, "Dragonborn")
    characters = Characters()
    characters.add_or_update_character(example_skyrim_npc_character)
    rememberer.save_thoughts_only(thread, characters, "player-name-thoughts", save_timestamp=12345)

    prompt = _system_prompt(mock_client)
    assert "Sworn to carry Dragonborn's burdens." in prompt
    assert "{player_name}" not in prompt
    assert example_skyrim_npc_character.bio == "Sworn to carry {player_name}'s burdens."


def test_personal_reflection_prompt_resolves_player_name_in_bios(
    skyrim,
    default_config,
    llm_client,
    english_language_info,
    example_skyrim_npc_character,
    monkeypatch,
):
    monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
    example_skyrim_npc_character.bio = "Sworn to carry {player_name}'s burdens."
    mock_client = MagicMock()
    mock_client.request_call.return_value = "I have carried this for a long time."
    mock_client.token_limit = 4096
    mock_client.get_count_tokens.return_value = 1
    from src.remember.personal_reflection import PersonalReflection
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
        "player-name-reflections",
        f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
    )
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{example_skyrim_npc_character.name}_summary_1.txt"), "w", encoding="utf-8") as f:
        f.write("ts=1\n- They asked for help.\n\n")
    thread = _thread_with_named_player(default_config, example_skyrim_npc_character, "Dragonborn")
    characters = Characters()
    characters.add_or_update_character(example_skyrim_npc_character)
    rememberer.save_reflections_only(thread, characters, "player-name-reflections", save_timestamp=12345)

    prompt = _system_prompt(mock_client)
    assert "Sworn to carry Dragonborn's burdens." in prompt
    assert "{player_name}" not in prompt
    assert example_skyrim_npc_character.bio == "Sworn to carry {player_name}'s burdens."
