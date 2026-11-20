"""The End Conversation and Interrupt hotkeys act right away, also while Mantella still waits for the AI model to answer.

The route answers the request the game is waiting for at once ("conversation over" or "your turn"), and the conversation stops whatever
it waits for: the AI model, the player's voice, a Game Master turn. What the NPCs remember is saved after the game has been told.
"""
import asyncio
import threading
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.config_loader import ConfigLoader
from src.game_manager import GameStateManager
from src.http.communication_constants import communication_constants as comm_consts
from src.http.routes.mantella_route import mantella_route
from tests.test_conversation_events_flow import HULDA, FakeGame, FakeMicrophone, FakeModel, manager  # noqa: F401 (the manager fixture)

QUICK = 2.0 # Seconds: the hotkeys must act well before the model answers, which is never in these tests unless released


class SlowModel(FakeModel):
    """The NPCs' model only answers once released, like OpenRouter taking its time"""

    def __init__(self):
        super().__init__()
        self.hold = False
        self.asked = threading.Event()
        self.answer = threading.Event()

    async def streaming_call(self, messages, is_multi_npc: bool, response_started_at: float | None = None, **kwargs):
        if self.hold:
            self.asked.set()
            while not self.answer.is_set():
                await asyncio.sleep(0.01)
        async for content in super().streaming_call(messages, is_multi_npc, response_started_at):
            yield content


@pytest.fixture
def model() -> SlowModel:
    return SlowModel()


@pytest.fixture(params=[False, True], ids=["one reply for everyone", "game master mode"])
def game(request, manager: GameStateManager, default_config: ConfigLoader, model: SlowModel):
    default_config.multi_npc_game_master_mode = request.param
    game = FakeGame(manager)
    game.start(HULDA) # Until the player's turn
    yield game
    model.answer.set() # Lets anything still generating finish


def player_line(text: str) -> dict[str, Any]:
    return {comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_PLAYERINPUT, comm_consts.KEY_REQUESTTYPE_PLAYERINPUT: text,
            comm_consts.KEY_CONTEXT: FakeGame.context()}


def run(call, *args) -> tuple[threading.Thread, dict[str, Any]]:
    """Runs a request in its own thread, as the route does. The reply lands in the dict"""
    result: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: result.update(reply=call(*args)), daemon=True)
    thread.start()
    return thread, result


