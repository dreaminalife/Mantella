"""Tests for personal-reflection save/load and injection into NPC bios."""
import os
from unittest.mock import MagicMock

from src.character_manager import Character
from src.characters_manager import Characters
from src.conversation.context import context as Context
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage, join_message
from src.remember.inner_monologue import InnerMonologue
from src.remember.personal_reflection import (
    PERSONAL_REFLECTION_HEADER,
    PersonalReflection,
    parse_reflection_blocks,
    resolve_language_display_name,
    select_latest_reflection,
    should_load_reflections_into_prompt,
    strip_reflection_heading,
    wrap_personal_reflection,
)
from src.remember.summaries import Summaries


def _write_reflection_file(game, character, world_id: str, contents: str) -> str:
    base_name = character.name
    folder = os.path.join(game.reflections_folder_path, world_id, f"{base_name} - {character.ref_id}")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{base_name}_reflections_1.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(contents)
    return path


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


def _short_thread(config, npc) -> message_thread:
    thread = message_thread(config, None)
    thread.add_message(join_message(npc, config))
    thread.add_message(UserMessage(config, "Hello."))
    return thread


def _other_npc() -> Character:
    return Character(
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


def _mock_llm():
    mock_client = MagicMock()
    mock_client.request_call.return_value = "I have grown wary of strangers, yet I still keep the watch."
    mock_client.token_limit = 4096
    mock_client.get_count_tokens.return_value = 1
    mock_client.is_too_long.return_value = False
    return mock_client


class TestParseWrapAndSelect:
    def test_parse_keeps_blank_lines_inside_one_block(self):
        raw = (
            "ts=100\n"
            "First paragraph of an older reflection.\n"
            "\n"
            "Second paragraph of that same reflection.\n"
            "\n"
            "ts=200\n"
            "Newest reflection.\n"
            "\n"
            "Also multiple paragraphs.\n"
        )
        legacy, timestamped = parse_reflection_blocks(raw)
        assert legacy == []
        assert timestamped[0][0] == 100
        assert "First paragraph" in timestamped[0][1]
        assert "Second paragraph" in timestamped[0][1]
        assert timestamped[1][0] == 200
        assert "Newest reflection." in timestamped[1][1]
        assert "Also multiple paragraphs." in timestamped[1][1]

    def test_parse_legacy_before_first_timestamp(self):
        raw = "Old untimed reflection.\n\nts=50\nTimed one."
        legacy, timestamped = parse_reflection_blocks(raw)
        assert legacy == ["Old untimed reflection."]
        assert timestamped == [(50, "Timed one.")]

    def test_select_latest_timestamped(self):
        latest = select_latest_reflection(
            ["old legacy"],
            [(100, "older"), (300, "newest"), (200, "mid")],
        )
        assert latest == (300, "newest")

    def test_select_latest_legacy_fallback(self):
        latest = select_latest_reflection(["first", "last"], [])
        assert latest == (0, "last")

    def test_select_latest_empty(self):
        assert select_latest_reflection([], []) is None

    def test_wrap_personal_reflection(self):
        wrapped = wrap_personal_reflection("I do not trust them.")
        assert wrapped.startswith(PERSONAL_REFLECTION_HEADER)
        assert "Don't speak out loud, these are private thoughts" in wrapped
        assert "I do not trust them." in wrapped

    def test_wrap_skips_empty(self):
        assert wrap_personal_reflection("  ") == ""

    def test_wrap_does_not_double_wrap(self):
        once = wrap_personal_reflection("secret")
        assert wrap_personal_reflection(once) == once

    def test_strip_heading(self):
        assert strip_reflection_heading("## Personal Reflection\nI have changed.") == "I have changed."
        assert strip_reflection_heading(
            "## Personal Reflection (Don't speak out loud, these are private thoughts)\nI have changed."
        ) == "I have changed."
        assert strip_reflection_heading("I have changed.") == "I have changed."

    def test_should_load_defaults_to_latest(self):
        config = MagicMock(spec=[])
        assert should_load_reflections_into_prompt(config) is True

    def test_should_load_none_mode(self):
        config = MagicMock(spec=[])
        config.personal_reflection_load_mode = "None"
        assert should_load_reflections_into_prompt(config) is False
        config.personal_reflection_load_mode = "none"
        assert should_load_reflections_into_prompt(config) is False


class TestLoadLatest:
    def test_latest_reflection_is_not_in_memory_text(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        world_id = "mix-reflection-world"
        _write_summary_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=100\n- They agreed to help.\n\n",
        )
        _write_reflection_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=50\nOld doubt.\n\nStill unsure.\n\nts=100\nI have become harder to fool.\n\nAnd I keep it private.\n\n",
        )
        manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"])
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            personal_reflection=manager,
        )

        memory = rememberer.get_character_summary(example_skyrim_npc_character, world_id)
        assert "They agreed to help." in memory
        assert PERSONAL_REFLECTION_HEADER not in memory
        assert "I have become harder to fool." not in memory

        text = rememberer.get_character_personal_reflection(example_skyrim_npc_character, world_id)
        assert "I have become harder to fool." in text
        assert "And I keep it private." in text
        assert "Old doubt." not in text
        assert PERSONAL_REFLECTION_HEADER not in text

    def test_load_mode_none_hides_reflection(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        default_config.personal_reflection_load_mode = "None"
        world_id = "hide-reflection-world"
        _write_reflection_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=100\nI keep this to myself.\n\n",
        )
        manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"])
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            personal_reflection=manager,
        )
        assert rememberer.get_character_personal_reflection(example_skyrim_npc_character, world_id) == ""


