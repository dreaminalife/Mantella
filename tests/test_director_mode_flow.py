"""End-to-end tests of Director Mode on the Mantella side: whole conversations, with the requests the game sends and the replies it reads,
and made-up models. They cover the checks that were otherwise done by hand in the game, each with Director Mode on and, where it matters, off.

Uses the fake game and model of tests/test_conversation_events_flow.py. DirectorGame adds what the game also sends with the player's input:
the in-game events the listeners queued, the vision hint values and the player's combat state. As in MantellaConversation.psc, continue
requests carry none of these: what happens while the NPCs talk reaches Mantella with the player's next input.
What only the game can show (who turns to face whom, what is on screen) still needs a look in the game.
"""
import logging
from typing import Any

import pytest

from src.config.config_loader import ConfigLoader
from src.game_manager import GameStateManager
from src.http.communication_constants import communication_constants as comm_consts
from src.llm.message_thread import message_thread
from src.config.definitions.conversation_definitions import MAX_NPC_TURNS_KINDS, max_npc_turns_identifier
from tests.test_conversation_events_flow import (FakeGame, FakeModel, HULDA, MIKAEL, OLFINA, PLAYER, SAADIA, YSOLDA, Person, in_conversation,
                                                 manager, shout_state) # noqa: F401 (manager is a fixture)

SUMMARY = "They talked about the day."


class RecordingModel(FakeModel):
    """FakeModel that also keeps the prompt each memory was written with, and can give the NPCs other lines"""

    def __init__(self):
        super().__init__()
        self.summary_prompts: list[str] = []
        self.npc_lines: list[str] = [] # The NPCs' next lines, in order. Without one: the usual line

    async def streaming_call(self, messages: message_thread, is_multi_npc: bool, response_started_at: float | None = None):
        if not self.npc_lines:
            async for text in super().streaming_call(messages, is_multi_npc, response_started_at):
                yield text
            return
        self.npc_requests.append(str(messages.get_openai_messages()))
        yield self.npc_lines.pop(0)

    def request_call(self, messages: message_thread, topic_log=None) -> str:
        reply = super().request_call(messages)
        if reply == SUMMARY:
            self.summary_prompts.append(str(messages.get_openai_messages()[0]["content"]))
        return reply


class DirectorGame(FakeGame):
    """FakeGame that also sends what BuildContext and the listeners add to the player's input"""

    def __init__(self, manager: GameStateManager, around: list[Person] | None = None):
        super().__init__(manager, around)
        self.next_events: list[str] = [] # AddIngameEvent: sent with the next request that carries the context, then cleared
        self.vision: tuple[str, str] | None = None # The vision hint names and distances, as the game's custom context values
        self.player_in_combat = False
        self.voice_player = False # The MCM's Voice Player Input
        self.relationship_rank: dict[str, int] = {} # By name: the NPC's relationship rank with the player, when it isn't 0
        self.actions: list[str] = [] # The identifiers of the actions the NPCs' lines asked the game to do

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        reply = super().send(request)
        talk = reply.get(comm_consts.KEY_REPLYTYPE_NPCTALK) if reply.get(comm_consts.KEY_REPLYTYPE) == comm_consts.KEY_REPLYTYPE_NPCTALK else None
        if talk:
            self.actions += [action["identifier"] for action in talk.get(comm_consts.KEY_ACTOR_ACTIONS) or []]
        return reply

    def context(self) -> dict[str, Any]:
        values = FakeGame.context()
        values[comm_consts.KEY_CONTEXT_INGAMEEVENTS], self.next_events = list(self.next_events), []
        if self.vision:
            values[comm_consts.KEY_CONTEXT_CUSTOMVALUES] = {comm_consts.KEY_CONTEXT_CUSTOMVALUES_VISION_HINTSNAMEARRAY: self.vision[0],
                                                            comm_consts.KEY_CONTEXT_CUSTOMVALUES_VISION_HINTSDISTANCEARRAY: self.vision[1]}
        return values

    def actors(self) -> list[dict[str, Any]]:
        actors = super().actors()
        for actor in actors:
            if actor[comm_consts.KEY_ACTOR_ISPLAYER]:
                actor[comm_consts.KEY_ACTOR_ISINCOMBAT] = self.player_in_combat
                actor[comm_consts.KEY_ACTOR_CUSTOMVALUES][comm_consts.KEY_ACTOR_PC_VOICEPLAYERINPUT] = self.voice_player
            else:
                actor[comm_consts.KEY_ACTOR_RELATIONSHIPRANK] = self.relationship_rank.get(actor[comm_consts.KEY_ACTOR_NAME], 0)
        return actors

    def start_npc_initiative(self, npc: Person):
        """An NPC walks up to the player (NPC Initiative): the start request says so"""
        self.participants = [PLAYER, npc]
        self.is_running = True
        self.handle(self.send({comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_STARTCONVERSATION,
                               comm_consts.KEY_STARTCONVERSATION_WORLDID: PLAYER.name + "Test1", comm_consts.KEY_ACTORS: self.actors(),
                               comm_consts.KEY_CONTEXT: self.context(), comm_consts.KEY_INPUTTYPE: self.input_type,
                               comm_consts.KEY_STARTCONVERSATION_NPCINITIATIVE: True}))


