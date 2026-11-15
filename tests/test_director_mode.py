from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config.definitions.game_definitions import GameEnum
from src.config.definitions.llm_definitions import NarrationIndicatorsEnum
from src.conversation.conversation import Conversation
from src.conversation.conversation_serializer import deserialize_persistent_messages, serialize_persistent_messages
from src.conversation.conversation_type import multi_npc, multi_npc_game_master, npc_event, pc_to_npc, shout
from src.llm.messages import UserMessage


def config(director: bool = True, game: GameEnum = GameEnum.SKYRIM, narration: NarrationIndicatorsEnum = NarrationIndicatorsEnum.PARANTHESES):
    cfg = MagicMock()
    cfg.multi_conversation_director_mode = director
    cfg.game.base_game = game
    cfg.narration_indicators = narration
    cfg.actions = []
    cfg.automatic_greeting = True
    return cfg


def director_message(cfg, text: str) -> UserMessage:
    message = UserMessage(cfg, text, "Dragonborn")
    message.is_multi_npc_message = True
    message.is_director_message = True
    return message


def test_director_message_is_narration_without_the_player_name():
    assert director_message(config(), "A guard bursts in.").get_formatted_content() == "(A guard bursts in.)"


def test_director_message_uses_the_configured_narration_indicators():
    cfg = config(narration=NarrationIndicatorsEnum.ASTERISKS)
    assert director_message(cfg, "A guard bursts in.").get_formatted_content() == "*A guard bursts in.*"


def test_empty_director_message_only_shows_its_events():
    message = director_message(config(), "")
    message.add_event(["Lydia drew her sword."])
    assert message.get_formatted_content() == "(Lydia drew her sword.)"


def test_player_message_outside_director_mode_keeps_the_player_name():
    message = UserMessage(config(), "Hello.", "Dragonborn")
    message.is_multi_npc_message = True
    assert message.get_formatted_content() == "Dragonborn: Hello."


def test_director_message_survives_save_and_load():
    cfg = config()
    loaded = deserialize_persistent_messages(serialize_persistent_messages([director_message(cfg, "A guard bursts in.")], cfg), cfg)
    assert loaded[0].is_director_message is True
    assert loaded[0].get_formatted_content() == "(A guard bursts in.)"


@pytest.mark.parametrize("director, game, expected", [
    (True, GameEnum.SKYRIM, True),
    (False, GameEnum.SKYRIM, False),
    (True, GameEnum.FALLOUT4, False),
])
def test_director_mode_is_skyrim_only(director, game, expected):
    cfg = config(director, game)
    assert multi_npc(cfg).is_director_mode() is expected
    assert multi_npc_game_master(cfg).is_director_mode() is expected
    assert shout(cfg).is_director_mode() is expected
    assert pc_to_npc(cfg).is_director_mode() is False # A directed one-on-one runs as a group type with one NPC
    assert npc_event(cfg).is_director_mode() is False # The player only watches


def test_a_conversation_an_npc_started_is_never_directed():
    cfg = config()
    for conversation_type_class in (multi_npc, multi_npc_game_master, shout):
        assert conversation_type_class(cfg, can_direct=False).is_director_mode() is False


def test_multi_npc_director_mode_uses_the_director_prompt():
    cfg = config()
    context = MagicMock()
    multi_npc(cfg).generate_prompt(context)
    assert context.generate_system_message.call_args.args[0] is cfg.multi_npc_director_prompt
    assert context.generate_system_message.call_args.args[4] == "skyrim_multi_npc_director_prompt"


FOLLOW = SimpleNamespace(keyword="Follow", use_in_on_on_one=True, use_in_multi_npc=False)
CHAT = SimpleNamespace(keyword="Chat", use_in_on_on_one=False, use_in_multi_npc=True)


@pytest.mark.parametrize("has_more_npcs, expected", [(False, ["Follow"]), (True, ["Chat"])])
def test_a_directed_npc_alone_keeps_the_one_on_one_actions(has_more_npcs, expected):
    cfg = config()
    cfg.actions = [FOLLOW, CHAT]
    context = MagicMock()
    context.npcs_in_conversation.contains_multiple_npcs.return_value = has_more_npcs
    multi_npc(cfg).generate_prompt(context) # Without Game Master Mode
    assert [a.keyword for a in context.generate_system_message.call_args.args[1]] == expected
    multi_npc_game_master(cfg).generate_npc_prompt(context, MagicMock()) # With Game Master Mode
    assert [a.keyword for a in context.generate_system_message.call_args.args[1]] == expected


def test_the_director_prompts_offer_the_actions():
    from src.config.definitions.prompt_definitions import PromptDefinitions
    assert "{actions}" in PromptDefinitions.get_skyrim_multi_npc_director_prompt_config_value().default_value
    npc_prompt = PromptDefinitions.get_skyrim_game_master_npc_prompt_config_value().default_value
    for conditions in ({"director"}, {"director", "shout"}):
        text = PromptDefinitions.apply_prompt_conditions("skyrim_game_master_npc_prompt", npc_prompt, conditions)
        assert text.count("## ACTIONS") == 1 and "{actions}" in text, conditions


@pytest.mark.parametrize("director, conditions", [(True, {"director"}), (False, set())])
def test_game_master_passes_director_as_a_condition_of_the_merged_prompts(director, conditions):
    cfg = config(director)
    context = MagicMock()
    conversation_type = multi_npc_game_master(cfg)
    conversation_type.generate_game_master_prompt(context)
    assert context.generate_system_message.call_args.args[4] == "skyrim_game_master_prompt"
    assert context.generate_system_message.call_args.kwargs["conditions"] == conditions
    conversation_type.generate_npc_prompt(context, MagicMock())
    assert context.generate_system_message.call_args.args[4] == "skyrim_game_master_npc_prompt"
    assert context.generate_system_message.call_args.kwargs["conditions"] == conditions


