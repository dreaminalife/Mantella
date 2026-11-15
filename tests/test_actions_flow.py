"""Whole conversations with the basic actions, on the pretend game and models of tests/test_conversation_events_flow.py: the NPC starts its
line with an action's name ("Wait: As you wish") and the test checks what Mantella sends the game. The models answer with set lines, so each
action comes on cue. What the game then does with an action is checked in the game (tests/ingame/actions/actions_ingame.py)"""
from typing import Any

import pytest

from src.config.config_loader import ConfigLoader
from src.config.definitions.action_definitions import ACTION_KINDS, actions_key
from src.game_manager import GameStateManager
from src.http.communication_constants import communication_constants as comm_consts
from tests.test_conversation_events_flow import (HULDA, MIKAEL, NPC_LINE, PLAYER, YSOLDA, FakeGame, FakeModel, manager, # noqa: F401 (fixtures)
                                                 shout_state)

WAIT = "mantella_npc_wait"
MOVETO = "mantella_npc_moveto"
FOLLOW = "mantella_npc_follow"
LISTEN = "mantella_npc_listen"
BARTER = "mantella_npc_barter"


class ScriptedModel(FakeModel):
    """FakeModel, whose NPC replies are the set lines, in order. Without one: an ordinary line"""
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    async def streaming_call(self, messages, is_multi_npc: bool, response_started_at: float | None = None):
        self.npc_requests.append(str(messages.get_openai_messages()))
        yield self.lines.pop(0) if self.lines else NPC_LINE


class ActionGame(FakeGame):
    """FakeGame that also keeps the actions Mantella sends with each line, and the actions it asks for when the player types an action's name"""
    def __init__(self, manager: GameStateManager, around=None):
        super().__init__(manager, around)
        self.sent: list[tuple[str, str, list[str]]] = [] # (speaker, line, action identifiers)
        self.typed_actions: list[list[str]] = []

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        reply = super().send(request)
        reply_type = reply.get(comm_consts.KEY_REPLYTYPE)
        if reply_type == comm_consts.KEY_REPLYTYPE_NPCTALK:
            talk = reply.get(comm_consts.KEY_REPLYTYPE_NPCTALK)
            if talk and (talk.get(comm_consts.KEY_ACTOR_LINETOSPEAK) or talk.get(comm_consts.KEY_ACTOR_ACTIONS)): # A line, or only an action
                identifiers = [a["identifier"] for a in talk.get(comm_consts.KEY_ACTOR_ACTIONS) or []]
                self.sent.append((talk[comm_consts.KEY_ACTOR_SPEAKER], talk[comm_consts.KEY_ACTOR_LINETOSPEAK].strip(), identifiers))
        elif reply_type == comm_consts.KEY_REPLYTYPE_NPCACTION:
            # MantellaConversation.ProcessNpcAction: the game does the action, then carries on
            self.typed_actions.append([a["identifier"] for a in reply[comm_consts.KEY_REPLYTYPE_NPCACTION]["mantella_actor_actions"]])
            self.is_player_turn = True
            return {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_PLAYERTALK}
        return reply

    def actions_of(self, speaker: str) -> list[str]:
        return [identifier for who, _line, identifiers in self.sent if who == speaker for identifier in identifiers]

    def lines_of(self, speaker: str) -> list[str]:
        return [line for who, line, _identifiers in self.sent if who == speaker]


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
    with caplog.at_level(23):
        game.say("Let me think about it.")
    assert game.sent[-1] == (HULDA.name, "Take your time.", [])
    assert "Listen: the player's next turn waits" in caplog.text


def test_saying_listen_gives_more_time_and_the_npc_still_hears_it(game: ActionGame, model: ScriptedModel, caplog):
    game.start(HULDA)
    with caplog.at_level(23):
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