@pytest.fixture
def model() -> RecordingModel:
    return RecordingModel()


@pytest.fixture
def game(manager: GameStateManager) -> DirectorGame:
    return DirectorGame(manager, [YSOLDA, SAADIA, OLFINA])


@pytest.fixture
def director(default_config: ConfigLoader) -> ConfigLoader:
    default_config.multi_conversation_director_mode = True
    return default_config


def is_directed(game: FakeGame) -> bool:
    return game.manager._GameStateManager__talk.is_directed()


def player_was_offered(model: FakeModel, since: int = 0) -> bool:
    return any(PLAYER.name in choices for choices in model.game_master_choices[since:])


def plain(request: str) -> str:
    """A request as text, with the quotes Python escapes when printing it put back"""
    return request.replace("\\'", "'")


def direction(text: str) -> str:
    """How a direction reaches the NPCs: narration, without the player's name"""
    return f"({text})"


# Where Director Mode applies

def test_one_on_one_the_npc_answers_each_direction_once(director, game: DirectorGame, model: RecordingModel):
    game.start(HULDA)
    assert game.lines == [] # No greeting: the player directs
    game.say("Hulda sharpens her sword.")

    assert is_directed(game)
    assert direction("Hulda sharpens her sword.") in model.npc_requests[-1]
    assert f"{PLAYER.name}: Hulda sharpens" not in model.npc_requests[-1]
    assert game.speakers() == [HULDA.name] and game.is_player_turn # Once, then the player directs again
    assert not player_was_offered(model)


def test_the_npc_is_told_the_player_is_not_around_and_that_directions_are_in_brackets(director, game: DirectorGame, model: RecordingModel):
    game.start(HULDA, MIKAEL)
    game.say("A cold wind blows through the hall.")
    assert f"{PLAYER.name} is not around and doesn't participate in the conversation" in plain(model.npc_requests[-1])
    assert "Scene directions and events are shown in brackets like this: (A door slams shut)" in model.npc_requests[-1]
    assert "(The player picks up an item)" not in model.npc_requests[-1]


@pytest.mark.parametrize("director_mode", [True, False])
def test_the_game_master_never_reads_the_players_name_while_directing(default_config: ConfigLoader, game: DirectorGame, model: RecordingModel,
                                                                      director_mode):
    default_config.multi_conversation_director_mode = director_mode
    game.start(HULDA, MIKAEL)
    asked = len(model.game_master_requests)
    model.picks = [HULDA.name]
    game.say("A bard starts to play." if director_mode else "Play us something, bard.")
    assert any(PLAYER.name in request for request in model.game_master_requests[asked:]) is not director_mode


