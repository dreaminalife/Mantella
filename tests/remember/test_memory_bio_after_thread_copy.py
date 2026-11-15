"""Memory prompts must not lose NPC bios when the message thread holds copied Characters."""
from unittest.mock import MagicMock

from src.character_manager import Character
from src.characters_manager import Characters
from src.games.equipment import Equipment
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage, join_message, leave_message
from src.remember.summaries import Summaries


def _npc(name: str, ref_id: str, bio: str) -> Character:
    return Character(ref_id, ref_id, name, 0, "Nord", False, bio, False, False, 0, False, "", "", "", "", "", Equipment({}), {})


def _thread(config, *npcs: Character) -> message_thread:
    thread = message_thread(config, "system prompt")
    for npc in npcs:
        thread.add_message(join_message(npc, config))
    for i in range(5):
        thread.add_message(UserMessage(config, f"Player line number {i} with extra words.", "Dragonborn"))
    return thread


def test_lookup_uses_live_bio_after_undo_copy(default_config, default_rememberer):
    lydia, faendal = _npc("Lydia", "A1", "Lydia bio"), _npc("Faendal", "B2", "")
    thread = _thread(default_config, lydia, faendal)
    # Undo / reload write deep copies back into the thread
    thread.replace_persistent_messages(thread.get_persistent_messages())
    faendal.bio = "Faendal edited in Bio Editor"
    live = Characters()
    live.add_or_update_character(lydia)
    live.add_or_update_character(faendal)

    assert default_rememberer.get_character_lookup_dict(thread)["Faendal"].bio == ""
    characters = default_rememberer.get_character_lookup_dict(thread, live)
    assert characters["Faendal"].bio == "Faendal edited in Bio Editor"
    assert characters["Lydia"].bio == "Lydia bio"


def test_lookup_keeps_history_character_for_departed_npc(default_config, default_rememberer):
    lydia, faendal = _npc("Lydia", "A1", "Lydia bio"), _npc("Faendal", "B2", "Faendal bio")
    thread = _thread(default_config, lydia, faendal)
    thread.add_message(leave_message(faendal, default_config))
    live = Characters()
    live.add_or_update_character(lydia)

    characters = default_rememberer.get_character_lookup_dict(thread, live)
    assert characters["Faendal"].bio == "Faendal bio"


def test_memory_prompt_uses_bio_edited_after_undo_copy(
    skyrim, default_config, llm_client, english_language_info, monkeypatch
):
    monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
    mock_client = MagicMock()
    mock_client.request_call.return_value = "They talked."
    mock_client.token_limit = 4096
    mock_client.get_count_tokens.return_value = 1
    rememberer = Summaries(skyrim, default_config, llm_client, english_language_info["language"], summary_client=mock_client)

    faendal = _npc("Faendal", "B2", "")
    thread = _thread(default_config, faendal)
    thread.replace_persistent_messages(thread.get_persistent_messages())
    faendal.bio = "Faendal edited in Bio Editor"
    live = Characters()
    live.add_or_update_character(faendal)
    rememberer.save_conversation_state(thread, live, "memory-bio-after-copy", save_timestamp=12345)

    prompt = mock_client.request_call.call_args[0][0].get_openai_messages()[0]["content"]
    assert "Faendal edited in Bio Editor" in prompt


def test_live_editor_save_keeps_bio_of_departed_npc(default_conversation, default_config):
    lydia, faendal = _npc("Lydia", "A1", "Lydia bio"), _npc("Faendal", "B2", "Faendal bio")
    thread = _thread(default_config, lydia, faendal)
    thread.add_message(leave_message(faendal, default_config))
    default_conversation._Conversation__messages = thread
    default_conversation.context.npcs_in_conversation.add_or_update_character(lydia)

    assert default_conversation.apply_conversation_from_json(default_conversation.get_conversation_as_json())

    bios = {m.character.name: m.character.bio for m in thread.get_messages_of_type((join_message, leave_message))}
    assert bios == {"Lydia": "Lydia bio", "Faendal": "Faendal bio"}
