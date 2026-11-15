"""Which conditions of the merged prompts are true in each situation the player can be in. The situations are the ones in the
'When each condition is true' table of the prompt editor and FEATURES_README. Each conversation type passes the situation
as the player sees it: saying something to the room is never 'shout' as well, and the NPC prompt is never 'say_to_room'"""
from unittest.mock import MagicMock, patch

import pytest

from src.config.definitions.game_definitions import GameEnum
from src.conversation.bystanders import EventPerson, ShoutState
from src.conversation.conversation import Conversation
from src.conversation.conversation_type import multi_npc, multi_npc_game_master, npc_event, pc_to_npc, radiant, shout
from src.conversation.game_master import DECISION_INSTRUCTIONS, DECISION_PLAYER_CRITERIA, resolve_decision_prompts
from src.conversation.npc_event import NpcEvent, generate_conversation_event

GAME_MASTER = "skyrim_game_master_prompt"
NPC = "skyrim_game_master_npc_prompt"
PLAYER = EventPerson("Dragonborn", 20, "000014")


def config(game_master_mode: bool = True, director: bool = False, game: GameEnum = GameEnum.SKYRIM):
    cfg = MagicMock()
    cfg.multi_npc_game_master_mode = game_master_mode
    cfg.multi_conversation_director_mode = director
    cfg.game.base_game = game
    cfg.actions = []
    cfg.end_conversation_keyword = "goodbye"
    return cfg


def conversation(cfg, npcs: int, has_player: bool = True, shout_state: ShoutState | None = None, event: NpcEvent | None = None,
                 started_by_npc: bool = False) -> Conversation:
    """A conversation with the player (or not) and this many NPCs"""
    context = MagicMock()
    context.config = cfg
    context.shout = shout_state
    context.npc_event = event
    context.started_by_npc = started_by_npc
    context.npcs_in_conversation.contains_player_character.return_value = has_player
    context.npcs_in_conversation.active_character_count.return_value = npcs + (1 if has_player else 0)
    context.npcs_in_conversation.contains_multiple_npcs.return_value = npcs > 1
    context.get_characters_excluding_player.return_value = [MagicMock() for _ in range(npcs)]
    return Conversation(context, MagicMock(), MagicMock(), MagicMock(), None, False, False)


def conversation_type(talk: Conversation):
    return talk._Conversation__select_conversation_type(talk.context.config)


def conditions(talk: Conversation, prompt_type: str) -> set[str]:
    return conversation_type(talk).prompt_conditions(talk.context, prompt_type)


def scene(joined: bool = False) -> NpcEvent:
    return NpcEvent(id=7, text="Lydia and Faendal argue.", player=PLAYER, player_joined=joined)


# The rows of the table

def test_group_conversation_with_game_master_mode_has_no_condition():
    talk = conversation(config(), npcs=2)
    assert type(conversation_type(talk)) is multi_npc_game_master
    assert conditions(talk, GAME_MASTER) == set() and conditions(talk, NPC) == set()


def test_group_conversation_with_director_mode_is_director():
    talk = conversation(config(director=True), npcs=2)
    assert conditions(talk, GAME_MASTER) == {"director"} and conditions(talk, NPC) == {"director"}


@pytest.mark.parametrize("npcs", [1, 3])
@pytest.mark.parametrize("game_master_mode", [True, False])
@pytest.mark.parametrize("director", [True, False])
def test_shout_with_people_is_shout_whatever_the_settings(npcs, game_master_mode, director):
    talk = conversation(config(game_master_mode, director), npcs=npcs, shout_state=ShoutState())
    assert isinstance(conversation_type(talk), shout)
    expected = {"shout", "director"} if director else {"shout"} # Director Mode is true together with Shout, also with one NPC
    assert conditions(talk, GAME_MASTER) == expected and conditions(talk, NPC) == expected