def waiting_for_the_model(game: FakeGame, model: SlowModel) -> tuple[threading.Thread, dict[str, Any]]:
    """The player said something, and the continue request waits for the NPC's answer"""
    model.hold = True
    reply = game.manager.player_input(player_line("What news from the war?"))
    assert reply[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_NPCTALK
    thread, result = run(game.manager.continue_conversation, game.continue_request())
    assert model.asked.wait(5)
    time.sleep(0.1)
    assert "reply" not in result # Still waiting
    return thread, result


# The conversation side

def test_ending_stops_the_wait_for_the_model(game: FakeGame, model: SlowModel):
    thread, result = waiting_for_the_model(game, model)

    game.manager.stop_conversation_now()
    thread.join(QUICK)

    assert result["reply"][comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_ENDCONVERSATION
    assert not model.answer.is_set() # The model never answered
    game.manager.end_requested_conversation()
    assert game.manager._GameStateManager__talk is None


def test_the_interrupt_hotkey_stops_the_wait_for_the_model(game: FakeGame, model: SlowModel):
    thread, result = waiting_for_the_model(game, model)

    assert game.manager.interrupt_conversation({comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_INTERRUPT})
    thread.join(QUICK)

    assert result["reply"][comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_PLAYERTALK
    assert not model.answer.is_set()


def test_after_an_interruption_the_player_speaks_and_the_npc_answers(game: FakeGame, model: SlowModel):
    thread, _ = waiting_for_the_model(game, model)
    game.manager.interrupt_conversation({comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_INTERRUPT})
    thread.join(QUICK)

    model.hold = False
    model.answer.set()
    game.say("Sorry, go on.")

    assert game.lines[-1][0] == HULDA.name
    talk = game.manager._GameStateManager__talk
    assert "Interrupting..." in str(talk._Conversation__messages.get_openai_messages())


def test_ending_stops_the_wait_for_the_players_voice(manager: GameStateManager, model: SlowModel):
    microphone = FakeMicrophone()
    manager._GameStateManager__stt = microphone
    manager._GameStateManager__mic_input = True
    game = FakeGame(manager)
    game.start(HULDA)
    talk = manager._GameStateManager__talk
    talk.set_mic_setup(microphone, True, False)
    thread, result = run(manager.player_input, player_line(""))
    assert microphone.waiting.wait(5)

    manager.stop_conversation_now()
    thread.join(QUICK)

    assert "reply" in result # Done, without the player saying anything


def test_a_conversation_ended_before_its_start_request_ran_never_starts(manager: GameStateManager):
    ends_before = manager.ends_requested
    manager.stop_conversation_now() # The player ended it while the start request waited for the requests before it

    reply = manager.start_conversation({comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_STARTCONVERSATION}, ends_before)

    assert reply[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_ENDCONVERSATION
    assert manager._GameStateManager__talk is None


def test_the_end_of_the_last_conversation_is_handled_by_the_next_start(game: FakeGame):
    old_talk = game.manager._GameStateManager__talk
    game.manager.stop_conversation_now() # Its end has not run yet when the player starts the next conversation
    game.start(HULDA)

    assert old_talk.has_already_ended
    new_talk = game.manager._GameStateManager__talk
    game.manager.end_requested_conversation() # The last conversation's end, running late: the new one goes on
    assert game.manager._GameStateManager__talk is new_talk


def test_an_end_that_arrives_while_the_conversation_starts_ends_it(game: FakeGame):
    talk = game.manager._GameStateManager__talk
    game.manager.end_requested_conversation() # Nothing was ended: the conversation goes on
    assert game.manager._GameStateManager__talk is talk

    game.manager.stop_conversation_now()
    game.manager.end_requested_conversation()
    assert game.manager._GameStateManager__talk is None


# The route

class StuckManager:
    """Stands in for GameStateManager: the continue request waits until released, like one waiting for the AI model"""

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()
        self.ended = threading.Event()
        self.ends = 0
        self.can_interrupt = True

    @property
    def ends_requested(self) -> int:
        return self.ends

    def continue_conversation(self, input_json: dict[str, Any]) -> dict[str, Any]:
        self.started.set()
        self.release.wait(10)
        return {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_NPCTALK}

    def interrupt_conversation(self, input_json: dict[str, Any]) -> bool:
        return self.can_interrupt

    def player_turn_reply(self) -> dict[str, Any]:
        return {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_PLAYERTALK}

    def stop_conversation_now(self):
        self.ends += 1

    def end_requested_conversation(self):
        self.ended.set()


@pytest.fixture
def stuck() -> StuckManager:
    return StuckManager()


@pytest.fixture
def client(default_config: ConfigLoader, english_language_info: dict, stuck: StuckManager):
    route = mantella_route(default_config, "STT_SECRET_KEY.txt", "IMAGE_SECRET_KEY.txt", "GPT_SECRET_KEY.txt", english_language_info)
    route._can_route_be_used = lambda: True
    route._mantella_route__game = stuck
    route._mantella_route__add_extras = lambda reply: reply # No voice clips in these tests
    app = FastAPI()
    route.add_route_to_server(app)
    with TestClient(app) as client:
        yield client
    stuck.release.set()


def post(client: TestClient, request_type: str) -> dict[str, Any]:
    return client.post("/mantella", json={comm_consts.KEY_REQUESTTYPE: request_type}).json()


def continue_in_background(client: TestClient, stuck: StuckManager) -> tuple[threading.Thread, dict[str, Any]]:
    thread, result = run(post, client, comm_consts.KEY_REQUESTTYPE_CONTINUECONVERSATION)
    assert stuck.started.wait(5)
    return thread, result


def test_the_end_answers_the_request_the_game_waits_for_right_away(client: TestClient, stuck: StuckManager):
    thread, result = continue_in_background(client, stuck)

    end_reply = post(client, comm_consts.KEY_REQUESTTYPE_ENDCONVERSATION)
    thread.join(QUICK)

    assert result["reply"][comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_ENDCONVERSATION
    # The game cleans up once: the end request's own reply is one it ignores
    assert end_reply[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_INTERRUPTRECEIVED
    # The conversation ends once the request still running is done
    assert not stuck.ended.is_set()
    stuck.release.set()
    assert stuck.ended.wait(5)


def test_the_end_is_answered_with_the_end_when_the_game_waits_for_nothing(client: TestClient, stuck: StuckManager):
    end_reply = post(client, comm_consts.KEY_REQUESTTYPE_ENDCONVERSATION)

    assert end_reply[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_ENDCONVERSATION
    assert stuck.ended.wait(5)


def test_the_interrupt_hotkey_gives_the_player_the_turn_right_away(client: TestClient, stuck: StuckManager):
    thread, result = continue_in_background(client, stuck)

    interrupt_reply = post(client, comm_consts.KEY_REQUESTTYPE_INTERRUPT)
    thread.join(QUICK)

    assert interrupt_reply[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_INTERRUPTRECEIVED
    assert result["reply"][comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_PLAYERTALK


def test_an_interruption_with_nothing_to_interrupt_leaves_the_request_running(client: TestClient, stuck: StuckManager):
    stuck.can_interrupt = False # The goodbye that ends the conversation is being said
    thread, result = continue_in_background(client, stuck)

    post(client, comm_consts.KEY_REQUESTTYPE_INTERRUPT)
    thread.join(0.3)
    assert "reply" not in result

    stuck.release.set()
    thread.join(5)
    assert result["reply"][comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_NPCTALK
