"""Whole conversations with the actions, on the pretend game and models of tests/test_conversation_events_flow.py: the NPC starts its
line with an action's name ("Wait: As you wish"), or with Advanced Actions on the model picks actions with their details (tool calls), and
the test checks what Mantella sends the game. The models answer with set lines and picks, so each action comes on cue. What the game then
does with an action is checked in the game (tests/ingame/actions/actions_ingame.py)"""
import glob
import json
import logging
import os
import re
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.config.config_loader import ConfigLoader
from src.config.definitions.action_definitions import ACTION_KINDS, actions_key
from src.conversation.game_master import GameMasterTurns
from src.game_manager import GameStateManager
from src.http.communication_constants import communication_constants as comm_consts
from src.llm.client_base import ToolCalls
from tests.test_conversation_events_flow import (HULDA, MIKAEL, NPC_LINE, PLAYER, YSOLDA, FakeGame, FakeModel, manager, # noqa: F401 (fixtures)
                                                 shout_state)

WAIT = "mantella_npc_wait"
MOVETO = "mantella_npc_moveto"
FOLLOW = "mantella_npc_follow"
LISTEN = "mantella_npc_listen"
BARTER = "mantella_npc_barter"
DIRECTIONS = "mantella_npc_givedirections"
CAST = "mantella_npc_castspell"


