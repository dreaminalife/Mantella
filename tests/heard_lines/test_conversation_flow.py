"""End-to-end: heard lines arriving with the game's requests during whole conversations, with made-up models (see
tests/test_conversation_events_flow.py for FakeGame and FakeModel)"""
import logging
import time
from typing import Any

import pytest

from src.game_manager import GameStateManager
from src.http.communication_constants import communication_constants as comm_consts
from tests.heard_lines.lines import CARLOTTA, HULDA, MIKAEL, SAADIA, hand_over, menu_line, scene_line
from tests.test_conversation_events_flow import FakeGame, FakeModel, PLAYER, HULDA as HULDA_PERSON, MIKAEL as MIKAEL_PERSON, manager, model # noqa: F401 (fixtures)

WORLD = PLAYER.name + "Test1" # The world FakeGame starts its conversations in
HEARD = "Your beauty outshines the sun."
REQUESTS_WITH_LINES = {comm_consts.KEY_REQUESTTYPE_STARTCONVERSATION, comm_consts.KEY_REQUESTTYPE_CONTINUECONVERSATION,
                       comm_consts.KEY_REQUESTTYPE_PLAYERINPUT, comm_consts.KEY_REQUESTTYPE_CONVERSATIONEVENT}


class HeardLinesGame(FakeGame):
    """FakeGame that hands the caught lines over like MantellaConversation.AddHeardLines: with the next request that carries them"""
    def __init__(self, manager: GameStateManager):
        super().__init__(manager)
        self.heard: str | None = None

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.heard and request[comm_consts.KEY_REQUESTTYPE] in REQUESTS_WITH_LINES:
            request[comm_consts.KEY_HEARDLINES] = self.heard
            self.heard = None
        return super().send(request)


@pytest.fixture
def game(manager: GameStateManager) -> HeardLinesGame:
    return HeardLinesGame(manager)


def kept(manager: GameStateManager, name: str, ref_id: str, world: str = WORLD) -> list[str]:
    store = manager._GameStateManager__heard_lines_store
    return [line.words for line in store.lines_for(world, name, ref_id)]


def scene(count: int, heard_by=None) -> str:
    now = int(time.time())
    return hand_over(*(scene_line(f"Line {i}.", time=now + i, heard_by=heard_by or [HULDA]) for i in range(count)))


def test_a_whole_scene_goes_in_as_narration_past_max_count_events(game: HeardLinesGame, model: FakeModel, caplog):
    game.config.max_count_events = 5
    game.config.ingame_events_filter = ["*mikael*", "*line*"] # Ignored In-Game Events never match heard lines
    game.start(HULDA_PERSON)
    game.heard = scene(10)
    with caplog.at_level(logging.DEBUG):
        game.say("Did you hear that?")
    request = model.npc_requests[-1]
    for i in range(10):
        assert f'Nearby, Mikael says to Carlotta: "Line {i}."' in request
    assert request.index("Line 9.") < request.index("Did you hear that?") # Before the player's text
    assert "Heard nearby since previous exchange" in caplog.text
    assert kept(game.manager, "Hulda", "01A66E") == [] # In the conversation now. Kept when it ends


def test_most_heard_lines_per_npc_cuts_the_oldest_from_one_message(game: HeardLinesGame, model: FakeModel):
    game.config.heard_lines_most_per_npc = 3
    game.start(HULDA_PERSON)
    game.heard = scene(10)
    game.say("Did you hear that?")
    request = model.npc_requests[-1]
    assert "Line 6." not in request and all(f"Line {i}." in request for i in (7, 8, 9))


def test_director_mode_leaves_out_menu_talk_with_the_player(game: HeardLinesGame, model: FakeModel, monkeypatch):
    game.start(HULDA_PERSON)
    talk = game.manager.active_conversation
    monkeypatch.setattr(talk, "hides_player", lambda: True)
    now = int(time.time())
    game.heard = hand_over(menu_line("Any rumors?", "Not today.", speaker=SAADIA, heard_by=[SAADIA, HULDA], time=now),
                           scene_line(HEARD, time=now + 1, heard_by=[HULDA]))
    game.say("The scene goes on.")
    assert "Not today." not in model.npc_requests[-1] and HEARD in model.npc_requests[-1]


