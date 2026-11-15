"""Tests for how GameStateManager passes quest tag answers on: announcements on player input, and the check list request."""
from unittest.mock import MagicMock
import pytest
from src.game_manager import GameStateManager
from src.games.skyrim import Skyrim
from src.http.communication_constants import communication_constants as comm_consts
from src.quest_tags.conditions import CheckList

CHECK_LIST = CheckList({"battle": {2: "getstagedone CWObj 100=1"}})


@pytest.fixture
def game_manager(skyrim, default_chat_manager, default_config, english_language_info, llm_client, monkeypatch):
    manager = GameStateManager(skyrim, default_chat_manager, default_config, english_language_info, llm_client, None, "STT_SECRET_KEY.txt", "GPT_SECRET_KEY.txt")
    monkeypatch.setattr(Skyrim, "quest_check_list", property(lambda self: CHECK_LIST))
    monkeypatch.setattr(Skyrim, "get_quest_outcome_text", lambda self, tag, slot: "{player_name} won the battle.")
    talk = MagicMock()
    talk.context.world_id = "Player1"
    talk.context.npcs_in_conversation.get_player_character.return_value.name = "Dovahkiin"
    talk.hides_player.return_value = False
    manager._GameStateManager__talk = talk
    return manager


def _player_input(results: list[float], events: list[str]) -> dict:
    return {comm_consts.KEY_CONTEXT: {
        comm_consts.KEY_CONTEXT_INGAMEEVENTS: events,
        comm_consts.KEY_QUESTCHECKS_VERSION: CHECK_LIST.version,
        comm_consts.KEY_QUESTCHECKS_RESULTS: results,
    }}


def _send(game_manager: GameStateManager, request: dict) -> list[str] | None:
    """What the player's input request passes on as in-game events, as __handle_player_input does"""
    quest_events = game_manager._GameStateManager__get_quest_tag_announcements(request)
    game_manager._GameStateManager__update_context(request, quest_events)
    return game_manager._GameStateManager__talk.update_context.call_args.args[2]


def test_announcement_is_added_after_the_game_events(game_manager: GameStateManager):
    _send(game_manager, _player_input([0.0], []))  # The first check: background, nothing announced
    events = _send(game_manager, _player_input([1.0], ["The location is now Whiterun.", "Dovahkiin picked up / took Iron Sword"]))
    assert events == ["The location is now Whiterun.", "Dovahkiin picked up / took Iron Sword", "Dovahkiin won the battle."]


def test_first_check_announces_nothing(game_manager: GameStateManager):
    assert _send(game_manager, _player_input([1.0], [])) == []


def test_director_mode_leaves_out_the_events_about_the_player_but_keeps_the_quest_events(game_manager: GameStateManager):
    game_manager._GameStateManager__talk.hides_player.return_value = True
    _send(game_manager, _player_input([0.0], []))
    events = _send(game_manager, _player_input([1.0], ["The location is now Whiterun.", "Dovahkiin picked up / took Iron Sword"]))
    assert events == ["The location is now Whiterun.", "Dovahkiin won the battle."]


def test_watching_an_npc_event_in_director_mode_leaves_out_the_events_about_the_player(game_manager: GameStateManager):
    talk = game_manager._GameStateManager__talk
    talk.hides_player.return_value = True
    talk.context.npcs_in_conversation.get_player_character.return_value = None # Watching: the player is not in the conversation
    talk.context.npc_event.player.name = "Dovahkiin"
    game_manager._GameStateManager__update_context(_player_input([], ["Dovahkiin casted the spell / consumed Healing", "The player took damage.", "Lydia drew her sword."]))
    assert talk.update_context.call_args.args[2] == ["Lydia drew her sword."]


def test_check_list_reply(game_manager: GameStateManager):
    reply = game_manager.quest_check_list()
    assert reply[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_QUESTCHECKLIST
    assert reply[comm_consts.KEY_QUESTCHECKS_LIST][comm_consts.KEY_QUESTCHECKS_FUNCTIONS] == ["getstagedone"]