def test_one_npc_you_direct_gets_the_director_mode_actions(director, game: DirectorGame, model: RecordingModel):
    """"Make the NPC do stuff": a direction can make the NPC wait, but not follow the player, who the NPC doesn't know is there"""
    director.actions_director = ["Follow", "Wait"]
    game.start(HULDA)
    model.npc_lines = ["Follow: Very well, I'll come along.", "Wait: I'll stay right here."]
    game.say("Hulda decides to follow the Dragonborn.")
    assert "begin your response with 'Follow:'" not in plain(model.npc_requests[-1])
    assert "begin your response with 'Wait:'" in plain(model.npc_requests[-1])
    game.say("Hulda decides to stay.")
    assert "mantella_npc_follow" not in game.actions and "mantella_npc_wait" in game.actions


def test_a_group_you_direct_gets_the_director_mode_actions(director, game: DirectorGame, model: RecordingModel):
    director.actions_director = ["Wait"]
    director.actions_group = ["MoveTo"]
    game.start(HULDA, MIKAEL)
    model.picks = [HULDA.name]
    game.say("Hulda decides to follow the Dragonborn.")
    assert "begin your response with 'Wait:'" in plain(model.npc_requests[-1])
    assert "begin your response with 'MoveTo:'" not in plain(model.npc_requests[-1])


@pytest.mark.parametrize("director_mode", [True, False])
def test_what_the_player_types_is_voiced_only_without_director_mode(default_config: ConfigLoader, game: DirectorGame, model: RecordingModel,
                                                                    director_mode):
    default_config.multi_conversation_director_mode = director_mode
    game.voice_player = True
    game.start(HULDA, MIKAEL)
    spoken = len(game.lines)
    model.picks = [HULDA.name]
    game.say("A raven lands on the table." if director_mode else "Look, a raven.")
    assert (PLAYER.name in game.speakers(spoken)) is not director_mode # A direction is never said aloud


@pytest.mark.parametrize("director_mode", [True, False])
def test_interrupting_the_npcs_is_an_event_only_without_director_mode(default_config: ConfigLoader, game: DirectorGame, model: RecordingModel,
                                                                      director_mode):
    default_config.multi_conversation_director_mode = director_mode
    default_config.multi_npc_game_master_director_max_npc_turns = 3
    for each_kind in MAX_NPC_TURNS_KINDS: # Each kind of conversation has its own
        setattr(default_config, max_npc_turns_identifier(each_kind), 3)
    game.start(HULDA, MIKAEL)
    spoken = len(game.lines)
    model.picks = [HULDA.name, MIKAEL.name, HULDA.name]
    game.at_line[spoken + 1] = game.interrupt # The interrupt hotkey while the NPCs talk
    game.say("The two of them argue about the price of mead." if director_mode else "So, how is the mead this year?")
    assert game.is_player_turn
    replied = len(model.npc_requests)
    model.picks = [MIKAEL.name]
    game.say("A guard walks in." if director_mode else "Never mind.")
    assert ("Interrupting..." in model.npc_requests[replied]) is not director_mode # Nobody interrupts: the player isn't there


def test_one_on_one_without_director_mode_the_player_speaks(game: DirectorGame, model: RecordingModel):
    game.start(HULDA)
    game.say("Hello Hulda.")

    assert not is_directed(game)
    assert "Hello Hulda." in model.npc_requests[-1] and direction("Hello Hulda.") not in model.npc_requests[-1]