def test_game_master_continue_with_only_heard_lines_still_sends_them(game: HeardLinesGame, model: FakeModel):
    game.start(HULDA_PERSON, MIKAEL_PERSON) # A group with the Game Master
    game.heard = hand_over(scene_line(HEARD, time=int(time.time()), heard_by=[HULDA]))
    model.picks = [HULDA_PERSON.name]
    game.say(".") # The NPCs go on, without new player input or in-game events
    assert any(f'Mikael says to Carlotta: "{HEARD}"' in request for request in model.npc_requests)


def test_a_line_said_by_someone_in_the_conversation_is_not_nearby(game: HeardLinesGame, model: FakeModel):
    game.start(HULDA_PERSON, MIKAEL_PERSON) # Mikael is in it. Carlotta isn't
    now = int(time.time())
    game.heard = hand_over(scene_line(HEARD, time=now, heard_by=[HULDA]),
                           scene_line("Leave me be.", speaker=CARLOTTA, to=SAADIA, time=now + 1, heard_by=[HULDA]))
    sent_before = len(model.npc_requests)
    model.picks = [HULDA_PERSON.name]
    game.say("Did you hear that?")
    sent = "\n".join(model.npc_requests[sent_before:])
    assert f'Mikael says to Carlotta: "{HEARD}"' in sent and "Nearby, Mikael" not in sent
    assert 'Nearby, Carlotta says to Saadia: "Leave me be."' in sent # Neither is in the conversation


def test_menu_talk_with_the_player_is_not_nearby(game: HeardLinesGame, model: FakeModel):
    game.start(HULDA_PERSON)
    game.heard = hand_over(menu_line("Any rumors?", "Not today.", speaker=SAADIA, heard_by=[SAADIA, HULDA], time=int(time.time())))
    game.say("Did you hear that?")
    request = model.npc_requests[-1]
    assert 'Kit asks Saadia: "Any rumors?" Saadia answers: "Not today."' in request # The player is in the conversation
    assert "Nearby, Kit" not in request


def test_a_line_in_the_conversation_is_not_also_in_memory_while_it_runs(game: HeardLinesGame, model: FakeModel):
    """Game Master Mode builds a prompt for every NPC turn: the line must not come back as a memory"""
    game.start(HULDA_PERSON, MIKAEL_PERSON)
    game.heard = hand_over(scene_line(HEARD, time=int(time.time()), heard_by=[HULDA]))
    sent_before = len(model.npc_requests)
    model.picks = [HULDA_PERSON.name]
    game.say("Did you hear that?")
    model.picks = [HULDA_PERSON.name] # Her prompt is built again for this turn
    game.say("What do you make of it?")
    assert len(model.npc_requests) - sent_before >= 2
    assert not any("Hulda overheard Mikael say" in request for request in model.npc_requests[sent_before:])
    assert kept(game.manager, "Hulda", "01A66E") == []
    game.end()
    assert kept(game.manager, "Hulda", "01A66E") == [HEARD]


def test_a_scene_that_loops_during_the_conversation_is_given_once_and_remembered_once(game: HeardLinesGame, model: FakeModel):
    game.start(HULDA_PERSON)
    now = int(time.time())
    game.heard = hand_over(scene_line(HEARD, time=now, heard_by=[HULDA]), scene_line("Thank you, thank you.", time=now + 1, heard_by=[HULDA]))
    game.say("Did you hear that?")
    assert HEARD in model.npc_requests[-1]
    game.heard = hand_over(scene_line(HEARD, time=now + 60, heard_by=[HULDA]), scene_line("Something new.", time=now + 61, heard_by=[HULDA]))
    game.say("He's singing it again.")
    request = model.npc_requests[-1]
    assert "Something new." in request
    assert request.count(HEARD) == 1 # Only from the earlier message, not given again
    game.end()
    assert kept(game.manager, "Hulda", "01A66E") == [HEARD, "Thank you, thank you.", "Something new."]


def test_a_line_repeated_in_one_message_goes_in_once(game: HeardLinesGame, model: FakeModel):
    game.start(HULDA_PERSON)
    now = int(time.time())
    game.heard = hand_over(scene_line(HEARD, time=now, heard_by=[HULDA]), scene_line(HEARD, time=now + 30, heard_by=[HULDA]))
    game.say("Did you hear that?")
    assert model.npc_requests[-1].count(HEARD) == 1
    game.end()
    assert kept(game.manager, "Hulda", "01A66E") == [HEARD]


