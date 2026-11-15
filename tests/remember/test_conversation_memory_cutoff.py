"""Memory requests ignore summaries/thoughts/reflections written since the conversation started."""
import os
from unittest.mock import MagicMock

from src.characters_manager import Characters
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage, join_message
from src.remember.inner_monologue import InnerMonologue
from src.remember.personal_reflection import PersonalReflection
from src.remember.summaries import Summaries, drop_blocks_since

START_TS = 1000


def _write_file(folder_root, character, world_id: str, suffix: str, contents: str) -> None:
    folder = os.path.join(folder_root, world_id, f"{character.name} - {character.ref_id}")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{character.name}_{suffix}_1.txt"), "w", encoding="utf-8") as f:
        f.write(contents)


def _long_enough_thread(config, npc) -> message_thread:
    thread = message_thread(config, None)
    thread.add_message(join_message(npc, config))
    for i in range(5):
        thread.add_message(UserMessage(config, f"Player line number {i} with extra words."))
    return thread


def _call_text(call) -> str:
    return "\n".join(str(m["content"]) for m in call[0][0].get_openai_messages())


def _setup(skyrim, default_config, llm_client, language, npc, monkeypatch, responses):
    for module in ("summaries", "inner_monologue", "personal_reflection"):
        monkeypatch.setattr(f"src.remember.{module}.time.sleep", lambda *_args, **_kwargs: None)
    # The default thought prompt does not use {previous_thoughts}; add it so the tests can see it.
    default_config.inner_monologue_prompt += "\n{previous_thoughts}"
    mock_client = MagicMock()
    mock_client.request_call.side_effect = responses
    mock_client.token_limit = 4096
    mock_client.get_count_tokens.return_value = 1
    mock_client.is_too_long.return_value = False
    inner = InnerMonologue(skyrim, default_config, llm_client, language, mock_client)
    reflection = PersonalReflection(skyrim, default_config, llm_client, language, mock_client)
    rememberer = Summaries(
        skyrim,
        default_config,
        llm_client,
        language,
        summary_client=mock_client,
        inner_monologue=inner,
        personal_reflection=reflection,
    )
    characters = Characters()
    characters.add_or_update_character(npc)
    return mock_client, rememberer, _long_enough_thread(default_config, npc), characters


def _write_history(skyrim, npc, world_id):
    _write_file(
        skyrim.conversation_folder_path, npc, world_id, "summary",
        f"ts=500\n- Original summary.\n\nts={START_TS + 10}\n- Rejected summary.\n\nts={START_TS + 20}\n- Kept summary.\n\n",
    )
    _write_file(
        skyrim.thoughts_folder_path, npc, world_id, "thoughts",
        f"ts=500\nOriginal thought.\n\nts={START_TS + 10}\nRejected thought.\n\n",
    )
    _write_file(
        skyrim.reflections_folder_path, npc, world_id, "reflections",
        f"ts=500\nOriginal reflection.\nts={START_TS + 10}\nRejected reflection.\n",
    )


class TestDropBlocksSince:
    BLOCKS = [(500, "orig"), (1010, "first attempt"), (1020, "second attempt")]

    def test_none_keeps_all(self):
        assert drop_blocks_since(self.BLOCKS, None) == self.BLOCKS

    def test_drops_blocks_at_or_after_start(self):
        assert drop_blocks_since(self.BLOCKS, 1010) == [(500, "orig")]

    def test_keep_latest_since(self):
        assert drop_blocks_since(self.BLOCKS, START_TS, keep_latest_since=True) == [(500, "orig"), (1020, "second attempt")]

    def test_keep_latest_since_with_nothing_new(self):
        assert drop_blocks_since(self.BLOCKS, 2000, keep_latest_since=True) == self.BLOCKS


def test_save_summary_ignores_summaries_from_this_conversation(
    skyrim, default_config, llm_client, english_language_info, example_skyrim_npc_character, monkeypatch
):
    world_id = "cutoff-summary"
    _write_history(skyrim, example_skyrim_npc_character, world_id)
    mock_client, rememberer, thread, characters = _setup(
        skyrim, default_config, llm_client, english_language_info["language"], example_skyrim_npc_character,
        monkeypatch, ["New summary."],
    )
    rememberer.save_conversation_state(
        thread, characters, world_id, save_timestamp=START_TS + 30,
        save_thoughts=False, save_reflections=False, conversation_start_ts=START_TS,
    )
    prompt = _call_text(mock_client.request_call.call_args_list[0])
    assert "Original summary." in prompt
    assert "Rejected summary." not in prompt
    assert "Kept summary." not in prompt


def test_save_thoughts_only_sees_original_thoughts_and_summaries(
    skyrim, default_config, llm_client, english_language_info, example_skyrim_npc_character, monkeypatch
):
    world_id = "cutoff-thoughts"
    _write_history(skyrim, example_skyrim_npc_character, world_id)
    mock_client, rememberer, thread, characters = _setup(
        skyrim, default_config, llm_client, english_language_info["language"], example_skyrim_npc_character,
        monkeypatch, ["New thought."],
    )
    rememberer.save_thoughts_only(thread, characters, world_id, save_timestamp=START_TS + 30, conversation_start_ts=START_TS)
    prompt = _call_text(mock_client.request_call.call_args_list[0])
    assert "Original thought." in prompt
    assert "Rejected thought." not in prompt
    assert "Original summary." in prompt
    assert "Rejected summary." not in prompt
    assert "Kept summary." not in prompt