def test_group_the_npcs_talk_up_to_the_director_turn_limit_without_the_player(director, game: DirectorGame, model: RecordingModel):
    director.multi_npc_game_master_director_max_npc_turns = 3
    game.start(HULDA, MIKAEL)
    model.picks = [HULDA.name, MIKAEL.name, HULDA.name]
    game.say("A courier bursts in with news of a dragon.")

    assert direction("A courier bursts in with news of a dragon.") in model.npc_requests[0]
    assert game.speakers() == [HULDA.name, MIKAEL.name, HULDA.name] and game.is_player_turn
    assert not player_was_offered(model)
    model.picks = [MIKAEL.name, HULDA.name, MIKAEL.name]
    game.say(".") # The scene goes on without a new direction
    assert game.speakers(3) == [MIKAEL.name, HULDA.name, MIKAEL.name]


def test_saying_something_to_the_room_is_a_direction_someone_nearby_reacts_to(director, game: DirectorGame, model: RecordingModel):
    model.picks = [YSOLDA.name]
    game.say_to_room("A thief snatches an apple from the stall.")

    assert is_directed(game)
    assert game.joined == [YSOLDA] and game.speakers() == [YSOLDA.name]
    assert direction("A thief snatches an apple from the stall.") in model.npc_requests[-1]
    assert "Nobody" in model.game_master_choices[0] and not player_was_offered(model)
    assert "that person is named in what happened" in model.game_master_requests[0] # Asked who is part of it, as after an event


def test_shout_with_one_npc_then_private_stays_directed(director, game: DirectorGame, model: RecordingModel):
    game.start(HULDA)
    game.set_shout(True)
    game.say("Rain starts to pour.")
    assert is_directed(game) and game.speakers() == [HULDA.name] # Answers once, nobody nearby is made to join
    game.set_shout(False)
    game.say("Thunder cracks overhead.")

    assert is_directed(game) and game.speakers(1) == [HULDA.name]
    assert direction("Thunder cracks overhead.") in model.npc_requests[-1]
    assert "Nobody else joins the conversation now." in model.npc_requests[-1]
    assert "speaking privately" not in model.npc_requests[-1] # The player isn't named
    assert not player_was_offered(model)


def test_a_conversation_an_npc_started_is_never_directed(director, game: DirectorGame, model: RecordingModel):
    game.start_npc_initiative(HULDA)
    game.say("Hello there.")
    assert not is_directed(game)
    assert direction("Hello there.") not in model.npc_requests[-1]
    game.set_shout(True)
    game.say("Anyone else want to talk?")
    assert not is_directed(game)
    assert direction("Anyone else want to talk?") not in model.npc_requests[-1]


def test_a_nearby_event_keeps_a_group_directed(director, game: DirectorGame, model: RecordingModel):
    game.start(HULDA, MIKAEL)
    model.picks = [YSOLDA.name]
    game.pick_event("nearby")
    assert game.notes[-1] == "Something happened" and is_directed(game)
    assert not player_was_offered(model)
    assert "There is a conversation in Whiterun" in model.event_requests[-1] and f"{PLAYER.name} is in a conversation" not in model.event_requests[-1]
    game.say("The guards arrive.")
    assert direction("The guards arrive.") in model.npc_requests[-1]


# The player stays out of the scene

@pytest.mark.parametrize("director_mode", [True, False])
def test_events_about_the_player_only_reach_the_npcs_without_director_mode(default_config: ConfigLoader, game: DirectorGame, model: RecordingModel,
                                                                          director_mode):
    default_config.multi_conversation_director_mode = director_mode
    default_config.max_count_events = 20 # Room for all of them: only the latest events are kept
    game.start(HULDA, MIKAEL)
    model.picks = [HULDA.name]
    game.next_events = [f"{PLAYER.name} picked up / took Iron Sword", "The player took damage.", "Hulda drew her dagger."]
    game.player_in_combat = True
    game.vision = ("[Hulda, Mikael]", "[100.0, 200.0]")
    game.relationship_rank[HULDA.name] = 3
    replied = len(model.npc_requests) # Without Director Mode, the NPCs may have greeted the player
    game.say("A storm rolls in.")

    request = model.npc_requests[replied]
    about_the_player = ["took Iron Sword", "The player took damage.", "The player is now in combat!", "Characters currently in view",
                        f"{PLAYER.name} is now "] # The last: how much Hulda trusts the player
    assert [text for text in about_the_player if text in request] == ([] if director_mode else about_the_player)
    assert "Hulda drew her dagger." in request # Not about the player: always kept


