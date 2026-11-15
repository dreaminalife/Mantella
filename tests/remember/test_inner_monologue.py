"""Tests for private inner-monologue save/load and mixing into the summary timeline."""
import os
from unittest.mock import MagicMock

from src.character_manager import Character
from src.characters_manager import Characters
from src.llm.message_thread import message_thread
from src.llm.messages import AssistantMessage, UserMessage, join_message
from src.llm.sentence import Sentence
from src.llm.sentence_content import SentenceContent, SentenceTypeEnum
from src.remember.inner_monologue import (
    InnerMonologue,
    PRIVATE_THOUGHT_FOOTER,
    PRIVATE_THOUGHT_HEADER,
    get_thoughts_load_count,
    select_latest_thoughts,
    wrap_private_thought,
)
from src.remember.summaries import CharacterSummaryParameters, Summaries


def _write_thought_file(game, character, world_id: str, contents: str) -> str:
    base_name = character.name
    folder = os.path.join(game.thoughts_folder_path, world_id, f"{base_name} - {character.ref_id}")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{base_name}_thoughts_1.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(contents)
    return path


def _write_summary_file(game, character, world_id: str, contents: str) -> str:
    base_name = character.name
    folder = os.path.join(game.conversation_folder_path, world_id, f"{base_name} - {character.ref_id}")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{base_name}_summary_1.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(contents)
    return path


def _long_enough_thread(config, npc) -> message_thread:
    thread = message_thread(config, None)
    thread.add_message(join_message(npc, config))
    for i in range(5):
        thread.add_message(UserMessage(config, f"Player line number {i} with extra words."))
    return thread


class TestWrapAndSelect:
    def test_wrap_private_thought(self):
        wrapped = wrap_private_thought("I do not trust them.")
        assert wrapped.startswith(PRIVATE_THOUGHT_HEADER)
        assert wrapped.endswith(PRIVATE_THOUGHT_FOOTER)
        assert "I do not trust them." in wrapped

    def test_wrap_skips_empty(self):
        assert wrap_private_thought("  ") == ""

    def test_wrap_does_not_double_wrap(self):
        once = wrap_private_thought("secret")
        assert wrap_private_thought(once) == once

    def test_select_latest_timestamped(self):
        latest = select_latest_thoughts(
            ["old legacy"],
            [(100, "older"), (300, "newest"), (200, "mid")],
            1,
        )
        assert latest == [(300, "newest")]

    def test_select_latest_several_oldest_first(self):
        latest = select_latest_thoughts(
            ["old legacy"],
            [(100, "older"), (300, "newest"), (200, "mid")],
            2,
        )
        assert latest == [(200, "mid"), (300, "newest")]

    def test_select_latest_count_above_available(self):
        latest = select_latest_thoughts([], [(200, "b"), (100, "a")], 10)
        assert latest == [(100, "a"), (200, "b")]

    def test_select_latest_legacy_fallback(self):
        assert select_latest_thoughts(["first", "mid", "last"], [], 1) == [(0, "last")]
        assert select_latest_thoughts(["first", "mid", "last"], [], 2) == [(0, "mid"), (0, "last")]

    def test_select_latest_empty(self):
        assert select_latest_thoughts([], [], 3) == []

    def test_select_latest_zero_count(self):
        assert select_latest_thoughts(["a"], [(100, "b")], 0) == []

    def test_load_count_defaults_to_one(self):
        config = MagicMock(spec=[])
        assert get_thoughts_load_count(config) == 1

    def test_load_count_reads_config(self):
        config = MagicMock()
        config.inner_monologue_load_count = 0
        assert get_thoughts_load_count(config) == 0
        config.inner_monologue_load_count = 3
        assert get_thoughts_load_count(config) == 3


class TestThoughtFileLoad:
    def test_latest_thought_is_raw(self, skyrim, default_config, llm_client, english_language_info, example_skyrim_npc_character):
        world_id = "thoughts-world"
        _write_thought_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=100\nI was suspicious.\n\nts=200\nI will ask about the blade.\n\n",
        )
        manager = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"])
        blocks = manager.get_latest_thought_blocks(example_skyrim_npc_character, world_id, 1)
        assert blocks == [(200, "I will ask about the blade.")]
        assert PRIVATE_THOUGHT_HEADER not in blocks[0][1]

    def test_previous_thoughts_are_unwrapped(self, skyrim, default_config, llm_client, english_language_info, example_skyrim_npc_character):
        world_id = "thoughts-world-prev"
        _write_thought_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=100\nFirst thought.\n\nts=200\nSecond thought.\n\n",
        )
        manager = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"])
        previous = manager.get_previous_thoughts_text(example_skyrim_npc_character, world_id)
        assert previous == "First thought.\n\nSecond thought."
        assert PRIVATE_THOUGHT_HEADER not in previous