def picks(*calls: tuple[str, dict[str, Any]]) -> ToolCalls:
    """What the model picks with Advanced Actions: (action name, arguments)"""
    return ToolCalls({"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
                     for i, (name, arguments) in enumerate(calls))


class ScriptedModel(FakeModel):
    """FakeModel, whose NPC replies are the set lines, in order. Without one: an ordinary line. A reply can also be the actions the model
    picks (picks(...)), or a tuple of both, in the order the model sends them. A real model sends its picks after its text"""
    def __init__(self):
        super().__init__()
        self.lines: list[Any] = []
        self.tools_sent: list[list[dict] | None] = [] # The tools sent with each NPC reply request
        self.saw_screenshot: list[bool] = [] # Whether each NPC reply request came with a screenshot (Look)
        self.own_model_picks: list[ToolCalls] = [] # Separate Model for Advanced Actions: what it picks, each time
        self.own_model_requests: list[tuple[str, list[dict]]] = []
        # As in ClientBase: one reply at a time, the lock held until the reply's stream is closed. Asked again while the last one is still
        # open, the real client waits forever and Mantella stops answering. Here the request fails instead
        self.reply_lock = threading.Lock()

    async def streaming_call(self, messages, is_multi_npc: bool, response_started_at: float | None = None, tools: list[dict] | None = None):
        if not self.reply_lock.acquire(timeout=1):
            raise AssertionError("Asked for a reply while the last one's stream was still open: the real client would wait forever")
        try:
            self.npc_requests.append(str(messages.get_openai_messages()))
            self.tools_sent.append(tools)
            self.saw_screenshot.append(getattr(self, "_image_client", None) is not None)
            line = self.lines.pop(0) if self.lines else NPC_LINE
            for part in (line if isinstance(line, tuple) else (line,)):
                yield part
        finally:
            self.reply_lock.release()

    def request_tool_calls(self, messages, tools: list[dict], topic_log=None) -> ToolCalls:
        self.own_model_requests.append((str(messages.get_openai_messages()), tools))
        return self.own_model_picks.pop(0) if self.own_model_picks else ToolCalls()


class ActionGame(FakeGame):
    """FakeGame that also keeps the actions Mantella sends with each line, and the actions it asks for when the player types an action's name.
    An action that answers back gets the answer set in answers, as an in-game event with the next continue request"""
    def __init__(self, manager: GameStateManager, around=None):
        super().__init__(manager, around)
        self.sent: list[tuple[str, str, list[str]]] = [] # (speaker, line, action identifiers)
        self.typed_actions: list[list[str]] = []
        self.npc_actions: list[dict[str, Any]] = [] # The actions sent without a line, with their arguments
        self.answers: dict[str, str] = {} # By action identifier: what the game answers
        self.nearby_actors: list[dict[str, Any]] = [] # Sent as mantella_nearby_actors with each continue request
        self.line_seconds = 0.0 # How long each line takes to play before the game asks for the next one
        self.joins: dict[str, Any] = {} # By action identifier: who the action brings into the conversation, once

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        reply = super().send(request)
        reply_type = reply.get(comm_consts.KEY_REPLYTYPE)
        if reply_type == comm_consts.KEY_REPLYTYPE_NPCTALK:
            talk = reply.get(comm_consts.KEY_REPLYTYPE_NPCTALK)
            if talk and (talk.get(comm_consts.KEY_ACTOR_LINETOSPEAK) or talk.get(comm_consts.KEY_ACTOR_ACTIONS)): # A line, or only an action
                identifiers = [a["identifier"] for a in talk.get(comm_consts.KEY_ACTOR_ACTIONS) or []]
                self.sent.append((talk[comm_consts.KEY_ACTOR_SPEAKER], talk[comm_consts.KEY_ACTOR_LINETOSPEAK].strip(), identifiers))
                if self.line_seconds and talk.get(comm_consts.KEY_ACTOR_LINETOSPEAK, "").strip():
                    time.sleep(self.line_seconds) # The line plays
        elif reply_type == comm_consts.KEY_REPLYTYPE_NPCACTION:
            action = reply[comm_consts.KEY_REPLYTYPE_NPCACTION]
            identifiers = [a["identifier"] for a in action[comm_consts.KEY_ACTOR_ACTIONS]]
            if request[comm_consts.KEY_REQUESTTYPE] == comm_consts.KEY_REQUESTTYPE_PLAYERINPUT:
                # The player typed an action's name. MantellaConversation.ProcessNpcAction: the game does the action, then carries on
                self.typed_actions.append(identifiers)
                self.is_player_turn = True
                return {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_PLAYERTALK}
            # An NPC's actions without a line: ProcessNpcAction, then RequestContinueConversation, with the answer if one answers back
            self.sent.append((action[comm_consts.KEY_ACTOR_SPEAKER], "", identifiers))
            self.npc_actions.extend(action[comm_consts.KEY_ACTOR_ACTIONS])
            for identifier in identifiers:
                joiner = self.joins.pop(identifier, None)
                if joiner and joiner not in self.participants: # The action calls someone over, e.g. ReportCrime a guard
                    self.participants.append(joiner)
                    self.actors_updated = True
            next_request = self.continue_request()
            if action.get(comm_consts.KEY_ACTOR_ACTIONS_REQUIRE_RESPONSE):
                next_request[comm_consts.KEY_CONTEXT] = {**self.context(),
                                                         comm_consts.KEY_CONTEXT_INGAMEEVENTS: [self.answers[i] for i in identifiers if i in self.answers]}
            return self.send(next_request)
        return reply

    def continue_request(self) -> dict[str, Any]:
        request = super().continue_request()
        if self.nearby_actors:
            request[comm_consts.KEY_CONTEXT] = {**request.get(comm_consts.KEY_CONTEXT, {}), comm_consts.KEY_CONTEXT_NPCS_NEARBY: self.nearby_actors}
        return request

    def context(self) -> dict[str, Any]:
        return {**FakeGame.context(), comm_consts.KEY_CONTEXT_NPCS_NEARBY: self.nearby_actors} # mantella_nearby_actors

    def actions_of(self, speaker: str) -> list[str]:
        return [identifier for who, _line, identifiers in self.sent if who == speaker for identifier in identifiers]

    def lines_of(self, speaker: str) -> list[str]:
        return [line for who, line, _identifiers in self.sent if who == speaker and line] # Not the actions sent without a line


def offers(request: str, keyword: str) -> bool:
    """Whether the prompt of a request tells the NPC how to use the action"""
    return f"begin your response with '{keyword}:'" in request.replace("\\'", "'") # The request is written with its quotes escaped


@pytest.fixture
def model() -> ScriptedModel:
    return ScriptedModel()


@pytest.fixture
def game(manager: GameStateManager, default_config: ConfigLoader) -> ActionGame:
    default_config.automatic_greeting = False
    for kind in ACTION_KINDS: # Each test switches on what it needs
        setattr(default_config, actions_key(kind), ["Wait", "MoveTo", "Follow", "StandDown", "Listen", "Barter"])
    return ActionGame(manager, [YSOLDA])


def test_one_on_one_the_npc_waits_and_doesnt_say_the_actions_name(game: ActionGame, model: ScriptedModel):
    game.start(HULDA)
    model.lines = ["Wait: As you wish."]
    game.say("Stay here.")
    assert game.sent[-1] == (HULDA.name, "As you wish.", [WAIT])
    assert offers(model.npc_requests[-1], "Wait")


def test_an_action_switched_off_for_the_kind_of_conversation_isnt_sent_and_its_name_isnt_said(game: ActionGame, model: ScriptedModel, default_config: ConfigLoader):
    default_config.actions_one_on_one = ["MoveTo"]
    game.start(HULDA)
    model.lines = ["Wait: As you wish."]
    game.say("Stay here.")
    assert game.sent[-1] == (HULDA.name, "As you wish.", [])
    assert not offers(model.npc_requests[-1], "Wait") and offers(model.npc_requests[-1], "MoveTo")


def test_in_a_group_each_npc_does_only_their_own_action(game: ActionGame, model: ScriptedModel):
    game.start(HULDA, MIKAEL)
    model.picks = [HULDA.name, MIKAEL.name]
    model.lines = ["Wait: As you wish.", "MoveTo: I'm coming over."]
    game.say("Stay here, Hulda. Mikael, come here.")
    assert game.actions_of(HULDA.name) == [WAIT]
    assert game.actions_of(MIKAEL.name) == [MOVETO]
    assert "CharacterName: Action:" in model.npc_requests[-1] # Each line starts with the speaker's name in a group


def test_in_director_mode_npcs_cant_use_actions_that_would_involve_the_player(game: ActionGame, model: ScriptedModel, default_config: ConfigLoader):
    default_config.multi_conversation_director_mode = True
    game.start(HULDA, MIKAEL)
    model.picks = [HULDA.name, MIKAEL.name]
    model.lines = ["Follow: Let's go.", "Wait: I'll stay."]
    game.say("The two of them get ready to leave.")
    assert game.actions_of(HULDA.name) == [] and game.lines_of(HULDA.name)[0] == "Let's go."
    assert game.actions_of(MIKAEL.name) == [WAIT]
    assert not offers(model.npc_requests[0], "Follow") and offers(model.npc_requests[0], "Wait")


def test_watching_an_npc_event_the_npcs_get_the_short_list(game: ActionGame, model: ScriptedModel, default_config: ConfigLoader):
    default_config.npc_event_max_lines_without_player = 2
    game.around = [HULDA, MIKAEL, YSOLDA]
    model.participants = [HULDA.name, MIKAEL.name]
    model.picks = [HULDA.name, MIKAEL.name]
    model.lines = ["MoveTo: Over here.", "Wait: I'll stay right here."]
    game.npc_event_check()
    assert game.actions_of(HULDA.name) == [] # MoveTo would walk her to the player, who the NPCs don't know is there
    assert game.actions_of(MIKAEL.name) == [WAIT]


def test_after_stepping_into_an_npc_event_the_shout_switches_apply(game: ActionGame, model: ScriptedModel, default_config: ConfigLoader):
    default_config.actions_npc_event = ["Wait"]
    default_config.actions_shout = ["MoveTo"]
    game.around = [HULDA, MIKAEL, YSOLDA]
    model.participants = [HULDA.name, MIKAEL.name]
    model.picks = [HULDA.name, MIKAEL.name]
    game.at_line[2] = game.step_in
    game.npc_event_check()
    assert shout_state(game.manager) and game.is_player_turn
    model.picks = [HULDA.name]
    model.lines = ["MoveTo: Coming."]
    game.say("Hulda, come here.")
    assert game.actions_of(HULDA.name) == [MOVETO]


def test_an_npcs_listen_isnt_sent_to_the_game(game: ActionGame, model: ScriptedModel, caplog):
    game.start(HULDA)
    model.lines = ["Listen: Take your time."]
    with caplog.at_level(logging.DEBUG):
        game.say("Let me think about it.")
    assert game.sent[-1] == (HULDA.name, "Take your time.", [])
    assert "Listen: the player's next turn waits" in caplog.text


def test_saying_listen_gives_more_time_and_the_npc_still_hears_it(game: ActionGame, model: ScriptedModel, caplog):
    game.start(HULDA)
    with caplog.at_level(logging.DEBUG):
        game.say("Listen.")
    assert "Listen: the player's next turn waits" in caplog.text
    assert game.typed_actions == [] and game.lines_of(HULDA.name) == [NPC_LINE]


def test_typing_an_actions_name_makes_the_npc_do_it_in_a_group_too(game: ActionGame, model: ScriptedModel):
    game.start(HULDA, MIKAEL)
    game.say("Barter")
    assert game.typed_actions == [[BARTER]]


def test_typing_an_action_the_npcs_cant_use_here_is_an_ordinary_line(game: ActionGame, model: ScriptedModel, default_config: ConfigLoader):
    default_config.actions_one_on_one = ["Wait"]
    game.start(HULDA)
    game.say("Barter")
    assert game.typed_actions == [] and game.lines_of(HULDA.name) == [NPC_LINE]


def test_a_reply_that_is_only_an_actions_name_still_does_it(game: ActionGame, model: ScriptedModel):
    game.start(HULDA)
    model.lines = ["Barter:"]
    game.say("What do you sell?")
    assert game.actions_of(HULDA.name) == [BARTER]


def test_typing_an_action_whose_word_has_a_space_does_it(game: ActionGame, model: ScriptedModel, default_config: ConfigLoader):
    barter = next(a for a in default_config.all_actions if a.name == "Barter")
    barter.keyword = "Show Wares"
    game.start(HULDA)
    game.say("show wares")
    assert game.typed_actions == [[BARTER]]


# Advanced Actions: the model picks actions with their details

@pytest.fixture
def advanced(default_config: ConfigLoader) -> ConfigLoader:
    default_config.advanced_actions_enabled = True
    for kind in ACTION_KINDS:
        setattr(default_config, actions_key(kind), ["Wait", "MoveTo", "Attack", "Barter", "StandDown", "CheckDirections", "CastSpell", "AddToConversation"])
    return default_config


def tool_names(tools: list[dict] | None) -> list[str]:
    return sorted(t["function"]["name"] for t in tools or [])


def tool(tools: list[dict] | None, name: str) -> dict:
    return next(t["function"] for t in tools or [] if t["function"]["name"] == name)


def test_with_advanced_actions_off_the_model_gets_no_tools(game: ActionGame, model: ScriptedModel):
    game.start(HULDA)
    game.say("Hello.")
    assert model.tools_sent[-1] is None
    assert offers(model.npc_requests[-1], "Wait")


def test_with_advanced_actions_on_the_model_gets_the_ticked_actions_as_tools(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    game.start(HULDA)
    game.say("Hello.")
    assert tool_names(model.tools_sent[-1]) == ["AddToConversation", "Attack", "Barter", "CastSpell", "CheckDirections", "MoveTo", "StandDown", "Wait"]
    assert "Use tools to perform actions where appropriate." in model.npc_requests[-1]
    assert not offers(model.npc_requests[-1], "Wait") # The actions are no longer listed in the prompt
    assert "source" not in tool(model.tools_sent[-1], "Wait")["parameters"]["properties"] # Who does it is always the speaker


def test_whoever_the_model_names_only_the_speaker_does_it(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    game.start(HULDA)
    model.lines = [("Coming.", picks(("MoveTo", {"source": ["Mikael", "Hulda"], "target": PLAYER.name})))]
    game.say("Come here, everyone.")
    assert game.npc_actions == [{"identifier": MOVETO, "arguments": {"source": [HULDA.name], "target": PLAYER.name}}]
    assert game.lines_of(HULDA.name) == ["Coming."]


def test_when_the_model_only_picks_actions_it_is_asked_again_for_the_line(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    game.start(HULDA)
    model.lines = [picks(("Wait", {})), "I'll stay right here."]
    game.say("Stay here.")
    assert game.npc_actions == [{"identifier": WAIT, "arguments": {"source": [HULDA.name]}}]
    assert game.lines_of(HULDA.name) == ["I'll stay right here."]
    assert model.tools_sent[-1] is None # Asked again without the tools
    assert "Hulda does this now: Wait." in model.npc_requests[-1].replace("\\'", "'")


def test_when_the_models_only_action_isnt_done_it_is_asked_again_for_the_line(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    game.start(HULDA)
    model.lines = [picks(("AddToConversation", {"source": [HULDA.name]})), "I'm right here."] # Hulda is in the conversation already
    game.say("Join us.")
    assert game.npc_actions == []
    assert game.lines_of(HULDA.name) == ["I'm right here."] # Not silent
    assert model.tools_sent[-1] is None # Asked again without the tools


def test_asking_for_someone_already_in_the_conversation_is_no_mistake(caplog):
    from src.actions.function_manager import FunctionManager
    characters = MagicMock()
    characters.get_player_name.return_value = PLAYER.name
    characters.get_all_names_w_nearby.side_effect = lambda include_player=True, include_nearby=False, nearby_only=False: \
        ["Sven"] if nearby_only else [HULDA.name, PLAYER.name]
    caplog.set_level(logging.DEBUG)
    assert FunctionManager._validate_npc_names([HULDA.name], characters, nearby_only=True) == []
    FunctionManager._log_skipped("mantella_npc_addtoconversation", "source", [HULDA.name], characters, True)
    assert "not found" not in caplog.text and "Skipping action" not in caplog.text
    assert f"{HULDA.name} is already in the conversation" in caplog.text
    FunctionManager._log_skipped("mantella_npc_addtoconversation", "source", ["Nobody"], characters, True)
    assert "Skipping action" in caplog.text # Someone who isn't there at all is still a mistake


def test_an_action_that_answers_back_the_npc_goes_on_with_the_answer(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    game.answers[DIRECTIONS] = "The way to Dawnstar: three days north, through the pass."
    game.start(HULDA)
    model.lines = [picks(("CheckDirections", {"location": "Dawnstar"})), "Three days north, through the pass."]
    game.say("How do I get to Dawnstar?")
    assert [i for _, _, ids in game.sent for i in ids] == [DIRECTIONS]
    assert game.lines_of(HULDA.name) == ["Three days north, through the pass."]
    assert "three days north, through the pass" in model.npc_requests[-1] # The answer came first
    assert model.tools_sent[-1] is None # The line after can't pick another action that answers back
    assert game.is_player_turn


def test_in_a_group_the_npc_who_asked_answers_before_anyone_else_speaks(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    game.answers[DIRECTIONS] = "The way to Dawnstar: three days north, through the pass."
    game.start(HULDA, MIKAEL)
    model.picks = [HULDA.name, MIKAEL.name]
    model.lines = [picks(("CheckDirections", {"location": "Dawnstar"})), "Three days north.", "Watch the pass."]
    game.say("How do I get to Dawnstar?")
    assert [speaker for speaker, _ in game.lines] == [HULDA.name, MIKAEL.name]
    assert game.lines_of(HULDA.name) == ["Three days north."]
    assert "three days north, through the pass" in model.npc_requests[1] # Hulda's second request: with the answer
    assert "Three days north." in model.game_master_requests[1] # Who speaks after Hulda is only picked once she has answered


def test_without_an_answer_from_the_game_the_npc_still_goes_on(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    game.start(HULDA)
    model.lines = [picks(("CheckDirections", {"location": "Dawnstar"})), "Hm, I'm not sure."]
    game.say("How do I get to Dawnstar?")
    assert game.lines_of(HULDA.name) == ["Hm, I'm not sure."]
    assert "Nothing came of it." in model.npc_requests[-1]


def test_in_director_mode_no_action_can_name_the_player(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    advanced.multi_conversation_director_mode = True
    game.start(HULDA, MIKAEL)
    model.picks = [HULDA.name, MIKAEL.name]
    model.lines = [("You'll regret that.", picks(("Attack", {"target": PLAYER.name}))), ("Have at you!", picks(("Attack", {"target": HULDA.name})))]
    game.say("Hulda and Mikael start to fight.")
    tools = model.tools_sent[0]
    assert "Barter" not in tool_names(tools) # Acts on the player whoever is named
    assert PLAYER.name not in tool(tools, "Attack")["parameters"]["properties"]["target"]["description"]
    assert game.npc_actions == [{"identifier": "mantella_npc_offended", "arguments": {"source": [MIKAEL.name], "target": HULDA.name}}]


def test_add_to_conversation_calls_over_only_as_many_as_there_is_room_for(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    advanced.shout_max_people = 3
    game.nearby_actors = [{"name": YSOLDA.name, "distance": 3.0}, {"name": "Saadia", "distance": 4.0}]
    game.start(HULDA)
    model.lines = [("Ysolda, Saadia, come here!", picks(("AddToConversation", {"source": [YSOLDA.name, "Saadia"]})))]
    game.say("Call your friends.")
    assert "At most 1 can join." in tool(model.tools_sent[-1], "AddToConversation")["parameters"]["properties"]["source"]["description"]
    assert game.npc_actions == [{"identifier": "mantella_npc_addtoconversation", "arguments": {"source": [YSOLDA.name]}}]


def test_add_to_conversation_isnt_offered_when_the_conversation_is_full(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    advanced.shout_max_people = 2
    game.nearby_actors = [{"name": YSOLDA.name, "distance": 3.0}]
    game.start(HULDA)
    game.say("Hello.")
    assert "AddToConversation" not in tool_names(model.tools_sent[-1])


def test_an_action_switched_off_here_does_nothing_even_if_the_model_picks_it(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    advanced.actions_one_on_one = ["Wait"]
    game.start(HULDA)
    model.lines = [("There.", picks(("CastSpell", {"target": PLAYER.name, "spellName": "Healing"})))]
    game.say("Heal me.")
    assert game.npc_actions == []
    assert game.lines_of(HULDA.name) == ["There."]


def test_with_a_separate_model_it_picks_and_the_npcs_model_only_speaks(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader, manager: GameStateManager):
    advanced.advanced_actions_own_model = True
    chat_manager = manager._GameStateManager__chat_manager
    chat_manager._ChatManager__get_advanced_actions_client = lambda: model
    game.start(HULDA)
    model.own_model_picks = [picks(("Wait", {}))]
    game.say("Stay here.")
    assert game.npc_actions == [{"identifier": WAIT, "arguments": {"source": [HULDA.name]}}]
    assert model.tools_sent[-1] is None and game.lines_of(HULDA.name) == [NPC_LINE]
    request, tools = model.own_model_requests[-1]
    assert "decide whether Hulda does something" in request and "Wait" in tool_names(tools)
    assert "Use tools" not in model.npc_requests[-1] # The NPC's model isn't told to use tools it doesn't get
    assert "Hulda does this now: Wait." in model.npc_requests[-1].replace("\\'", "'") # But it knows what she does, so the line fits


def test_the_same_action_asked_for_again_is_done_again(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    game.start(HULDA)
    model.lines = [("Fine.", picks(("Wait", {})))]
    game.say("Stay here please.")
    model.lines = ["Ok, coming."]
    game.say("Never mind, come along.")
    model.lines = [("I'll stay again.", picks(("Wait", {})))]
    game.say("Stay here again please.")
    assert game.npc_actions == [{"identifier": WAIT, "arguments": {"source": [HULDA.name]}}] * 2


def test_the_same_action_in_two_replies_in_a_row_is_done_once(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    """The model doesn't see what it picked before, so it may pick the same again with each reply"""
    game.start(HULDA)
    model.lines = [("Have a look.", picks(("Barter", {}), ("MoveTo", {"target": PLAYER.name})))]
    game.say("What do you sell?")
    model.lines = [("Anything else?", picks(("Barter", {}), ("MoveTo", {"target": PLAYER.name})))]
    game.say("Thanks.")
    assert [a["identifier"] for a in game.npc_actions] == [BARTER, MOVETO, MOVETO] # MoveTo can always repeat
    assert game.lines_of(HULDA.name) == ["Have a look.", "Anything else?"]


def test_the_prompt_only_says_to_use_tools_when_there_are_some(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    advanced.actions_one_on_one = ["Listen"] # The player's: no tool
    game.start(HULDA)
    game.say("Hello.")
    assert model.tools_sent[-1] is None
    assert "Use tools" not in model.npc_requests[-1]


REPORT_CRIME = "mantella_npc_reportcrime"


def say_within(game: ActionGame, text: str, seconds: float):
    """Says the line, and fails if the conversation is still waiting after that long: it froze"""
    done = threading.Event()
    failure: list[BaseException] = []

    def say():
        try:
            game.say(text)
        except BaseException as e: # noqa: BLE001 (passed on to the test)
            failure.append(e)
        finally:
            done.set()

    threading.Thread(target=say, daemon=True).start()
    assert done.wait(seconds), f"The conversation froze: still waiting {seconds} seconds after the player said {text!r}"
    if failure:
        raise failure[0]


@pytest.mark.parametrize("game_master", [True, False])
@pytest.mark.parametrize("in_a_group", [False, True])
def test_an_action_that_answers_back_and_calls_someone_over_doesnt_freeze_the_conversation(game_master: bool, in_a_group: bool, game: ActionGame,
                                                                                         model: ScriptedModel, advanced: ConfigLoader):
    # ReportCrime calls a guard over while the game works out its answer: the conversation changes while the reply waits for it
    advanced.multi_npc_game_master_mode = game_master
    advanced.actions_one_on_one = advanced.actions_group = ["ReportCrime"]
    game.answers[REPORT_CRIME] = "Crime reported against the player."
    game.joins[REPORT_CRIME] = MIKAEL # The guard
    game.start(*([HULDA, YSOLDA] if in_a_group else [HULDA]))
    model.picks = [HULDA.name]
    model.lines = [("Hulda: I'm calling the guard." if in_a_group and not game_master else "I'm calling the guard.", picks(("ReportCrime", {"crime_gold": 10}))), "You heard me. Pay up."]
    say_within(game, "I stole a sweetroll.", 20)
    assert any("Crime reported against the player." in request for request in model.npc_requests[1:]) # Someone went on with the answer
    assert "Pay up." in [line for _, line in game.lines]
    assert game.is_player_turn


# Advanced Actions without Game Master Mode: one reply speaks for every NPC in a group

@pytest.fixture
def one_reply(advanced: ConfigLoader) -> ConfigLoader:
    advanced.multi_npc_game_master_mode = False
    return advanced


def test_without_game_master_mode_the_model_names_which_npc_does_it(game: ActionGame, model: ScriptedModel, one_reply: ConfigLoader):
    game.start(HULDA, MIKAEL)
    model.lines = [("Hulda: I'll wait right here. Mikael: Be careful out there.", picks(("Wait", {"source": [HULDA.name]}),
                                                                                         ("CheckDirections", {"location": "Dawnstar", "source": MIKAEL.name})))]
    game.say("Hulda, wait here. Mikael, how far is Dawnstar?")
    assert game.npc_actions == [{"identifier": WAIT, "arguments": {"source": [HULDA.name]}},
                                {"identifier": DIRECTIONS, "arguments": {"location": "Dawnstar"}}]
    assert [(speaker, ids) for speaker, line, ids in game.sent if not line] == [(HULDA.name, [WAIT]), (MIKAEL.name, [DIRECTIONS])] # Who does each
    source = tool(model.tools_sent[0], "CheckDirections")["parameters"]["properties"]["source"]["description"]
    assert HULDA.name in source and MIKAEL.name in source and PLAYER.name not in source
    assert "Mikael speaks first, with what came of it." in model.npc_requests[-1] # Whose action answered back goes on first


def test_without_game_master_mode_a_name_in_another_case_still_counts(game: ActionGame, model: ScriptedModel, one_reply: ConfigLoader):
    game.start(HULDA, MIKAEL)
    model.lines = [("Hulda: I'll wait. Mikael: Fine.", picks(("Wait", {"source": ["hulda "]})))]
    game.say("Hulda, wait here.")
    assert game.npc_actions == [{"identifier": WAIT, "arguments": {"source": [HULDA.name]}}]


def test_without_game_master_mode_naming_nobody_in_the_conversation_the_last_speaker_does_it(game: ActionGame, model: ScriptedModel, one_reply: ConfigLoader):
    game.start(HULDA, MIKAEL)
    model.lines = [("Hulda: Sure. Mikael: I'll wait.", picks(("Wait", {"source": [PLAYER.name, "Lydia"]})))]
    game.say("Someone wait here.")
    assert game.npc_actions == [{"identifier": WAIT, "arguments": {"source": [MIKAEL.name]}}]


def test_without_game_master_mode_the_separate_model_picks_for_every_npc(game: ActionGame, model: ScriptedModel, one_reply: ConfigLoader, manager: GameStateManager):
    one_reply.advanced_actions_own_model = True
    manager._GameStateManager__chat_manager._ChatManager__get_advanced_actions_client = lambda: model
    game.start(HULDA, MIKAEL)
    model.own_model_picks = [picks(("Wait", {"source": [HULDA.name]}))]
    game.say("Hulda, wait here.")
    assert game.npc_actions == [{"identifier": WAIT, "arguments": {"source": [HULDA.name]}}]
    request, _tools = model.own_model_requests[-1]
    assert "whether Hulda or Mikael does something" in request or "whether Mikael or Hulda does something" in request
    assert "Hulda does this now: Wait." in model.npc_requests[-1].replace("\\'", "'")


def test_share_conversation_is_only_remembered_once_it_reaches_the_game(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader, manager: GameStateManager):
    advanced.actions_one_on_one = ["ShareConversation"]
    game_object = MagicMock()
    game_object.resolve_npc_refid_by_name.return_value = "01A69A"
    manager._GameStateManager__chat_manager._ChatManager__game = game_object # The game the actions are read with
    game.start(HULDA)
    talk = manager._GameStateManager__talk
    model.lines = [("I'll tell her.", picks(("ShareConversation", {"recipient": YSOLDA.name})))]
    game.say("Tell Ysolda about this.")
    assert talk.context.npcs_in_conversation.get_pending_shares() == [(HULDA.name, YSOLDA.name, "01A69A")]



@pytest.mark.parametrize("summaries", [False, True])
def test_whoever_the_conversation_was_shared_with_remembers_it_when_it_ends(summaries: bool, game: ActionGame, model: ScriptedModel,
                                                                          advanced: ConfigLoader, manager: GameStateManager):
    advanced.actions_one_on_one = ["ShareConversation"]
    advanced.conversation_summary_enabled = summaries
    manager._GameStateManager__chat_manager._ChatManager__game = manager._GameStateManager__game # Finds Ysolda in the character list
    game.start(HULDA)
    for line in ["Hello.", "How is business?", "Any news from the market?"]:
        game.say(line)
    model.lines = [("I'll tell her.", picks(("ShareConversation", {"recipient": YSOLDA.name})))]
    game.say("Tell Ysolda about this.")
    game.end()
    folder = manager._GameStateManager__game.conversation_folder_path
    memories = []
    for path in glob.glob(os.path.join(folder, "*", f"{YSOLDA.name}*", "*.txt")):
        with open(path, encoding="utf-8") as f:
            memories.append(f.read())
    assert any(f"{HULDA.name} told {YSOLDA.name} about a conversation with" in memory for memory in memories), \
        f"{YSOLDA.name} doesn't remember the conversation: {memories}"

# Look

@pytest.fixture
def look(default_config: ConfigLoader, manager: GameStateManager) -> ConfigLoader:
    default_config.vision_enabled = True
    manager._GameStateManager__chat_manager._ChatManager__vision_client = MagicMock()
    for kind in ACTION_KINDS:
        setattr(default_config, actions_key(kind), ["Look", "Wait"])
    return default_config


def test_with_look_the_npc_only_gets_a_screenshot_when_it_looks(game: ActionGame, model: ScriptedModel, look: ConfigLoader):
    game.start(HULDA)
    model.lines = ["Look: Let me see.", "Ah, a dragon on the hill."]
    game.say("What's that over there?")
    assert model.saw_screenshot[-2:] == [False, True]
    assert game.lines_of(HULDA.name) == ["Ah, a dragon on the hill."] # What it wrote before looking is never said


@pytest.mark.parametrize("group", [False, True])
def test_starting_with_look_again_once_it_sees_is_not_taken_as_looking_again(group: bool, game: ActionGame, model: ScriptedModel, look: ConfigLoader):
    # Models often put "Look:" before the line they write with the screenshot too. That line is said, and nothing is asked again
    if group:
        look.multi_npc_game_master_mode = False
        game.start(HULDA, MIKAEL)
        model.lines = ["Hulda: Look: Let me see.", "Hulda: Look: Ah, a dragon on the hill."]
    else:
        game.start(HULDA)
        model.lines = ["Look: Let me see.", "Look: Ah, a dragon on the hill."]
    game.say("What's that over there?")
    assert game.lines_of(HULDA.name) == ["Ah, a dragon on the hill."]
    assert model.saw_screenshot[-2:] == [False, True]
    assert len(model.npc_requests) == 2


def test_without_look_ticked_vision_sends_a_screenshot_with_every_reply(game: ActionGame, model: ScriptedModel, look: ConfigLoader):
    look.actions_one_on_one = ["Wait"]
    game.start(HULDA)
    game.say("What's that over there?")
    assert model.saw_screenshot[-1] is True


def test_looking_in_the_middle_of_a_group_reply_goes_on_from_there(game: ActionGame, model: ScriptedModel, look: ConfigLoader):
    look.multi_npc_game_master_mode = False
    game.start(HULDA, MIKAEL)
    model.lines = ["Mikael: Be careful. Hulda: Look: Let me see.", "Hulda: Ah, a dragon on the hill."]
    game.say("What's that over there?")
    assert game.lines_of(MIKAEL.name) == ["Be careful."] # Said once
    assert game.lines_of(HULDA.name) == ["Ah, a dragon on the hill."]
    assert "Be careful." in model.npc_requests[-1] and "Hulda looks around." in model.npc_requests[-1] # Asked again knowing what was said


def test_if_vision_fails_to_start_look_does_nothing_and_the_line_is_said(game: ActionGame, model: ScriptedModel, look: ConfigLoader, manager: GameStateManager):
    manager._GameStateManager__chat_manager._ChatManager__vision_client = None
    game.start(HULDA)
    model.lines = ["Look: Let me see."]
    game.say("What's that over there?")
    assert game.lines_of(HULDA.name) == ["Let me see."]
    assert len(model.npc_requests) == 1

# A line cut short: the model sends the actions it picked after its text, so they are read even when the line stops early

LONG_SENTENCE = "I will gladly stay right here by the fire and keep an eye on the door for you. "


def test_a_line_cut_short_by_the_sentence_limit_still_does_its_actions(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    game.start(HULDA)
    model.lines = [(LONG_SENTENCE * 6, picks(("Wait", {})))]
    game.say("Stay here.")
    assert game.npc_actions == [{"identifier": WAIT, "arguments": {"source": [HULDA.name]}}]
    assert len(game.lines_of(HULDA.name)) == advanced.max_response_sentences_single # The rest of the text is still not said


def test_a_line_cut_short_as_the_npc_speaks_for_the_player_still_does_its_actions(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    game.start(HULDA)
    model.lines = [(f"Of course. {PLAYER.name}: Thank you.", picks(("Wait", {})))]
    game.say("Stay here.")
    assert game.npc_actions == [{"identifier": WAIT, "arguments": {"source": [HULDA.name]}}]
    assert game.lines_of(HULDA.name) == ["Of course."]


def test_in_game_master_mode_an_npc_speaking_for_another_still_does_its_actions(game: ActionGame, model: ScriptedModel, advanced: ConfigLoader):
    game.start(HULDA, MIKAEL)
    model.picks = [HULDA.name]
    model.lines = [(f"I'll wait here. {MIKAEL.name}: And I'll come along.", picks(("Wait", {})))]
    game.say("Hulda, wait here.")
    assert {"identifier": WAIT, "arguments": {"source": [HULDA.name]}} in game.npc_actions
    assert game.lines_of(HULDA.name) == ["I'll wait here."]
    assert "And I'll come along." not in [line for _, line in game.lines] # Mikael's line, written by Hulda's reply, is never said


def test_in_game_master_mode_the_wait_for_the_answer_starts_once_the_action_reaches_the_game(game: ActionGame, model: ScriptedModel,
                                                                                           advanced: ConfigLoader, monkeypatch):
    # The lines before the action take longer to play than the wait for the answer: it must only start once the action is sent
    monkeypatch.setattr(GameMasterTurns, "ACTION_ANSWER_TIMEOUT", 0.5)
    game.line_seconds = 0.3
    game.answers[DIRECTIONS] = "The way to Dawnstar: three days north, through the pass."
    game.start(HULDA, MIKAEL)
    model.picks = [HULDA.name, MIKAEL.name]
    model.lines = [(LONG_SENTENCE * 3, picks(("CheckDirections", {"location": "Dawnstar"}))), "Three days north.", "Watch the pass."]
    game.say("How do I get to Dawnstar?")
    assert game.lines_of(HULDA.name)[-1] == "Three days north."
    assert "three days north, through the pass" in model.npc_requests[1]


# Every action: each one the NPCs can be given reaches the game, as a basic action and as one the model picks with its details.
# Whether the game reads those details is checked in tests/test_actions_game_side.py

ACTION_FOLDER = os.path.join(os.path.dirname(__file__), "..", "data", "actions")
ACTION_FILES: dict[str, dict[str, Any]] = {}
for _path in sorted(glob.glob(os.path.join(ACTION_FOLDER, "*.json"))):
    with open(_path, encoding="utf-8") as _file:
        _definition = json.load(_file)
    ACTION_FILES[_definition["name"]] = _definition
NEVER_SENT = {"Listen", "Look", "EndConversation"} # The player's, a screenshot for the model, and never offered to NPCs
BASIC_ACTIONS = sorted(name for name, definition in ACTION_FILES.items() if definition.get("prompt") and name not in NEVER_SENT)
PICKABLE_ACTIONS = sorted(name for name in ACTION_FILES if name not in NEVER_SENT)
PYTHON_TYPES = {"array": list, "string": str, "integer": int, "boolean": bool, "number": (int, float)}
SAMPLE_VALUES = {"location": "Whiterun", "spellName": "Flames", "crime_gold": 100, "leave_now": True,
                 "recipient": YSOLDA.name} # Someone not in the conversation, in the game's character list
ADD_TO_CONVERSATION = "mantella_npc_addtoconversation" # Names who gets called over, not who does it
SHARE_CONVERSATION = "mantella_npc_shareconversation"


def sample_arguments(offered: dict, speaker: str) -> dict[str, Any]:
    """Arguments a model could pick for a tool: one of the names it lists other than the speaker, a value of its list, or a value of the
    right kind"""
    arguments: dict[str, Any] = {}
    for key, parameter in offered["parameters"].get("properties", {}).items():
        names = re.search(r"Available: (.*)$", parameter.get("description", ""))
        options = [name.strip() for name in names.group(1).split(",")] if names else []
        others = [name for name in options if name != speaker] or options
        if "enum" in parameter:
            value: Any = parameter["enum"][0]
        elif others:
            value = others[0]
        elif key in SAMPLE_VALUES:
            value = SAMPLE_VALUES[key]
        else:
            value = {"integer": 1, "boolean": True}.get(parameter.get("type", ""), "Whiterun")
        arguments[key] = [value] if parameter.get("type") == "array" else value
    return arguments


def details_sent(definition: dict[str, Any]) -> dict[str, Any]:
    """The details Mantella sends the game with the action, with their Python type: its parameters, and what it adds once it has looked a
    name up (an animation's id, an NPC's id, whether it found it), as tests/test_actions_game_side.py expects the game to read them"""
    sent: dict[str, Any] = {key: PYTHON_TYPES[parameter["type"]] for key, parameter in definition.get("parameters", {}).items()}
    for key, parameter in definition.get("parameters", {}).items():
        resolve = parameter.get("resolve_to_id")
        if resolve:
            sent[f"{key}_id"] = int if resolve == "idle" else str
            sent[f"{key}_succeeded"] = bool
    if definition["identifier"] == SHARE_CONVERSATION:
        sent["debug_message"] = str
    return sent


def use_the_real_game(manager: GameStateManager, game: ActionGame):
    """The actions are read with the game's lists (Emote's animations, ShareConversation's recipient), and someone stands nearby whom
    AddToConversation can call over"""
    manager._GameStateManager__chat_manager._ChatManager__game = manager._GameStateManager__game
    game.nearby_actors = [{"name": YSOLDA.name, "distance": 300}]


def sent_action(game: ActionGame, name: str) -> tuple[str, dict[str, Any]]:
    """Who did the action, and the details it reached the game with. Checks it reached the game once, with every detail, each of the right
    kind, and that every name looked up was found"""
    definition = ACTION_FILES[name]
    sent = [(speaker, identifiers) for speaker, line, identifiers in game.sent if not line and definition["identifier"] in identifiers]
    actions = [action for action in game.npc_actions if action["identifier"] == definition["identifier"]]
    assert len(sent) == 1 and len(actions) == 1, f"{name} did not reach the game once: {game.sent}"
    arguments = actions[0].get("arguments", {})
    for key, kind in details_sent(definition).items():
        assert key in arguments, f"{name} reached the game without its {key}: {arguments}"
        assert isinstance(arguments[key], kind), f"{name}'s {key} is {arguments[key]!r}, not a {kind}"
        if key.endswith("_succeeded"):
            assert arguments[key] is True, f"{name} couldn't look up its {key[:-len('_succeeded')]}: {arguments}"
    return sent[0][0], arguments


def test_every_action_file_is_covered():
    assert len(ACTION_FILES) >= 26, f"Only {len(ACTION_FILES)} action files found in {os.path.abspath(ACTION_FOLDER)}"
    assert set(PICKABLE_ACTIONS) | NEVER_SENT == set(ACTION_FILES)


@pytest.mark.parametrize("name", BASIC_ACTIONS)
def test_every_basic_action_an_npc_starts_its_line_with_reaches_the_game(name: str, game: ActionGame, model: ScriptedModel, default_config: ConfigLoader):
    identifier = ACTION_FILES[name]["identifier"]
    default_config.actions_one_on_one = [name]
    keyword = next(a.keyword for a in default_config.all_actions if a.identifier == identifier)
    game.start(HULDA)
    model.lines = [f"{keyword}: Fine."]
    game.say("Hello.")
    assert (HULDA.name, "Fine.", [identifier]) in game.sent, f"{name} was not sent with the line: {game.sent}"


@pytest.mark.parametrize("name", PICKABLE_ACTIONS)
def test_every_action_the_model_picks_reaches_the_game_with_its_details(name: str, game: ActionGame, model: ScriptedModel, advanced: ConfigLoader,
                                                                       manager: GameStateManager):
    advanced.actions_one_on_one = [name]
    use_the_real_game(manager, game)
    game.start(HULDA)
    game.say("Hello.")
    offered = model.tools_sent[-1]
    assert name in tool_names(offered), f"{name} is ticked but not offered to the model: {tool_names(offered)}"
    model.lines = [("Of course.", picks((name, sample_arguments(tool(offered, name), HULDA.name))))]
    game.say("Please do.")
    doer, arguments = sent_action(game, name)
    assert doer == HULDA.name
    if "source" in arguments and ACTION_FILES[name]["identifier"] != ADD_TO_CONVERSATION:
        assert arguments["source"] in (HULDA.name, [HULDA.name]), f"{name} is done by {arguments['source']}, not the speaker"


@pytest.mark.parametrize("name", PICKABLE_ACTIONS)
def test_every_action_the_separate_model_picks_reaches_the_game_with_its_details(name: str, game: ActionGame, model: ScriptedModel,
                                                                                advanced: ConfigLoader, manager: GameStateManager):
    advanced.actions_one_on_one = [name]
    advanced.advanced_actions_own_model = True
    manager._GameStateManager__chat_manager._ChatManager__get_advanced_actions_client = lambda: model
    use_the_real_game(manager, game)
    game.start(HULDA)
    game.say("Hello.")
    _request, offered = model.own_model_requests[-1]
    assert name in tool_names(offered), f"{name} is ticked but not offered to the separate model: {tool_names(offered)}"
    model.own_model_picks = [picks((name, sample_arguments(tool(offered, name), HULDA.name)))]
    game.say("Please do.")
    doer, arguments = sent_action(game, name)
    assert doer == HULDA.name
    if "source" in arguments and ACTION_FILES[name]["identifier"] != ADD_TO_CONVERSATION:
        assert arguments["source"] in (HULDA.name, [HULDA.name]), f"{name} is done by {arguments['source']}, not the speaker"
    assert all(tools is None for tools in model.tools_sent), "The NPC's own model got tools, though a separate model picks the actions"
    if not ACTION_FILES[name].get("requires_response"): # Otherwise the line comes after the game's answer, which says what came of it
        assert f"Hulda does this now: {name}" in model.npc_requests[-1].replace("\\'", "'"), "The NPC's line isn't told what it does"


@pytest.mark.parametrize("name", PICKABLE_ACTIONS)
def test_every_action_in_a_group_without_game_master_mode_is_done_by_the_npc_the_model_names(name: str, game: ActionGame, model: ScriptedModel,
                                                                                            one_reply: ConfigLoader, manager: GameStateManager):
    one_reply.actions_group = [name]
    use_the_real_game(manager, game)
    game.start(HULDA, MIKAEL)
    game.say("Hello.")
    offered = model.tools_sent[-1]
    assert name in tool_names(offered), f"{name} is ticked but not offered to the model: {tool_names(offered)}"
    # Hulda speaks last, and the model names Mikael: he does it, not the last speaker
    model.lines = [(f"{MIKAEL.name}: Of course. {HULDA.name}: Sure.", picks((name, sample_arguments(tool(offered, name), HULDA.name))))]
    game.say("Please do.")
    doer, arguments = sent_action(game, name)
    if ACTION_FILES[name]["identifier"] == ADD_TO_CONVERSATION:
        assert arguments["source"] == [YSOLDA.name] # Who gets called over
    else:
        assert doer == MIKAEL.name, f"{name} was done by {doer}, not the NPC the model named"
        if "source" in arguments:
            assert arguments["source"] in (MIKAEL.name, [MIKAEL.name]), f"{name} is done by {arguments['source']}, not the NPC the model named"


def test_every_emote_the_model_is_offered_can_be_played(manager: GameStateManager):
    skyrim = manager._GameStateManager__game
    names = skyrim.get_enabled_idle_names()
    assert names
    assert all(skyrim.resolve_idle_id(name) is not None for name in names for _ in range(20)) # A random row each time
