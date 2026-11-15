"""When a conversation gets too long it is cut: the cut lines must be kept as memories and in the conversation log"""
from unittest.mock import MagicMock

from src.conversation import conversation as conv_module
from src.conversation.conversation import Conversation
from tests.test_shout import HULDA_NPC, PLAYER, config, thread_with_lines


def conversation_with_lines(monkeypatch, *lines: str) -> tuple[Conversation, list, list]:
    cfg = config()
    cfg.conversation_summary_enabled = True
    context = MagicMock()
    context.config = cfg
    context.world_id = "world"
    context.npcs_in_conversation.get_all_characters.return_value = [PLAYER, HULDA_NPC]
    conversation = Conversation(context, MagicMock(), MagicMock(), MagicMock(), None, False, False)
    conversation._Conversation__messages = thread_with_lines(cfg, *lines)
    conversation._Conversation__conversation_type = MagicMock()
    conversation._Conversation__conversation_type.generate_prompt.return_value = "New prompt"
    conversation._Conversation__llm_client.is_too_long.side_effect = lambda messages, percent: len(messages) > 1 # Room for one line
    logged: list = []
    monkeypatch.setattr(conv_module.conversation_log, "save_conversation_log", lambda character, messages, world_id: logged.append((character, messages)))
    summarized: list = []
    conversation._Conversation__rememberer.save_conversation_state.side_effect = lambda messages, *args, **kwargs: summarized.append(messages)
    return conversation, logged, summarized


def test_cutting_a_long_conversation_remembers_and_logs_the_cut_lines(monkeypatch):
    conversation, logged, summarized = conversation_with_lines(monkeypatch, "First.", "Second.", "Third.")

    conversation.reload_conversation()

    assert [m.text for m in conversation._Conversation__messages.get_talk_only()] == ["Third."]
    assert len(summarized) == 1
    assert [m.text for m in summarized[0].get_talk_only()] == ["First.", "Second."]
    # Only the NPC gets a log, with the cut lines only: the kept line is logged when the conversation ends
    assert [(character.name, [m["content"] for m in messages]) for character, messages in logged] == [("Hulda", ["First.", "Second."])]


def test_nothing_is_saved_when_nothing_is_cut(monkeypatch):
    conversation, logged, summarized = conversation_with_lines(monkeypatch, "Only.")

    conversation.reload_conversation()

    assert [m.text for m in conversation._Conversation__messages.get_talk_only()] == ["Only."]
    assert summarized == [] and logged == []