def test_save_reflections_only_sees_original_reflection_and_latest_summary(
    skyrim, default_config, llm_client, english_language_info, example_skyrim_npc_character, monkeypatch
):
    world_id = "cutoff-reflections"
    _write_history(skyrim, example_skyrim_npc_character, world_id)
    mock_client, rememberer, thread, characters = _setup(
        skyrim, default_config, llm_client, english_language_info["language"], example_skyrim_npc_character,
        monkeypatch, ["New reflection."],
    )
    rememberer.save_reflections_only(thread, characters, world_id, save_timestamp=START_TS + 30, conversation_start_ts=START_TS)
    prompt = _call_text(mock_client.request_call.call_args_list[0])
    assert "Original reflection." in prompt
    assert "Rejected reflection." not in prompt
    assert "Original summary." in prompt
    assert "Kept summary." in prompt
    assert "Rejected summary." not in prompt


def test_conversation_end_applies_cutoff_to_all_three(
    skyrim, default_config, llm_client, english_language_info, example_skyrim_npc_character, monkeypatch
):
    default_config.inner_monologue_enabled = True
    default_config.personal_reflection_enabled = True
    world_id = "cutoff-end"
    _write_history(skyrim, example_skyrim_npc_character, world_id)
    mock_client, rememberer, thread, characters = _setup(
        skyrim, default_config, llm_client, english_language_info["language"], example_skyrim_npc_character,
        monkeypatch, ["End summary.", "End thought.", "End reflection."],
    )
    rememberer.save_conversation_state(thread, characters, world_id, save_timestamp=START_TS + 30, conversation_start_ts=START_TS)
    summary_prompt, thought_prompt, reflection_prompt = (
        _call_text(call) for call in mock_client.request_call.call_args_list
    )
    for prompt in (summary_prompt, thought_prompt, reflection_prompt):
        assert "Original summary." in prompt
        assert "Rejected summary." not in prompt
        assert "Kept summary." not in prompt
    assert "End summary." not in thought_prompt
    assert "End summary." in reflection_prompt
    assert "Original thought." in thought_prompt
    assert "Rejected thought." not in thought_prompt
    assert "Original reflection." in reflection_prompt
    assert "Rejected reflection." not in reflection_prompt


def test_conversation_end_saves_thought_when_no_earlier_summary(
    skyrim, default_config, llm_client, english_language_info, example_skyrim_npc_character, monkeypatch
):
    default_config.inner_monologue_enabled = True
    default_config.personal_reflection_enabled = False
    world_id = "cutoff-first-meeting"
    mock_client, rememberer, thread, characters = _setup(
        skyrim, default_config, llm_client, english_language_info["language"], example_skyrim_npc_character,
        monkeypatch, ["First summary.", "First thought."],
    )
    rememberer.save_conversation_state(thread, characters, world_id, save_timestamp=START_TS + 30, conversation_start_ts=START_TS)
    thought_path = os.path.join(
        skyrim.thoughts_folder_path,
        world_id,
        f"{example_skyrim_npc_character.name} - {example_skyrim_npc_character.ref_id}",
        f"{example_skyrim_npc_character.name}_thoughts_1.txt",
    )
    with open(thought_path, encoding="utf-8") as f:
        assert "First thought." in f.read()


def test_conversation_passes_its_start_time(default_conversation, example_skyrim_npc_character, monkeypatch):
    captured = []

    def fake_save(*args, **kwargs):
        captured.append(kwargs.get("conversation_start_ts"))
        return {}

    rememberer = default_conversation._Conversation__rememberer
    monkeypatch.setattr(rememberer, "save_conversation_state", fake_save)
    monkeypatch.setattr(rememberer, "save_thoughts_only", fake_save)
    monkeypatch.setattr(rememberer, "save_reflections_only", fake_save)
    default_conversation.save_summary_only()
    default_conversation.save_inner_thoughts_only()
    default_conversation.save_personal_reflection_only()
    default_conversation._Conversation__save_summary_for_characters(
        default_conversation._Conversation__messages, [example_skyrim_npc_character], force=True
    )
    start_ts = default_conversation._Conversation__conversation_start_ts
    assert isinstance(start_ts, int)
    assert captured == [start_ts] * 4


def test_bio_editor_generate_ignores_reflections_since_npc_selected(
    skyrim, default_config, llm_client, english_language_info, example_skyrim_npc_character, monkeypatch
):
    world_id = "cutoff-bio-editor"
    _write_history(skyrim, example_skyrim_npc_character, world_id)
    mock_client, rememberer, thread, characters = _setup(
        skyrim, default_config, llm_client, english_language_info["language"], example_skyrim_npc_character,
        monkeypatch, ["Bio editor reflection."],
    )
    manager = PersonalReflection(skyrim, default_config, llm_client, english_language_info["language"], mock_client)
    result = manager.save_for_named_npc(
        example_skyrim_npc_character.name,
        example_skyrim_npc_character.bio,
        "ts=500\n- Original summary.\n",
        world_id,
        save_timestamp=START_TS + 30,
        selected_ts=START_TS,
    )
    assert result
    prompt = _call_text(mock_client.request_call.call_args_list[0])
    assert "Original reflection." in prompt
    assert "Rejected reflection." not in prompt