@pytest.mark.parametrize("director", [True, False])
def test_saying_something_to_the_room_is_say_to_room_and_never_shout_as_well(director):
    talk = conversation(config(game_master_mode=False, director=director), npcs=0, shout_state=ShoutState())
    assert isinstance(conversation_type(talk), shout)
    extra = {"director"} if director else set() # Director Mode: what was said to the room is a direction
    assert conditions(talk, GAME_MASTER) == {"say_to_room"} | extra
    assert conditions(talk, NPC) == {"shout"} | extra # Whoever answers has joined by the time they speak


@pytest.mark.parametrize("game_master_mode, expected_type", [(True, multi_npc_game_master), (False, multi_npc)])
def test_one_npc_with_director_mode_is_directed(game_master_mode, expected_type):
    talk = conversation(config(game_master_mode, director=True), npcs=1)
    assert type(conversation_type(talk)) is expected_type
    assert conversation_type(talk).is_director_mode()
    if game_master_mode:
        assert conditions(talk, GAME_MASTER) == {"director"} and conditions(talk, NPC) == {"director"}


def test_one_npc_without_director_mode_is_one_on_one():
    talk = conversation(config(director=False), npcs=1)
    assert isinstance(conversation_type(talk), pc_to_npc)


@pytest.mark.parametrize("npcs, shouting", [(1, False), (2, False), (1, True), (3, True)])
def test_a_conversation_an_npc_started_is_never_directed(npcs, shouting):
    talk = conversation(config(director=True), npcs=npcs, shout_state=ShoutState() if shouting else None, started_by_npc=True)
    chosen = conversation_type(talk)
    if npcs == 1 and not shouting:
        assert isinstance(chosen, pc_to_npc)
    else:
        assert not chosen.is_director_mode()
        assert "director" not in conditions(talk, GAME_MASTER)


def test_watching_an_npc_event_is_npc_event():
    talk = conversation(config(game_master_mode=False), npcs=2, has_player=False, shout_state=ShoutState(), event=scene())
    assert isinstance(conversation_type(talk), npc_event)
    assert conditions(talk, GAME_MASTER) == {"npc_event"} and conditions(talk, NPC) == {"npc_event"}


# Moving between rows

def test_someone_answering_what_was_said_to_the_room_makes_it_shout():
    talk = conversation(config(), npcs=0, shout_state=ShoutState())
    assert conditions(talk, GAME_MASTER) == {"say_to_room"}
    talk.context.get_characters_excluding_player.return_value = [MagicMock()]
    assert conditions(talk, GAME_MASTER) == {"shout"}


@pytest.mark.parametrize("director", [False, True])
def test_joining_an_npc_event_makes_it_shout(director):
    talk = conversation(config(director=director), npcs=2, has_player=False, shout_state=ShoutState(), event=scene())
    assert conditions(talk, GAME_MASTER) == {"npc_event"} # Watching is never directed
    talk.context.npc_event.on_player_joined()
    talk.context.npcs_in_conversation.contains_player_character.return_value = True
    assert conditions(talk, GAME_MASTER) == ({"shout", "director"} if director else {"shout"}) # Joined: the player directs the scene


@pytest.mark.parametrize("director, expected", [(False, set()), (True, {"director"})])
def test_switching_shout_off_with_a_group_goes_back_to_the_group_rows(director, expected):
    talk = conversation(config(director=director), npcs=2, shout_state=ShoutState())
    assert conditions(talk, GAME_MASTER) == {"shout"} | expected
    talk.context.shout = None
    assert conditions(talk, GAME_MASTER) == expected


def test_switching_shout_off_with_one_npc_leaves_the_merged_prompts():
    talk = conversation(config(), npcs=1, shout_state=ShoutState())
    talk.context.shout = None
    assert isinstance(conversation_type(talk), pc_to_npc)