class TestPrivateThoughtsAreSeparate:
    def test_latest_thought_is_not_in_memory_text(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        world_id = "mix-world"
        _write_summary_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=100\n- They agreed to help.\n\n",
        )
        _write_thought_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=50\nOld doubt.\n\nts=100\nI still do not trust them.\n\n",
        )
        inner = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"])
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            inner_monologue=inner,
        )

        memory = rememberer.get_character_summary(example_skyrim_npc_character, world_id)
        assert "They agreed to help." in memory
        assert PRIVATE_THOUGHT_HEADER not in memory
        assert "I still do not trust them." not in memory

        thought = rememberer.get_character_private_thought(example_skyrim_npc_character, world_id)
        assert thought == "I still do not trust them."
        assert PRIVATE_THOUGHT_HEADER not in thought

        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        prompt_thoughts = rememberer.get_private_thoughts_text(characters, world_id)
        assert prompt_thoughts == thought

    def test_multi_npc_appends_wrapped_thought_to_memory(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        other = Character(
            base_id="1",
            ref_id="abc",
            name="Lydia",
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
            equipment=None,
            custom_character_values={},
        )
        world_id = "multi-thought-world"
        _write_summary_file(skyrim, example_skyrim_npc_character, world_id, "ts=100\n- The guard watched the gate.\n\n")
        _write_thought_file(skyrim, example_skyrim_npc_character, world_id, "ts=100\nI do not like that housecarl.\n\n")
        _write_summary_file(skyrim, other, world_id, "ts=100\n- Lydia stood by the player.\n\n")
        _write_thought_file(skyrim, other, world_id, "ts=100\nI will not mention the blade.\n\n")
        inner = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"])
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            inner_monologue=inner,
        )
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        characters.add_or_update_character(other)

        assert rememberer.get_private_thoughts_text(characters, world_id) == ""

        text = rememberer.get_prompt_text(characters, world_id)
        assert PRIVATE_THOUGHT_HEADER in text
        assert "I do not like that housecarl." in text
        assert "I will not mention the blade." in text
        guard_memory_start = text.index("[This is the beginning of Guard's memory]")
        guard_memory_end = text.index("[This is the end of Guard's memory]")
        lydia_memory_start = text.index("[This is the beginning of Lydia's memory]")
        lydia_memory_end = text.index("[This is the end of Lydia's memory]")
        guard_block = text[guard_memory_start:guard_memory_end]
        lydia_block = text[lydia_memory_start:lydia_memory_end]
        assert "The guard watched the gate." in guard_block
        assert "I do not like that housecarl." in guard_block
        assert guard_block.index("The guard watched the gate.") < guard_block.index("I do not like that housecarl.")
        assert "I will not mention the blade." not in guard_block
        assert "I will not mention the blade." in lydia_block
        assert "I do not like that housecarl." not in lydia_block

    def test_load_count_sends_several_thoughts_single_npc(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        default_config.inner_monologue_load_count = 2
        world_id = "load-count-single-world"
        _write_thought_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=100\nFirst.\n\nts=300\nThird.\n\nts=200\nSecond.\n\n",
        )
        inner = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"])
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            inner_monologue=inner,
        )
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        assert rememberer.get_private_thoughts_text(characters, world_id) == "Second.\n\nThird."

    def test_load_count_sends_several_thoughts_in_one_wrapper_multi_npc(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        default_config.inner_monologue_load_count = 2
        other = Character(
            base_id="1",
            ref_id="abc",
            name="Lydia",
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
            equipment=None,
            custom_character_values={},
        )
        world_id = "load-count-multi-world"
        _write_thought_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=100\nGuard first.\n\nts=200\nGuard second.\n\nts=300\nGuard third.\n\n",
        )
        _write_thought_file(skyrim, other, world_id, "ts=100\nLydia only.\n\n")
        inner = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"])
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            inner_monologue=inner,
        )
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        characters.add_or_update_character(other)

        text = rememberer.get_prompt_text(characters, world_id)
        guard_block = text[text.index("[This is the beginning of Guard's memory]"):text.index("[This is the end of Guard's memory]")]
        lydia_block = text[text.index("[This is the beginning of Lydia's memory]"):text.index("[This is the end of Lydia's memory]")]
        assert "Guard first." not in guard_block
        assert f"{PRIVATE_THOUGHT_HEADER}\nGuard second.\n\nGuard third.\n{PRIVATE_THOUGHT_FOOTER}" in guard_block
        assert guard_block.count(PRIVATE_THOUGHT_HEADER) == 1
        assert f"{PRIVATE_THOUGHT_HEADER}\nLydia only.\n{PRIVATE_THOUGHT_FOOTER}" in lydia_block

    def test_load_mode_none_hides_existing_thoughts(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        default_config.inner_monologue_load_count = 0
        world_id = "load-none-world"
        _write_summary_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=100\n- They agreed to help.\n\n",
        )
        _write_thought_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=100\nI still do not trust them.\n\n",
        )
        inner = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"])
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            inner_monologue=inner,
        )

        assert rememberer.get_character_private_thought(example_skyrim_npc_character, world_id) == ""
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        assert rememberer.get_private_thoughts_text(characters, world_id) == ""
        # Files still exist and can be read for generation context.
        previous = inner.get_previous_thoughts_text(example_skyrim_npc_character, world_id)
        assert previous == "I still do not trust them."

    def test_load_mode_none_hides_thoughts_in_multi_npc_memory(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        default_config.inner_monologue_load_count = 0
        other = Character(
            base_id="1",
            ref_id="abc",
            name="Lydia",
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
            equipment=None,
            custom_character_values={},
        )
        world_id = "load-none-multi-world"
        _write_summary_file(skyrim, example_skyrim_npc_character, world_id, "ts=100\n- The guard watched the gate.\n\n")
        _write_thought_file(skyrim, example_skyrim_npc_character, world_id, "ts=100\nI do not like that housecarl.\n\n")
        _write_summary_file(skyrim, other, world_id, "ts=100\n- Lydia stood by the player.\n\n")
        _write_thought_file(skyrim, other, world_id, "ts=100\nI will not mention the blade.\n\n")
        inner = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"])
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            inner_monologue=inner,
        )
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        characters.add_or_update_character(other)

        text = rememberer.get_prompt_text(characters, world_id)
        assert "The guard watched the gate." in text
        assert "Lydia stood by the player." in text
        assert PRIVATE_THOUGHT_HEADER not in text
        assert "I do not like that housecarl." not in text
        assert "I will not mention the blade." not in text

    def test_memory_generation_prompt_does_not_include_thoughts(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        world_id = "mix-world-memory-gen"
        _write_summary_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=100\n- They agreed to help.\n\n",
        )
        _write_thought_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=100\nI still do not trust them.\n\n",
        )
        inner = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"])
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            inner_monologue=inner,
        )
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        text = rememberer.get_prompt_text(characters, world_id)
        assert "They agreed to help." in text
        assert PRIVATE_THOUGHT_HEADER not in text

    def test_summary_request_omits_private_thoughts(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.inner_monologue_enabled = False
        default_config.personal_reflection_enabled = False
        monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
        other = Character(
            base_id="1",
            ref_id="abc",
            name="Lydia",
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
            equipment=None,
            custom_character_values={},
        )
        world_id = "summary-request-no-thoughts"
        _write_summary_file(skyrim, example_skyrim_npc_character, world_id, "ts=100\n- The guard watched the gate.\n\n")
        _write_thought_file(skyrim, example_skyrim_npc_character, world_id, "ts=100\nI do not like that housecarl.\n\n")
        _write_summary_file(skyrim, other, world_id, "ts=100\n- Lydia stood by the player.\n\n")
        _write_thought_file(skyrim, other, world_id, "ts=100\nI will not mention the blade.\n\n")
        inner = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"])
        mock_client = MagicMock()
        mock_client.request_call.return_value = "They spoke in the city."
        mock_client.token_limit = 4096
        mock_client.get_count_tokens.return_value = 1
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            summary_client=mock_client,
            inner_monologue=inner,
        )
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        characters.add_or_update_character(other)

        live_memory = rememberer.get_prompt_text(characters, world_id)
        assert "I do not like that housecarl." in live_memory
        assert "I will not mention the blade." in live_memory

        thread = message_thread(default_config, None)
        thread.add_message(join_message(example_skyrim_npc_character, default_config))
        thread.add_message(join_message(other, default_config))
        for i in range(5):
            thread.add_message(UserMessage(default_config, f"Player line number {i} with extra words."))
        rememberer.save_conversation_state(thread, characters, world_id, save_timestamp=99)

        assert mock_client.request_call.call_count >= 1
        saw_past_summaries = False
        for call in mock_client.request_call.call_args_list:
            sent = "\n".join(
                str(message.get("content", ""))
                for message in call[0][0].get_openai_messages()
            )
            assert PRIVATE_THOUGHT_HEADER not in sent
            assert "I do not like that housecarl." not in sent
            assert "I will not mention the blade." not in sent
            if "The guard watched the gate." in sent and "Lydia stood by the player." in sent:
                saw_past_summaries = True
        assert saw_past_summaries