class TestSaveGates:
    def test_reflections_not_saved_when_toggle_off(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        default_config.personal_reflection_enabled = False
        mock_client = _mock_llm()
        manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            summary_client=mock_client,
            personal_reflection=manager,
        )
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_conversation_state(thread, characters, "save-off-reflection", save_timestamp=12345)

        reflection_folder = os.path.join(
            skyrim.reflections_folder_path,
            "save-off-reflection",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
        )
        assert not os.path.exists(reflection_folder)

    def test_save_reflections_override_ignores_enable_toggle(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.personal_reflection_enabled = False
        monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
        mock_client = _mock_llm()
        manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            summary_client=mock_client,
            personal_reflection=manager,
        )
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_conversation_state(
            thread,
            characters,
            "save-override-on-reflection",
            save_timestamp=12345,
            save_reflections=True,
        )
        reflection_path = os.path.join(
            skyrim.reflections_folder_path,
            "save-override-on-reflection",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
            f"{example_skyrim_npc_character.name}_reflections_1.txt",
        )
        assert os.path.exists(reflection_path)

    def test_save_reflections_override_off_skips_even_when_enabled(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.personal_reflection_enabled = True
        monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
        mock_client = _mock_llm()
        manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            summary_client=mock_client,
            personal_reflection=manager,
        )
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_conversation_state(
            thread,
            characters,
            "save-override-off-reflection",
            save_timestamp=12345,
            save_reflections=False,
        )
        reflection_folder = os.path.join(
            skyrim.reflections_folder_path,
            "save-override-off-reflection",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
        )
        assert not os.path.exists(reflection_folder)

    def test_empty_summaries_skip_reflection(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.personal_reflection_enabled = True
        monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
        mock_client = _mock_llm()
        manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            summary_client=mock_client,
            personal_reflection=manager,
        )
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_reflections_only(thread, characters, "no-summary-world", save_timestamp=12345)

        reflection_folder = os.path.join(
            skyrim.reflections_folder_path,
            "no-summary-world",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
        )
        assert not os.path.exists(reflection_folder)
        mock_client.request_call.assert_not_called()

    def test_short_conversation_skips_reflection_even_with_existing_summaries(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.personal_reflection_enabled = True
        monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
        _write_summary_file(
            skyrim,
            example_skyrim_npc_character,
            "short-chat-reflection",
            "ts=50\n- They asked about Whiterun.\n\n",
        )
        mock_client = _mock_llm()
        manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            summary_client=mock_client,
            personal_reflection=manager,
        )
        thread = _short_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_conversation_state(thread, characters, "short-chat-reflection", save_timestamp=12345)

        reflection_folder = os.path.join(
            skyrim.reflections_folder_path,
            "short-chat-reflection",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
        )
        assert not os.path.exists(reflection_folder)
        mock_client.request_call.assert_not_called()

    def test_save_summary_now_still_reflects_on_short_conversation(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.personal_reflection_enabled = False
        monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
        _write_summary_file(
            skyrim,
            example_skyrim_npc_character,
            "short-chat-manual-reflection",
            "ts=50\n- They asked about Whiterun.\n\n",
        )
        mock_client = _mock_llm()
        manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            summary_client=mock_client,
            personal_reflection=manager,
        )
        thread = _short_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_conversation_state(
            thread,
            characters,
            "short-chat-manual-reflection",
            save_timestamp=12345,
            save_reflections=True,
        )

        reflection_path = os.path.join(
            skyrim.reflections_folder_path,
            "short-chat-manual-reflection",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
            f"{example_skyrim_npc_character.name}_reflections_1.txt",
        )
        assert os.path.exists(reflection_path)
        mock_client.request_call.assert_called()

    def test_conversation_end_appends_without_overwriting(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        default_config.personal_reflection_enabled = True
        monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
        _write_reflection_file(
            skyrim,
            example_skyrim_npc_character,
            "append-world",
            "ts=100\nI used to trust everyone.\n\nEven travelers.\n\n",
        )
        mock_client = _mock_llm()
        mock_client.request_call.return_value = "## Personal Reflection\nI am more cautious now.\n\nI keep my own counsel."
        manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            summary_client=mock_client,
            personal_reflection=manager,
        )
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_conversation_state(thread, characters, "append-world", save_timestamp=200)

        path = os.path.join(
            skyrim.reflections_folder_path,
            "append-world",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
            f"{example_skyrim_npc_character.name}_reflections_1.txt",
        )
        on_disk = open(path, encoding="utf-8").read()
        assert "I used to trust everyone." in on_disk
        assert "Even travelers." in on_disk
        assert "I am more cautious now." in on_disk
        assert "I keep my own counsel." in on_disk
        assert on_disk.index("I used to trust everyone.") < on_disk.index("I am more cautious now.")
        assert PERSONAL_REFLECTION_HEADER not in on_disk
        latest = rememberer.get_character_personal_reflection(example_skyrim_npc_character, "append-world")
        assert "I am more cautious now." in latest
        assert "I used to trust everyone." not in latest

    def test_save_reflections_only_does_not_write_summary(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
        _write_summary_file(
            skyrim,
            example_skyrim_npc_character,
            "reflections-only-world",
            "ts=50\n- They asked about Whiterun.\n\n",
        )
        mock_client = _mock_llm()
        manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            summary_client=mock_client,
            personal_reflection=manager,
        )
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_reflections_only(thread, characters, "reflections-only-world", save_timestamp=99)

        summary_path = os.path.join(
            skyrim.conversation_folder_path,
            "reflections-only-world",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
            f"{example_skyrim_npc_character.name}_summary_1.txt",
        )
        summary_text = open(summary_path, encoding="utf-8").read()
        assert "They asked about Whiterun." in summary_text
        assert "ts=99" not in summary_text

        reflection_path = os.path.join(
            skyrim.reflections_folder_path,
            "reflections-only-world",
            f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
            f"{example_skyrim_npc_character.name}_reflections_1.txt",
        )
        reflection_text = open(reflection_path, encoding="utf-8").read()
        assert "ts=99" in reflection_text
        assert "I have grown wary of strangers" in reflection_text

    def test_save_for_named_npc_appends_to_existing_name_ref_folder(
        self,
        skyrim,
        default_config,
        llm_client,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
        existing_path = _write_reflection_file(
            skyrim,
            example_skyrim_npc_character,
            "bio-editor-folder",
            "ts=10\nI used to trust everyone.\n\n",
        )
        mock_client = _mock_llm()
        manager = PersonalReflection(skyrim, default_config, llm_client, "en", mock_client)
        result = manager.save_for_named_npc(
            example_skyrim_npc_character.name,
            example_skyrim_npc_character.bio,
            "ts=50\n- They asked about Whiterun.\n",
            "bio-editor-folder",
            save_timestamp=99,
        )
        assert result
        name_only_folder = os.path.join(
            skyrim.reflections_folder_path,
            "bio-editor-folder",
            example_skyrim_npc_character.name,
        )
        assert not os.path.exists(name_only_folder)
        on_disk = open(existing_path, encoding="utf-8").read()
        assert "I used to trust everyone." in on_disk
        assert "I have grown wary of strangers" in on_disk
        assert "ts=99" in on_disk

    def test_save_for_named_npc_appends_tag_templates_to_bio(
        self,
        skyrim,
        default_config,
        llm_client,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
        skyrim._Skyrim__bio_template_manager.templates["housecarl"] = "Sworn housecarl of Whiterun."
        mock_client = _mock_llm()
        manager = PersonalReflection(skyrim, default_config, llm_client, "en", mock_client)
        result = manager.save_for_named_npc(
            example_skyrim_npc_character.name,
            "A guard of Whiterun.",
            "ts=50\n- They asked about Whiterun.\n",
            "bio-editor-tags",
            save_timestamp=99,
            tags="housecarl, housecarl",
        )
        assert result
        prompt = mock_client.request_call.call_args[0][0].get_openai_messages()[0]["content"]
        assert "A guard of Whiterun." in prompt
        assert "## Additional info" in prompt
        assert prompt.count("Sworn housecarl of Whiterun.") == 1

    def test_save_for_named_npc_loads_templates_when_game_has_not_started(
        self,
        default_config,
        llm_client,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)

        class _FakeTemplates:
            def __init__(self, path, config, game_name="Skyrim"):
                self.game_name = game_name

            def expand_bio_with_tags(self, bio, tags):
                return bio + "\n\n## Additional info\nLoaded from disk."

            def extract_dynamic_events(self, tags):
                return [(60, "Offline event.")]

        monkeypatch.setattr("src.bio_template_manager.BioTemplateManager", _FakeTemplates)
        mock_client = _mock_llm()
        manager = PersonalReflection(
            None,
            default_config,
            llm_client,
            "en",
            mock_client,
            reflections_folder_path=str(tmp_path),
        )
        result = manager.save_for_named_npc(
            "Lydia",
            "A guard of Whiterun.",
            "ts=50\n- They asked about Whiterun.\n",
            "bio-editor-tags-offline",
            save_timestamp=99,
            tags="housecarl",
        )
        assert result
        prompt = mock_client.request_call.call_args[0][0].get_openai_messages()[0]["content"]
        assert "## Additional info" in prompt
        assert "Loaded from disk." in prompt
        assert "- Offline event." in prompt

    def test_save_for_named_npc_merges_dynamic_tag_events_into_summary(
        self,
        skyrim,
        default_config,
        llm_client,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
        default_config.personal_reflection_prompt = "Summary: {conversation_summary} END"
        skyrim._Skyrim__bio_template_manager.templates["quest"] = (
            "Sworn housecarl of Whiterun.\n- 40: Was named housecarl.\n- 70: Fought a dragon."
        )
        mock_client = _mock_llm()
        manager = PersonalReflection(skyrim, default_config, llm_client, "en", mock_client)
        result = manager.save_for_named_npc(
            example_skyrim_npc_character.name,
            "A guard of Whiterun.",
            "ts=30\n- They met at the gate.\n\nts=50\n- They asked about Whiterun.\n",
            "bio-editor-dynamic",
            save_timestamp=99,
            tags="quest",
        )
        assert result
        prompt = mock_client.request_call.call_args[0][0].get_openai_messages()[0]["content"]
        summary = prompt.split("Summary: ", 1)[1].split(" END", 1)[0]
        assert summary == (
            "- They met at the gate.\n"
            "Was named housecarl.\n"
            "- They asked about Whiterun.\n\n"
            "New developments since last meeting:\n"
            "- Fought a dragon."
        )

    def test_language_code_becomes_display_name_in_prompt(
        self,
        skyrim,
        default_config,
        llm_client,
        example_skyrim_npc_character,
    ):
        default_config.personal_reflection_prompt = (
            "Write in first person in {language}. Summary: {conversation_summary}."
        )
        manager = PersonalReflection(skyrim, default_config, llm_client, "en")
        prompt = manager._PersonalReflection__format_prompt(
            name=example_skyrim_npc_character.name,
            names=example_skyrim_npc_character.name,
            bios="A guard of Whiterun.",
            player_name="Dragonborn",
            conversation_summary="They asked about Whiterun.",
            previous_reflection="(none)",
        )
        assert "in English" in prompt
        assert "in en" not in prompt
        assert "They asked about Whiterun." in prompt

    def test_save_for_named_npc_uses_editor_thoughts(
        self,
        skyrim,
        default_config,
        llm_client,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
        default_config.personal_reflection_prompt = "Thoughts: {private_thoughts} END"
        _write_thought_file(
            skyrim,
            example_skyrim_npc_character,
            "bio-editor-thoughts",
            "ts=10\n- Saved on disk only.\n\n",
        )
        mock_client = _mock_llm()
        manager = PersonalReflection(skyrim, default_config, llm_client, "en", mock_client)
        result = manager.save_for_named_npc(
            example_skyrim_npc_character.name,
            "A guard of Whiterun.",
            "ts=50\n- They asked about Whiterun.\n",
            "bio-editor-thoughts",
            save_timestamp=99,
            private_thoughts="ts=10\n- They seemed kind.\n\nts=200\n- I started to doubt them.\n",
        )
        assert result
        prompt = mock_client.request_call.call_args[0][0].get_openai_messages()[0]["content"]
        assert prompt.index("They seemed kind.") < prompt.index("I started to doubt them.")
        assert "Saved on disk only." not in prompt
        assert "ts=" not in prompt

    def test_save_for_named_npc_empty_thoughts_are_none(
        self,
        skyrim,
        default_config,
        llm_client,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
        default_config.personal_reflection_prompt = "Thoughts: {private_thoughts} END"
        mock_client = _mock_llm()
        manager = PersonalReflection(skyrim, default_config, llm_client, "en", mock_client)
        result = manager.save_for_named_npc(
            example_skyrim_npc_character.name,
            "A guard of Whiterun.",
            "ts=50\n- They asked about Whiterun.\n",
            "bio-editor-empty-thoughts",
            save_timestamp=99,
            private_thoughts="  ",
        )
        assert result
        prompt = mock_client.request_call.call_args[0][0].get_openai_messages()[0]["content"]
        assert "Thoughts: (none) END" in prompt

    def test_conversation_end_includes_full_thought_history_and_the_new_thought(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        monkeypatch,
    ):
        monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("src.remember.inner_monologue.time.sleep", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("src.remember.personal_reflection.time.sleep", lambda *_args, **_kwargs: None)
        default_config.inner_monologue_enabled = True
        default_config.personal_reflection_enabled = True
        default_config.personal_reflection_prompt = "Thoughts: {private_thoughts} END"
        _write_thought_file(
            skyrim,
            example_skyrim_npc_character,
            "reflection-thoughts",
            "ts=50\n- They seemed kind.\n\nts=150\n- I started to doubt them.\n\n",
        )
        mock_client = _mock_llm()
        mock_client.request_call.side_effect = [
            "They spoke at the gate.",
            "I just realized I was wrong.",
            "I am more cautious now.",
        ]
        inner = InnerMonologue(
            skyrim, default_config, llm_client, english_language_info["language"], mock_client
        )
        manager = PersonalReflection(
            skyrim, default_config, llm_client, english_language_info["language"], mock_client
        )
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            summary_client=mock_client,
            inner_monologue=inner,
            personal_reflection=manager,
        )
        thread = _long_enough_thread(default_config, example_skyrim_npc_character)
        characters = Characters()
        characters.add_or_update_character(example_skyrim_npc_character)
        rememberer.save_conversation_state(
            thread,
            characters,
            "reflection-thoughts",
            save_timestamp=200,
            conversation_start_ts=100,
        )

        prompts = [
            call[0][0].get_openai_messages()[0]["content"]
            for call in mock_client.request_call.call_args_list
        ]
        reflection_prompts = [prompt for prompt in prompts if prompt.startswith("Thoughts:")]
        assert len(reflection_prompts) == 1
        prompt = reflection_prompts[0]
        assert prompt.index("They seemed kind.") < prompt.index("I started to doubt them.")
        assert prompt.index("I started to doubt them.") < prompt.index("I just realized I was wrong.")
        assert "ts=" not in prompt


class TestBioInjection:
    def test_single_npc_bio_appends_heading_after_filter(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
        example_skyrim_player_character,
    ):
        world_id = "inject-single"
        example_skyrim_npc_character.bio = "## Race\nImperial\n\n## Duties\nKeep the gate."
        default_config.enable_bio_section_filter = True
        default_config.bio_sections_to_exclude_list = ["race"]
        _write_reflection_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=10\nI have stood this post for years.\n\nIt has changed me.\n\n",
        )
        manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"])
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            personal_reflection=manager,
        )
        ctx = Context(world_id, default_config, llm_client, rememberer, english_language_info)
        ctx.npcs_in_conversation.add_or_update_character(example_skyrim_player_character)
        ctx.npcs_in_conversation.add_or_update_character(example_skyrim_npc_character)
        bios = ctx._context__get_bios_text()
        assert "## Race" not in bios
        assert "## Duties" in bios
        assert PERSONAL_REFLECTION_HEADER in bios
        assert "I have stood this post for years." in bios
        assert bios.index("## Duties") < bios.index(PERSONAL_REFLECTION_HEADER)

    def test_multi_npc_keeps_reflection_inside_each_bio(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        other = _other_npc()
        world_id = "inject-multi"
        _write_reflection_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=10\nThe housecarl watches me too closely.\n\n",
        )
        manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"])
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            personal_reflection=manager,
        )
        ctx = Context(world_id, default_config, llm_client, rememberer, english_language_info)
        ctx.npcs_in_conversation.add_or_update_character(example_skyrim_npc_character)
        ctx.npcs_in_conversation.add_or_update_character(other)
        bios = ctx._context__get_bios_text()
        guard_start = f"[This is the beginning of {example_skyrim_npc_character.name}'s bio]"
        guard_end = f"[This is the end of {example_skyrim_npc_character.name}'s bio]"
        lydia_start = f"[This is the beginning of {other.name}'s bio]"
        assert guard_start in bios
        reflection_at = bios.index(PERSONAL_REFLECTION_HEADER)
        assert bios.index(guard_start) < reflection_at < bios.index(guard_end)
        assert "The housecarl watches me too closely." in bios
        assert bios.index(guard_end) < bios.index(lydia_start)
        lydia_block = bios[bios.index(lydia_start):]
        assert PERSONAL_REFLECTION_HEADER not in lydia_block
        assert bios.count("\n\n==========\n\n") == 1

    def test_bios_and_summaries_puts_reflection_in_bio_not_memory(
        self,
        skyrim,
        default_config,
        llm_client,
        english_language_info,
        example_skyrim_npc_character,
    ):
        other = _other_npc()
        world_id = "inject-combined"
        _write_summary_file(skyrim, example_skyrim_npc_character, world_id, "ts=5\n- They guarded the gate.\n\n")
        _write_reflection_file(
            skyrim,
            example_skyrim_npc_character,
            world_id,
            "ts=10\nI no longer tell travelers everything.\n\n",
        )
        manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"])
        rememberer = Summaries(
            skyrim,
            default_config,
            llm_client,
            english_language_info["language"],
            personal_reflection=manager,
        )
        ctx = Context(world_id, default_config, llm_client, rememberer, english_language_info)
        ctx.npcs_in_conversation.add_or_update_character(example_skyrim_npc_character)
        ctx.npcs_in_conversation.add_or_update_character(other)
        combined = ctx._context__get_bios_and_summaries_text()
        bio_start = f"[This is the beginning of {example_skyrim_npc_character.name}'s bio"
        bio_end = f"[This is the end of {example_skyrim_npc_character.name}'s bio"
        memory_start = f"[This is the beginning of {example_skyrim_npc_character.name}'s memory"
        assert combined.index(bio_start) < combined.index(PERSONAL_REFLECTION_HEADER) < combined.index(bio_end)
        assert combined.index(bio_end) < combined.index(memory_start)
        memory_block = combined[combined.index(memory_start):]
        assert PERSONAL_REFLECTION_HEADER not in memory_block
        assert "They guarded the gate." in combined
        assert combined.count("\n\n==========\n\n") == 1


def test_conversation_save_personal_reflection_only_calls_rememberer(default_conversation, monkeypatch):
    called = {"reflections": False}

    def fake_save_reflections(*args, **kwargs):
        called["reflections"] = True

    monkeypatch.setattr(default_conversation._Conversation__rememberer, "save_reflections_only", fake_save_reflections)
    default_conversation.save_personal_reflection_only()
    assert called["reflections"] is True


def test_resolve_language_display_name():
    assert resolve_language_display_name("en") == "English"
    assert resolve_language_display_name("English") == "English"
    assert resolve_language_display_name("de") == "German"
    assert resolve_language_display_name("") == "English"


def test_personal_reflection_config_defaults(default_config):
    assert default_config.personal_reflection_enabled is False
    assert default_config.personal_reflection_load_mode == "Latest"
    assert default_config.save_summary_now_also_save_personal_reflection is True
    assert "{name}" in default_config.personal_reflection_prompt
    assert "{previous_reflection}" in default_config.personal_reflection_prompt
    assert "{conversation_summary}" in default_config.personal_reflection_prompt
    assert "{conversation_summaries}" not in default_config.personal_reflection_prompt
    assert "{private_thoughts}" not in default_config.personal_reflection_prompt
    assert default_config.definitions.get_config_value_definition("save_personal_reflection_now") is not None
    assert default_config.definitions.get_config_value_definition("personal_reflection_load_mode") is not None
    assert default_config.definitions.get_config_value_definition("save_summary_now_also_save_personal_reflection") is not None