def test_the_same_words_from_someone_else_or_after_another_pick_are_given_again(game: HeardLinesGame, model: FakeModel):
    """Only the same speaker saying the same words, and for menu talk after the same pick, is a repeat"""
    game.start(HULDA_PERSON)
    now = int(time.time())
    game.heard = hand_over(scene_line("Hmph.", time=now, heard_by=[HULDA]),
                           menu_line("Any rumors?", "Not today.", speaker=SAADIA, heard_by=[SAADIA, HULDA], time=now + 1))
    game.say("Did you hear that?")
    game.heard = hand_over(scene_line("Hmph.", speaker=CARLOTTA, to=MIKAEL, time=now + 2, heard_by=[HULDA]),
                           menu_line("Any work?", "Not today.", speaker=SAADIA, heard_by=[SAADIA, HULDA], time=now + 3),
                           menu_line("Any rumors?", "Not today.", speaker=SAADIA, heard_by=[SAADIA, HULDA], time=now + 4))
    game.say("And again.")
    request = model.npc_requests[-1]
    assert 'Nearby, Mikael says to Carlotta: "Hmph."' in request and 'Nearby, Carlotta says to Mikael: "Hmph."' in request
    assert 'Kit asks Saadia: "Any work?" Saadia answers: "Not today."' in request
    assert request.count('Kit asks Saadia: "Any rumors?"') == 1 # Only from the earlier message


@pytest.mark.parametrize("summaries_on", [True, False])
def test_a_short_conversation_still_remembers_what_was_heard(game: HeardLinesGame, summaries_on: bool):
    """No summary is written for a short conversation, or with summaries off: the lines are kept anyway"""
    game.config.conversation_summary_enabled = summaries_on
    game.start(HULDA_PERSON)
    game.heard = hand_over(scene_line(HEARD, time=int(time.time()), heard_by=[HULDA]))
    game.say("Did you hear that?")
    game.end()
    assert kept(game.manager, "Hulda", "01A66E") == [HEARD]


def test_lines_caught_before_the_start_become_memory(game: HeardLinesGame, model: FakeModel):
    game.heard = hand_over(scene_line(HEARD, time=int(time.time()) - 60, heard_by=[HULDA]))
    game.start(HULDA_PERSON)
    assert kept(game.manager, "Hulda", "01A66E") == [HEARD]
    game.say("Hello.") # Automatic Greeting is off at first, so her first reply is to the player
    assert "Hulda overheard Mikael say to Carlotta" in model.npc_requests[0] # In her memory
    assert "Nearby, Mikael says" not in model.npc_requests[0]


def test_lines_still_waiting_when_the_conversation_ends_are_kept(game: HeardLinesGame, model: FakeModel):
    game.start(HULDA_PERSON)
    game.at_line[len(game.lines) + 1] = lambda: setattr(game, "heard", scene(2, heard_by=[HULDA, SAADIA])) # Arrives with a continue request
    game.say("Hello.")
    assert game.heard is None # Handed over
    assert kept(game.manager, "Saadia", "0D7505") == ["Line 0.", "Line 1."] # Not in the conversation
    assert kept(game.manager, "Hulda", "01A66E") == []
    game.end()
    assert kept(game.manager, "Hulda", "01A66E") == ["Line 0.", "Line 1."] # Never went with a message, kept all the same


def test_a_request_turned_away_still_keeps_its_lines_in_the_last_playthrough(game: HeardLinesGame):
    game.start(HULDA_PERSON)
    game.end()
    reply = game.manager.continue_conversation({comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_CONTINUECONVERSATION,
                                                comm_consts.KEY_HEARDLINES: hand_over(scene_line(HEARD, heard_by=[SAADIA]))})
    assert reply[comm_consts.KEY_REPLYTYPE] == "error" # No running conversation, and the request names no world
    assert kept(game.manager, "Saadia", "0D7505") == [HEARD]
    assert kept(game.manager, "Saadia", "0D7505", world="default") == []


@pytest.mark.parametrize("check", ["npc_initiative_check", "npc_event_check"])
def test_the_npc_checks_keep_their_lines(manager: GameStateManager, check: str):
    getattr(manager, check)({comm_consts.KEY_STARTCONVERSATION_WORLDID: WORLD, comm_consts.KEY_ACTORS: [],
                             comm_consts.KEY_HEARDLINES: hand_over(scene_line(HEARD, heard_by=[SAADIA]))})
    assert kept(manager, "Saadia", "0D7505") == [HEARD]