def test_switching_shout_off_with_one_npc_in_director_mode_stays_directed():
    talk = conversation(config(director=True), npcs=1, shout_state=ShoutState())
    assert conditions(talk, GAME_MASTER) == {"shout", "director"}
    talk.context.shout = None
    assert conditions(talk, GAME_MASTER) == {"director"}


def test_turning_director_mode_on_mid_conversation_changes_the_next_prompt():
    cfg = config()
    talk = conversation(cfg, npcs=2)
    group = conversation_type(talk)
    assert group.prompt_conditions(talk.context, GAME_MASTER) == set()
    cfg.multi_conversation_director_mode = True # Hot-swapped settings
    assert group.prompt_conditions(talk.context, GAME_MASTER) == {"director"}


def test_turning_game_master_mode_off_mid_conversation_leaves_the_merged_prompts():
    cfg = config()
    talk = conversation(cfg, npcs=2)
    assert type(conversation_type(talk)) is multi_npc_game_master
    cfg.multi_npc_game_master_mode = False
    assert type(conversation_type(talk)) is multi_npc


# Conversations that don't use the merged prompts

@pytest.mark.parametrize("talk, expected", [
    (lambda: conversation(config(), npcs=1), pc_to_npc),
    (lambda: conversation(config(game_master_mode=False), npcs=2), multi_npc),
    (lambda: conversation(config(game_master_mode=False, director=True), npcs=2), multi_npc),
    (lambda: conversation(config(), npcs=2, has_player=False), radiant),
    (lambda: conversation(config(game=GameEnum.FALLOUT4), npcs=2), multi_npc),
    (lambda: conversation(config(game=GameEnum.FALLOUT4), npcs=1, shout_state=ShoutState()), pc_to_npc),
])
def test_conversations_without_the_merged_prompts(talk, expected):
    chosen = conversation_type(talk())
    assert type(chosen) is expected
    assert not isinstance(chosen, multi_npc_game_master)


# The other merged prompts

@pytest.mark.parametrize("kind, condition", [("conversation", "talk_event"), ("nearby", "nearby_event"), ("random", "random_event")])
def test_each_wheel_event_passes_its_kind(kind, condition):
    with patch("src.conversation.npc_event.request_event", return_value='{"event": "E"}') as request:
        generate_conversation_event(MagicMock(), MagicMock(), kind, ["Lydia"], "Dragonborn", "", "Whiterun", 12, "", "", "", [])
    assert request.call_args.args[2] == "skyrim_conversation_event_generator_prompt" # One prompt for all three kinds
    assert request.call_args.kwargs["conditions"] == {condition}


@pytest.mark.parametrize("after_event", [False, True])
def test_after_event_is_true_only_for_the_choice_right_after_a_wheel_event(after_event):
    cfg = MagicMock()
    cfg.prompt_profile_manager.resolve_prompt.side_effect = lambda _prompt_type, text: text
    cfg.game_master_decision_instructions = "{if after_event}REACT{else}NEXT{end if}"
    cfg.game_master_decision_player_criteria = "{if after_event}REACT{else}NEXT{end if}"
    cfg.game_master_decision_npc_criteria = "{name} speaks."
    prompts = resolve_decision_prompts(cfg, after_event=after_event)
    expected = "REACT" if after_event else "NEXT"
    assert prompts[DECISION_INSTRUCTIONS] == expected and prompts[DECISION_PLAYER_CRITERIA] == expected


@pytest.mark.parametrize("source, is_said_to_room, expected", [
    (lambda: scene(), False, {"npc_event"}),
    (lambda: ShoutState(), False, {"shout"}),
    (lambda: ShoutState(), True, {"say_to_room"}),
])
def test_nearby_criteria_pass_the_situation(source, is_said_to_room, expected):
    cfg = MagicMock()
    prompt_type, _text, passed = source().nearby_criteria(cfg, is_said_to_room)
    assert prompt_type == "skyrim_shout_decision_nearby_criteria" and passed == expected