@pytest.mark.parametrize("director_mode", [True, False])
def test_the_start_request_gives_who_the_player_can_see_only_without_director_mode(default_config: ConfigLoader, game: DirectorGame,
                                                                                  model: RecordingModel, director_mode):
    """The start request has the context too, handled before the conversation becomes directed"""
    default_config.multi_conversation_director_mode = director_mode
    game.vision = ("[Hulda]", "[100.0]")
    game.start(HULDA)
    game.vision = None
    game.say("Hulda looks up from her ledger.")

    assert any("Characters currently in view" in request for request in model.npc_requests) is not director_mode


@pytest.mark.parametrize("director_mode", [True, False])
def test_an_npc_who_attacks_the_player_is_reported_only_without_director_mode(default_config: ConfigLoader, game: DirectorGame,
                                                                             model: RecordingModel, director_mode):
    default_config.multi_conversation_director_mode = director_mode
    game.start(HULDA, MIKAEL)
    game.say("Night falls.")
    original = game.actors
    def hulda_attacks():
        actors = original()
        next(a for a in actors if a[comm_consts.KEY_ACTOR_NAME] == HULDA.name)[comm_consts.KEY_ACTOR_ISENEMY] = True
        return actors
    game.actors = hulda_attacks
    replied = len(model.npc_requests)
    game.say("Hulda's eyes go cold.")
    assert ("Hulda is attacking Prisoner" in model.npc_requests[replied]) is not director_mode


@pytest.mark.parametrize("director_mode", [True, False])
def test_most_people_in_conversation_only_counts_the_player_without_director_mode(default_config: ConfigLoader, game: DirectorGame,
                                                                                  model: RecordingModel, director_mode):
    default_config.multi_conversation_director_mode = director_mode
    game.config.shout_max_people = 3
    game.start(HULDA, MIKAEL)
    game.set_shout(True)
    asked = len(model.game_master_choices)
    model.picks = [HULDA.name, MIKAEL.name]
    game.say("Hulda waves at the bard." if director_mode else "Hulda, wave at the bard.")

    after_hulda = model.game_master_choices[asked + 1] # Who may speak after Hulda's line: no direction or line of the player's to react to
    assert (YSOLDA.name in after_hulda) is director_mode # Two NPCs and the player fill three places, unless the player only directs


@pytest.mark.parametrize("director_mode", [True, False])
def test_a_direction_brings_in_the_people_it_is_about_past_most_people(default_config: ConfigLoader, game: DirectorGame, model: RecordingModel,
                                                                       director_mode):
    default_config.multi_conversation_director_mode = director_mode
    game.config.shout_max_people = 2 # Full with Hulda and Mikael alone
    game.start(HULDA, MIKAEL)
    game.set_shout(True)
    asked = len(model.game_master_choices)
    if director_mode:
        model.picks, model.also = [YSOLDA.name], [[SAADIA.name]]
        game.say("Ysolda and Saadia start a loud argument at the door.")
        assert game.joined == [YSOLDA, SAADIA]
    else:
        game.say("Ysolda, Saadia, come over here!")
        assert YSOLDA.name not in model.game_master_choices[asked] and game.joined == []


@pytest.mark.parametrize("director_mode", [True, False])
def test_a_random_event_is_about_the_player_only_without_director_mode(default_config: ConfigLoader, game: DirectorGame, model: RecordingModel,
                                                                       director_mode, caplog):
    default_config.multi_conversation_director_mode = director_mode
    game.start(HULDA, MIKAEL)
    model.picks = [HULDA.name]
    with caplog.at_level(logging.DEBUG):
        game.pick_event("random") # Include Me is on for Random Events

    about = next(line for line in caplog.text.splitlines() if "Event (random) about:" in line)
    assert (PLAYER.name in about) is not director_mode
    request = model.event_requests[-1]
    assert ("What Prisoner looks like" in request) is not director_mode
    assert ("Prisoner is not around: leave Prisoner out of the event." in request) is director_mode
    assert player_was_offered(model) is not director_mode