class TestSaveGatesAndSharedTimestamp:
    def test_thoughts_not_saved_when_toggle_off(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        default_config.inner_monologue_enabled = False
        mock_client = MagicMock()
        mock_client.request_call.return_value = "The player asked the guard for directions."
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
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_conversation_state(thread, characters, "save-off-world", save_timestamp=12345)

        thought_folder = os.path.join(
            skyrim.thoughts_folder_path,
            "save-off-world",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
        )
        assert not os.path.exists(thought_folder)

    def test_save_thoughts_override_ignores_enable_toggle(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.inner_monologue_enabled = False
        monkeypatch.setattr("src.remember.inner_monologue.time.sleep", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
        mock_client = MagicMock()
        mock_client.request_call.return_value = "The player asked the guard for directions."
        mock_client.token_limit = 4096
        mock_client.get_count_tokens.return_value = 1
        mock_client.is_too_long.return_value = False
        inner = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            summary_client=mock_client,
            inner_monologue=inner,
        )
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_conversation_state(
            thread,
            characters,
            "save-override-on-world",
            save_timestamp=12345,
            save_thoughts=True,
        )
        thought_path = os.path.join(
            skyrim.thoughts_folder_path,
            "save-override-on-world",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
            f"{example_skyrim_npc_character.name}_thoughts_1.txt",
        )
        assert os.path.exists(thought_path)

    def test_save_thoughts_override_off_skips_even_when_enabled(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.inner_monologue_enabled = True
        monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
        mock_client = MagicMock()
        mock_client.request_call.return_value = "The player asked the guard for directions."
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
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_conversation_state(
            thread,
            characters,
            "save-override-off-world",
            save_timestamp=12345,
            save_thoughts=False,
        )
        thought_folder = os.path.join(
            skyrim.thoughts_folder_path,
            "save-override-off-world",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
        )
        assert not os.path.exists(thought_folder)

    def test_empty_summary_skips_thoughts(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.inner_monologue_enabled = True
        monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("src.remember.inner_monologue.time.sleep", lambda *_args, **_kwargs: None)
        mock_client = MagicMock()
        mock_client.request_call.return_value = ""
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
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_conversation_state(thread, characters, "empty-summary-world", save_timestamp=12345)

        thought_folder = os.path.join(
            skyrim.thoughts_folder_path,
            "empty-summary-world",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
        )
        assert not os.path.exists(thought_folder)

    def test_shared_timestamp_on_summary_and_thought(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.inner_monologue_enabled = True
        monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("src.remember.inner_monologue.time.sleep", lambda *_args, **_kwargs: None)
        mock_client = MagicMock()
        mock_client.request_call.side_effect = [
            "The player asked the guard for directions through the city.",
            "I do not trust this stranger yet.",
        ]
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
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        save_ts = 1739280000
        rememberer.save_conversation_state(thread, characters, "shared-ts-world", save_timestamp=save_ts)

        summary_path = os.path.join(
            skyrim.conversation_folder_path,
            "shared-ts-world",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
            f"{example_skyrim_npc_character.name}_summary_1.txt",
        )
        thought_path = os.path.join(
            skyrim.thoughts_folder_path,
            "shared-ts-world",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
            f"{example_skyrim_npc_character.name}_thoughts_1.txt",
        )
        with open(summary_path, encoding="utf-8") as f:
            summary_text = f.read()
        with open(thought_path, encoding="utf-8") as f:
            thought_text = f.read()
        assert f"ts={save_ts}" in summary_text
        assert f"ts={save_ts}" in thought_text
        assert "- I do not trust this stranger yet." in thought_text
        assert thought_path.startswith(skyrim.thoughts_folder_path)
        assert "conversations" not in thought_path.replace("\\", "/")

    def test_thought_prompt_includes_full_summary_history(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.inner_monologue_enabled = True
        monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("src.remember.inner_monologue.time.sleep", lambda *_args, **_kwargs: None)
        _write_summary_file(
            skyrim,
            example_skyrim_npc_character,
            "full-history-thoughts",
            "ts=50\n- They asked about Whiterun.\n\n",
        )
        mock_client = MagicMock()
        mock_client.request_call.side_effect = [
            "The player asked the guard for directions through the city.",
            "I do not trust this stranger yet.",
        ]
        mock_client.token_limit = 4096
        mock_client.get_count_tokens.return_value = 1
        mock_client.is_too_long.return_value = False
        inner = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            summary_client=mock_client,
            inner_monologue=inner,
        )
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_conversation_state(thread, characters, "full-history-thoughts", save_timestamp=99)

        thought_call = mock_client.request_call.call_args_list[-1]
        thought_prompt = thought_call[0][0].get_openai_messages()[0]["content"]
        assert "They asked about Whiterun." in thought_prompt
        assert "The player asked the guard for directions through the city." in thought_prompt

    def test_thought_request_only_has_this_npcs_turn_thoughts(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        guard = example_skyrim_npc_character
        lydia = Character(
            base_id="1",
            ref_id="abc",
            name="Lydia",
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
            equipment=None,
            custom_character_values={},
        )
        thread = message_thread(default_config, None)
        thread.add_message(UserMessage(default_config, "Where is the Jarl?"))
        reply = AssistantMessage(default_config)
        reply.is_multi_npc_message = True
        reply.add_sentence(Sentence(SentenceContent(guard, "In Dragonsreach.", SentenceTypeEnum.SPEECH), "", 0))
        reply.add_sentence(Sentence(SentenceContent(lydia, "I will show you.", SentenceTypeEnum.SPEECH), "", 0))
        reply.add_sentence(Sentence(SentenceContent(guard, "Guard secret.", SentenceTypeEnum.THOUGHT), "", 0))
        reply.add_sentence(Sentence(SentenceContent(lydia, "Lydia secret.", SentenceTypeEnum.THOUGHT), "", 0))
        thread.add_message(reply)

        mock_client = MagicMock()
        mock_client.request_call.return_value = "A thought."
        inner = InnerMonologue(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
        params = CharacterSummaryParameters(thread, [guard, lydia])
        inner.save_thoughts(
            {guard.name: params, lydia.name: params},
            {guard.name: guard, lydia.name: lydia},
            {},
            "turn-thought-world",
            save_timestamp=1,
            require_summary=False,
        )

        guard_text = mock_client.request_call.call_args_list[0][0][0].get_openai_messages()[-1]["content"]
        lydia_text = mock_client.request_call.call_args_list[1][0][0].get_openai_messages()[-1]["content"]
        assert "Guard secret." in guard_text and "Lydia secret." not in guard_text
        assert "Lydia secret." in lydia_text and "Guard secret." not in lydia_text
        # The live conversation thread is untouched
        assert reply.thoughts_only_of_ref_id is None

    def test_save_thoughts_only_does_not_write_summary(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.inner_monologue_enabled = False
        monkeypatch.setattr("src.remember.inner_monologue.time.sleep", lambda *_args, **_kwargs: None)
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
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        save_ts = 1739281111
        rememberer.save_thoughts_only(thread, characters, "thoughts-only-world", save_timestamp=save_ts)

        summary_folder = os.path.join(
            skyrim.conversation_folder_path,
            "thoughts-only-world",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
        )
        thought_path = os.path.join(
            skyrim.thoughts_folder_path,
            "thoughts-only-world",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
            f"{example_skyrim_npc_character.name}_thoughts_1.txt",
        )
        assert not os.path.exists(summary_folder)
        with open(thought_path, encoding="utf-8") as f:
            thought_text = f.read()
        assert f"ts={save_ts}" in thought_text
        assert "- I will keep an eye on this stranger." in thought_text
        mock_client.request_call.assert_called_once()

    def test_load_mode_none_still_saves_thoughts(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.inner_monologue_enabled = True
        default_config.inner_monologue_load_count = 0
        monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("src.remember.inner_monologue.time.sleep", lambda *_args, **_kwargs: None)
        mock_client = MagicMock()
        mock_client.request_call.side_effect = [
            "The player asked the guard for directions through the city.",
            "I do not trust this stranger yet.",
        ]
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
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_conversation_state(thread, characters, "save-with-none-load", save_timestamp=1739282222)

        thought_path = os.path.join(
            skyrim.thoughts_folder_path,
            "save-with-none-load",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
            f"{example_skyrim_npc_character.name}_thoughts_1.txt",
        )
        with open(thought_path, encoding="utf-8") as f:
            thought_text = f.read()
        assert "- I do not trust this stranger yet." in thought_text
        assert rememberer.get_character_private_thought(example_skyrim_npc_character, "save-with-none-load") == ""


def test_conversation_skips_save_when_summary_disabled(default_conversation, example_skyrim_npc_character, monkeypatch):
    default_conversation.context.config.conversation_summary_enabled = False
    default_conversation.context.config.inner_monologue_enabled = True
    called = {"save": False}

    def fake_save(*args, **kwargs):
        called["save"] = True
        return {}

    monkeypatch.setattr(default_conversation._Conversation__rememberer, "save_conversation_state", fake_save)
    default_conversation._Conversation__save_summary_for_characters(
        default_conversation._Conversation__messages,
        [example_skyrim_npc_character],
        force=False,
    )
    assert called["save"] is False


def test_conversation_save_inner_thoughts_only_calls_rememberer(default_conversation, monkeypatch):
    called = {"thoughts": False}

    def fake_save_thoughts(*args, **kwargs):
        called["thoughts"] = True

    monkeypatch.setattr(default_conversation._Conversation__rememberer, "save_thoughts_only", fake_save_thoughts)
    default_conversation.save_inner_thoughts_only()
    assert called["thoughts"] is True


def test_save_summary_only_passes_button_toggles_not_enables(default_conversation, monkeypatch):
    config = default_conversation.context.config
    config.inner_monologue_enabled = False
    config.personal_reflection_enabled = True
    config.save_summary_now_also_save_inner_thoughts = True
    config.save_summary_now_also_save_personal_reflection = False
    captured = {}

    def fake_save(*args, **kwargs):
        captured["kwargs"] = kwargs
        return {}

    monkeypatch.setattr(default_conversation._Conversation__rememberer, "save_conversation_state", fake_save)
    default_conversation.save_summary_only()
    assert captured["kwargs"]["save_thoughts"] is True
    assert captured["kwargs"]["save_reflections"] is False


def test_conversation_end_save_does_not_use_button_toggles(default_conversation, example_skyrim_npc_character, monkeypatch):
    config = default_conversation.context.config
    config.save_summary_now_also_save_inner_thoughts = False
    config.save_summary_now_also_save_personal_reflection = False
    captured = {}

    def fake_save(*args, **kwargs):
        captured["kwargs"] = kwargs
        return {}

    monkeypatch.setattr(default_conversation._Conversation__rememberer, "save_conversation_state", fake_save)
    default_conversation._Conversation__save_summary_for_characters(
        default_conversation._Conversation__messages,
        [example_skyrim_npc_character],
        force=True,
    )
    assert captured["kwargs"].get("save_thoughts") is None
    assert captured["kwargs"].get("save_reflections") is None


def test_inner_monologue_config_defaults(default_config):
    assert default_config.inner_monologue_enabled is False
    assert default_config.inner_monologue_load_count == 1
    assert default_config.turn_thoughts_load_mode == "Latest"
    assert default_config.save_summary_now_also_save_inner_thoughts is True
    assert "{name}" in default_config.inner_monologue_prompt
    assert "{previous_thoughts}" in default_config.inner_monologue_prompt
    assert "{conversation_summary}" in default_config.inner_monologue_prompt
    assert "{conversation_summaries}" not in default_config.inner_monologue_prompt
    assert "factual summary of the past events" in default_config.inner_monologue_prompt
    assert default_config.definitions.get_config_value_definition("save_inner_thoughts_now") is not None
    assert default_config.definitions.get_config_value_definition("inner_monologue_load_count") is not None
    assert default_config.definitions.get_config_value_definition("save_summary_now_also_save_inner_thoughts") is not None