def test_game_master_conversation_prompt_is_not_logged():
    # The Game Master logs its prompt when it sends it, so the prompt stored in the conversation is not logged too
    context = MagicMock()
    multi_npc_game_master(config()).generate_prompt(context, should_log=True)
    assert context.generate_system_message.call_args.args[2] is False


def test_director_mode_has_no_greeting():
    context = MagicMock()
    context.config = config()
    messages = MagicMock()
    messages.__len__.return_value = 1
    assert multi_npc(context.config).get_user_message(context, messages) is None


def director_conversation(conversation_type_class, events: list[str] | None = None) -> Conversation:
    context = MagicMock()
    context.config = config()
    context.config.end_conversation_keyword = "goodbye"
    context.config.max_count_events = 5
    context.get_context_ingame_events.return_value = list(events or [])
    context.npcs_in_conversation.contains_multiple_npcs.return_value = True
    context.npcs_in_conversation.get_player_character.return_value.name = "Dragonborn"
    conversation = Conversation(context, MagicMock(), MagicMock(), MagicMock(), None, False, False)
    conversation._Conversation__conversation_type = conversation_type_class(context.config)
    conversation._Conversation__game_master_turns = MagicMock()
    return conversation


def messages_of(conversation: Conversation) -> list:
    return conversation._Conversation__messages.get_persistent_messages()


@pytest.mark.parametrize("conversation_type_class", [multi_npc, multi_npc_game_master])
def test_director_input_is_sent_as_a_scene_direction(conversation_type_class):
    conversation = director_conversation(conversation_type_class)
    _, _, player_voiceline = conversation.process_player_input("A guard bursts in.")
    [message] = messages_of(conversation)
    assert message.get_formatted_content() == "(A guard bursts in.)"
    assert not message.is_system_generated_message
    assert player_voiceline is None


def test_continue_in_director_mode_sends_a_message_that_summaries_leave_out():
    conversation = director_conversation(multi_npc)
    conversation.process_player_input(".")
    [message] = messages_of(conversation)
    assert message.get_formatted_content() == "(The scene continues.)"
    assert message.is_system_generated_message
    assert conversation._Conversation__messages.get_talk_only() == []


def test_continue_in_game_master_director_mode_adds_nothing_and_starts_the_npcs():
    conversation = director_conversation(multi_npc_game_master)
    conversation.process_player_input(" . ")
    assert messages_of(conversation) == []
    conversation._Conversation__game_master_turns.start_turn.assert_called_once()


@pytest.mark.parametrize("conversation_type_class", [multi_npc, multi_npc_game_master])
def test_interrupting_in_director_mode_adds_no_interrupting_event(conversation_type_class):
    conversation = director_conversation(conversation_type_class)
    conversation._Conversation__is_player_interrupting = True
    conversation.process_player_input("A guard bursts in.")
    [message] = messages_of(conversation)
    assert message.get_formatted_content() == "(A guard bursts in.)"
    assert not conversation._Conversation__is_player_interrupting


def test_interrupting_then_continuing_in_game_master_director_mode_adds_nothing():
    conversation = director_conversation(multi_npc_game_master)
    conversation._Conversation__is_player_interrupting = True
    conversation.process_player_input(".")
    assert messages_of(conversation) == []
    conversation._Conversation__game_master_turns.start_turn.assert_called_once()


def test_interrupting_outside_director_mode_keeps_the_interrupting_event():
    conversation = director_conversation(multi_npc)
    conversation._Conversation__conversation_type = multi_npc(config(director=False))
    conversation._Conversation__is_player_interrupting = True
    conversation.process_player_input("Wait.")
    [message] = messages_of(conversation)
    assert message.get_formatted_content() == "(Interrupting...) Dragonborn: Wait."


def game_master_conversation(events: list[str] | None = None) -> Conversation:
    conversation = director_conversation(multi_npc_game_master, events)
    conversation._Conversation__conversation_type = multi_npc_game_master(config(director=False))
    return conversation


def test_continue_in_game_master_mode_adds_nothing_and_starts_the_npcs():
    conversation = game_master_conversation()
    _, _, player_voiceline = conversation.process_player_input(" . ")
    assert messages_of(conversation) == []
    assert player_voiceline is None
    conversation._Conversation__game_master_turns.start_turn.assert_called_once()


def test_continue_in_game_master_mode_keeps_the_game_events_without_the_player_name():
    conversation = game_master_conversation(["Lydia drew her sword."])
    conversation._Conversation__is_player_interrupting = True
    conversation.process_player_input(".")
    [message] = messages_of(conversation)
    assert message.get_formatted_content() == "(Lydia drew her sword.)"


def test_player_input_in_game_master_mode_is_still_spoken_by_the_player():
    conversation = game_master_conversation()
    conversation.process_player_input("Hello.")
    [message] = messages_of(conversation)
    assert message.get_formatted_content() == "Dragonborn: Hello."


@pytest.mark.parametrize("conversation_type_class", [multi_npc, multi_npc_game_master])
def test_continue_keeps_the_game_events(conversation_type_class):
    conversation = director_conversation(conversation_type_class, ["Lydia drew her sword."])
    conversation.process_player_input(".")
    [message] = messages_of(conversation)
    assert message.get_formatted_content() == "(Lydia drew her sword.)"
    assert not message.is_system_generated_message