@pytest.mark.parametrize("director_mode", [True, False])
def test_an_npc_event_only_notices_the_player_watching_without_director_mode(default_config: ConfigLoader, game: DirectorGame,
                                                                            model: RecordingModel, director_mode):
    default_config.multi_conversation_director_mode = director_mode
    default_config.npc_event_player_notice_delay = 0
    default_config.npc_event_max_lines_without_player = 3
    game.around = [HULDA, MIKAEL, YSOLDA]
    game.player_noticeable = True # Close, in sight and not sneaking
    model.participants = [HULDA.name, MIKAEL.name]
    model.picks = [HULDA.name, MIKAEL.name, HULDA.name]
    directed_while_watching: list[bool] = []
    game.at_line[1] = lambda: directed_while_watching.append(is_directed(game))
    game.npc_event_check()

    assert directed_while_watching == [False] # Watching is never directed: the scene runs on its own
    assert player_was_offered(model) is not director_mode


@pytest.mark.parametrize("director_mode", [True, False])
def test_a_watched_npc_event_gets_who_the_player_can_see_only_without_director_mode(default_config: ConfigLoader, game: DirectorGame,
                                                                                   model: RecordingModel, director_mode):
    """The scene's start request carries the context, as every start does"""
    default_config.multi_conversation_director_mode = director_mode
    default_config.npc_event_max_lines_without_player = 1
    game.around = [HULDA, MIKAEL, YSOLDA]
    game.vision = ("[Hulda, Mikael]", "[100.0, 200.0]")
    model.participants = [HULDA.name, MIKAEL.name]
    model.picks = [HULDA.name]
    game.npc_event_check()

    assert ("Characters currently in view" in model.npc_requests[0]) is not director_mode


def test_stepping_into_an_npc_event_directs_it_and_what_the_player_did_meanwhile_stays_out(director, game: DirectorGame, model: RecordingModel):
    game.around = [HULDA, MIKAEL, YSOLDA]
    model.participants = [HULDA.name, MIKAEL.name]
    model.picks = [HULDA.name, MIKAEL.name]
    game.at_line[1] = lambda: game.next_events.append(f"{PLAYER.name} picked up / took a sweetroll") # Queued by the game while the player watches
    game.at_line[2] = game.step_in
    game.npc_event_check()
    assert game.is_player_turn and PLAYER.name in in_conversation(game.manager)

    model.picks = [MIKAEL.name]
    game.say("A guard storms in looking for a thief.")
    assert is_directed(game)
    assert direction("A guard storms in looking for a thief.") in model.npc_requests[-1]
    assert "sweetroll" not in model.npc_requests[-1]
    assert not player_was_offered(model)


# Memories

@pytest.mark.parametrize("director_mode", [True, False])
def test_the_memory_says_the_player_was_not_there_also_when_they_only_sent_continues(default_config: ConfigLoader, game: DirectorGame,
                                                                                    model: RecordingModel, director_mode):
    default_config.multi_conversation_director_mode = director_mode
    default_config.conversation_summary_enabled = True
    default_config.multi_npc_game_master_director_max_npc_turns = 3
    for each_kind in MAX_NPC_TURNS_KINDS: # Each kind of conversation has its own
        setattr(default_config, max_npc_turns_identifier(each_kind), 3)
    game.start(HULDA, MIKAEL)
    game.say(".")
    game.say(".")
    game.end()

    assert model.summary_prompts
    assert ("Prisoner was not there and took no part in it" in model.summary_prompts[0]) is director_mode
    assert ("the player (the user)" in model.summary_prompts[0]) is not director_mode
