import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.conversation.conversation import Conversation
from src.conversation.conversation_type import multi_npc_game_master, npc_event, radiant, shout
from src.conversation.game_master import DECISION_QUESTION, GameMaster, GameMasterTurns
from src.conversation.bystanders import ShoutState
from src.conversation.npc_event import EventPerson, NpcEvent, generate_conversation_event, generate_event, parse_event, wind_down_text
from src.http.communication_constants import communication_constants as comm_consts
from src.llm.messages import SystemMessage, UserMessage
from src.llm.sentence import Sentence
from src.llm.sentence_content import SentenceContent, SentenceTypeEnum
from src.llm.sentence_queue import SentenceQueue
from src.config.definitions.conversation_definitions import MAX_NPC_TURNS_KINDS, max_npc_turns_identifier

HULDA = EventPerson("Hulda", 101, "000101")
MIKAEL = EventPerson("Mikael", 102, "000102")
YSOLDA = EventPerson("Ysolda", 103, "000103")
GUARD = EventPerson("Whiterun Guard", 104, "000104")
GUARD_2 = EventPerson("Whiterun Guard (2)", 105, "000105")
PLAYER = EventPerson("Dragonborn", 20, "000014")


def config(**overrides):
    cfg = MagicMock()
    cfg.npc_event_max_participants = 3
    cfg.npc_event_player_notice_delay = 3
    cfg.npc_event_max_lines_without_player = 10
    cfg.npc_event_wind_down_lines = 2
    cfg.npc_event_generator_prompt = "Scene in {location} with {names} for {player_name}, at most {max_participants}.\n{bios_and_summaries}"
    cfg.npc_event_wind_down_prompt = "Wrap it up, {names}."
    cfg.shout_decision_nearby_criteria = "{if npc_event}{name} was addressed.{else}{name} was shouted at.{end if}" # While the player watches: the NPC Event's branch
    cfg.prompt_profile_manager.resolve_prompt.side_effect = lambda _prompt_type, text: text
    cfg.game_master_use_decision_model = False
    cfg.game_master_decision_instructions = "Who speaks next of {names}?"
    cfg.game_master_decision_npc_criteria = "{name} was asked something."
    cfg.game_master_decision_player_criteria = "{player_name} was addressed."
    cfg.game_master_history_message_count = 0
    cfg.game_master_include_turn_thoughts = False
    for each_kind in MAX_NPC_TURNS_KINDS: # Each kind of conversation has its own
        setattr(cfg, max_npc_turns_identifier(each_kind), 3)
    cfg.llm_response_timeout = 10
    cfg.multi_conversation_director_mode = False
    for name, value in overrides.items():
        setattr(cfg, name, value)
    return cfg


def system_text(request) -> str:
    return request.get_messages_of_type(SystemMessage)[0].text


def event(**overrides) -> NpcEvent:
    values = dict(id=7, text="Hulda and Mikael argue about Emily.", player=PLAYER)
    values.update(overrides)
    return NpcEvent(**values)


def shout_state(**overrides) -> ShoutState:
    """The scene runs in Shout: the people nearby who can join"""
    values = dict(nearby=[YSOLDA, GUARD])
    values.update(overrides)
    return ShoutState(**values)


# Generator reply

def test_parse_event_returns_participants_and_text():
    reply = 'Sure: {"participants": ["Hulda", "mikael"], "event": "They argue."}'
    assert parse_event(reply, [HULDA, MIKAEL, YSOLDA], 3) == ([HULDA, MIKAEL], "They argue.")


def test_parse_event_no_event():
    assert parse_event('{"participants": []}', [HULDA, MIKAEL], 3) is None


@pytest.mark.parametrize("reply", ["no json here", '{"participants": "Hulda"}', '{"participants": ["Hulda", "Mikael"]}', "{not json}"])
def test_parse_event_invalid_replies_give_no_event(reply):
    assert parse_event(reply, [HULDA, MIKAEL], 3) is None


def test_parse_event_drops_unknown_names_and_needs_two_participants():
    assert parse_event('{"participants": ["Hulda", "Nazeem"], "event": "E"}', [HULDA, MIKAEL], 3) is None
    assert parse_event('{"participants": ["Hulda", "Nazeem", "Mikael", "Hulda"], "event": "E"}', [HULDA, MIKAEL], 3) == ([HULDA, MIKAEL], "E")


def test_parse_event_cuts_to_max_participants():
    reply = '{"participants": ["Hulda", "Mikael", "Ysolda"], "event": "E"}'
    assert parse_event(reply, [HULDA, MIKAEL, YSOLDA], 2) == ([HULDA, MIKAEL], "E")


def test_parse_event_tells_same_name_people_apart_by_label():
    reply = '{"participants": ["Whiterun Guard (2)", "Hulda"], "event": "E"}'
    assert parse_event(reply, [GUARD, GUARD_2, HULDA], 3) == ([GUARD_2, HULDA], "E")


def test_generate_event_fills_the_prompt_and_asks_for_json():
    client = MagicMock()
    client.request_call.return_value = '{"participants": ["Hulda", "Mikael"], "event": "They argue."}'
    result = generate_event(config(), client, [HULDA, MIKAEL, YSOLDA], "Dragonborn", "Whiterun", 20, "", "BIOS")
    assert result == ([HULDA, MIKAEL], "They argue.")
    request = client.request_call.call_args.args[0]
    assert system_text(request) == "Scene in Whiterun with Hulda, Mikael, Ysolda for Dragonborn, at most 3.\nBIOS"
    assert '{"participants": ["<name>", "<name>"], "event": "<the event>"}' in request.get_last_message().text


def test_generate_event_needs_a_client_and_two_candidates():
    client = MagicMock()
    assert generate_event(config(), None, [HULDA, MIKAEL], "Dragonborn", "Whiterun", 12, "", "") is None
    assert generate_event(config(), client, [HULDA], "Dragonborn", "Whiterun", 12, "", "") is None
    client.request_call.assert_not_called()


def test_generate_event_is_cancelled_when_a_conversation_starts():
    client = MagicMock()
    assert generate_event(config(), client, [HULDA, MIKAEL], "Dragonborn", "Whiterun", 12, "", "", lambda: True) is None
    client.request_call.assert_not_called()


def test_generate_event_failure_gives_no_event():
    client = MagicMock()
    client.request_call.side_effect = RuntimeError("down")
    assert generate_event(config(), client, [HULDA, MIKAEL], "Dragonborn", "Whiterun", 12, "", "") is None


# Event state

def test_player_is_noticed_only_after_the_delay_and_while_noticeable():
    cfg = config()
    scene = event()
    assert scene.noticed_player(cfg, ["Hulda", "Mikael"]) is None
    scene.player_noticeable = True
    assert scene.noticed_player(cfg, ["Hulda", "Mikael"]) is None # Not enough lines yet
    scene.npc_lines = 3
    assert scene.noticed_player(cfg, ["Hulda", "Mikael"]) is PLAYER
    scene.player_noticeable = False # e.g. the player started sneaking
    assert scene.noticed_player(cfg, ["Hulda", "Mikael"]) is None


def test_player_is_not_noticed_once_joined_while_picked_or_when_named_like_a_participant():
    cfg = config()
    assert event(player_noticeable=True, npc_lines=3, player_joined=True).noticed_player(cfg, ["Hulda"]) is None
    assert event(player_noticeable=True, npc_lines=3, pending_add=PLAYER).noticed_player(cfg, ["Hulda"]) is None
    assert event(player_noticeable=True, npc_lines=3).noticed_player(cfg, ["Hulda", "dragonborn"]) is None


def test_game_update_sets_whether_the_player_can_be_noticed():
    scene = event()
    scene.apply_game_update(True)
    assert scene.player_noticeable
    scene.apply_game_update(None) # Not checked yet
    assert scene.player_noticeable


@pytest.mark.parametrize("wind_down, winding_down_from", [(2, 8), (0, None)])
def test_line_limit_and_wind_down(wind_down, winding_down_from):
    cfg = config(npc_event_wind_down_lines=wind_down)
    scene = event()
    for lines in range(11):
        scene.npc_lines = lines
        assert scene.has_reached_limit(cfg) == (lines >= 10)
        assert scene.is_winding_down(cfg) == (winding_down_from is not None and lines >= winding_down_from)


def test_no_limit_and_no_wind_down_once_the_player_joined():
    cfg = config()
    scene = event(npc_lines=10, player_joined=True)
    assert not scene.has_reached_limit(cfg)
    assert not scene.is_winding_down(cfg)
    assert wind_down_text(scene, cfg, "Hulda and Mikael") == ""


def test_wind_down_text():
    cfg = config()
    assert wind_down_text(event(npc_lines=8), cfg, "Hulda and Mikael") == "Wrap it up, Hulda and Mikael."
    assert wind_down_text(event(npc_lines=7), cfg, "Hulda and Mikael") == ""
    assert wind_down_text(None, cfg, "Hulda and Mikael") == ""


def test_player_joining_makes_it_a_normal_conversation():
    scene = event(pending_add=PLAYER)
    scene.on_player_joined()
    assert scene.player_joined
    assert scene.pending_add is None


# Game Master choices

def npc(name: str, ref_id: str = ""):
    return SimpleNamespace(name=name, ref_id=ref_id or name.lower(), is_player_character=False)


def game_master(cfg, scene: NpcEvent, npcs, ai_client, state: ShoutState | None = None):
    context = MagicMock()
    context.config = cfg
    context.npc_event = scene
    context.shout = state or shout_state()
    context.get_characters_excluding_player.return_value.get_all_characters.return_value = npcs
    context.npcs_in_conversation.get_player_character.return_value = None # The player is a bystander
    context.get_character_names_as_text.return_value = "Hulda and Mikael"
    conversation_type = MagicMock()
    conversation_type.generate_game_master_prompt.return_value = "You are the game master."
    conversation_type.is_director_mode.return_value = False
    return GameMaster(context, conversation_type, ai_client)


def llm_client(reply: str):
    client = MagicMock()
    client.token_limit = 32000
    client.get_count_tokens = lambda text: len(text.split())
    client.request_call.return_value = reply
    return client


def test_game_master_can_pick_someone_nearby():
    scene = event()
    client = llm_client('{"next": "Ysolda", "reason": "Hulda asked her"}')
    gm = game_master(config(), scene, [npc("Hulda"), npc("Mikael")], client)
    assert gm.choose_next_speaker(MagicMock(), [], True, allow_nearby=True) is YSOLDA
    assert scene.pending_add is None # Set by the turn, once it knows the turn is still current
    request_text = client.request_call.call_args.args[0].get_last_message().text
    assert "Choose one of: Hulda, Mikael, Ysolda, Whiterun Guard." in request_text


def test_game_master_offers_nobody_nearby_unless_allowed():
    scene = event()
    client = llm_client('{"next": "Ysolda", "reason": "?"}')
    gm = game_master(config(), scene, [npc("Hulda"), npc("Mikael")], client)
    assert gm.choose_next_speaker(MagicMock(), [], False) is None
    assert scene.pending_add is None
    assert "Ysolda" not in client.request_call.call_args.args[0].get_last_message().text


def test_game_master_picking_a_participant_needs_no_add():
    scene = event()
    mikael = npc("Mikael")
    gm = game_master(config(), scene, [npc("Hulda"), mikael], llm_client('{"next": "Mikael", "reason": "r"}'))
    assert gm.choose_next_speaker(MagicMock(), [], True, allow_nearby=True) is mikael
    assert scene.pending_add is None


def test_game_master_picking_a_participant_named_like_someone_nearby_needs_no_add():
    scene = event()
    guard_in_scene = npc("Whiterun Guard", "000105") # GUARD_2 in the check
    client = llm_client('{"next": "Whiterun Guard", "reason": "r"}')
    gm = game_master(config(), scene, [npc("Hulda"), guard_in_scene], client)
    assert gm.choose_next_speaker(MagicMock(), [], True, allow_nearby=True) is guard_in_scene
    assert "Choose one of: Hulda, Whiterun Guard, Ysolda." in client.request_call.call_args.args[0].get_last_message().text


def test_game_master_can_pick_the_noticed_bystander_player():
    scene = event(player_noticeable=True, npc_lines=3)
    client = llm_client('{"next": "Dragonborn", "reason": "Mikael asked them"}')
    gm = game_master(config(), scene, [npc("Hulda"), npc("Mikael")], client)
    assert gm.choose_next_speaker(MagicMock(), [], True, allow_nearby=True) is PLAYER


def test_the_noticed_player_is_offered_outside_shouts_limits():
    scene = event(player_noticeable=True, npc_lines=3)
    client = llm_client('{"next": "Hulda", "reason": "r"}')
    gm = game_master(config(), scene, [npc("Hulda"), npc("Mikael")], client, shout_state(max_people=2)) # Full
    gm.choose_next_speaker(MagicMock(), [], True, allow_nearby=True)
    assert "Choose one of: Hulda, Mikael, Dragonborn." in client.request_call.call_args.args[0].get_last_message().text


def test_npcs_in_the_scene_count_toward_most_people_without_the_player():
    scene = event()
    client = llm_client('{"next": "Hulda", "reason": "r"}')
    gm = game_master(config(), scene, [npc("Hulda"), npc("Mikael")], client, shout_state(max_people=3))
    gm.choose_next_speaker(MagicMock(), [], True, allow_nearby=True)
    assert "Choose one of: Hulda, Mikael, Ysolda, Whiterun Guard." in client.request_call.call_args.args[0].get_last_message().text


def test_game_master_gets_the_wind_down_prompt():
    scene = event(npc_lines=9)
    client = llm_client('{"next": "Hulda", "reason": "r"}')
    gm = game_master(config(), scene, [npc("Hulda"), npc("Mikael")], client)
    gm.choose_next_speaker(MagicMock(), [], True, allow_nearby=True)
    assert system_text(client.request_call.call_args.args[0]) == "You are the game master.\n\nWrap it up, Hulda and Mikael."


@patch("src.conversation.game_master.requests.post")
def test_decision_model_gets_the_nearby_criteria_and_can_pick_someone_nearby(post):
    scene = event()
    body = {"answers": {DECISION_QUESTION: {"type": "choice", "choice": "Whiterun Guard", "confidence": 0.8, "probabilities": {"Whiterun Guard": 0.8}}}}
    post.return_value = MagicMock(status_code=200, text=json.dumps(body))
    post.return_value.json.return_value = body
    client = llm_client("")
    client.service_name = "OpenRouter"
    client.model_name = "typesafe/jev-1.13"
    gm = game_master(config(game_master_use_decision_model=True), scene, [npc("Hulda"), npc("Mikael")], client)
    assert gm.choose_next_speaker(MagicMock(), [], True, allow_nearby=True) is GUARD
    criteria = post.call_args.kwargs["json"]["questions"][DECISION_QUESTION]["criteria"]
    assert criteria["Ysolda"] == "Ysolda was addressed."
    assert criteria["Whiterun Guard"] == "Whiterun Guard was addressed."
    assert list(criteria) == ["Hulda", "Mikael", "Ysolda", "Whiterun Guard"] # No "Player" option for a bystander


# Game Master turns

HULDA_NPC = npc("Hulda", HULDA.ref_id)
MIKAEL_NPC = npc("Mikael", MIKAEL.ref_id)
YSOLDA_NPC = npc("Ysolda", YSOLDA.ref_id)


class EventTurns:
    """A GameMasterTurns for an event, with a fake LLM that answers each request with one line, and a Game Master that makes the given picks"""
    def __init__(self, scene: NpcEvent, picks: list, cfg=None, state: ShoutState | None = None):
        self.cfg = cfg or config()
        self.shout = state or shout_state()
        context = MagicMock()
        context.config = self.cfg
        context.npc_event = scene
        context.shout = self.shout
        context.npcs_in_conversation.last_added_character = HULDA_NPC
        context.npcs_in_conversation.contains_multiple_npcs.return_value = True
        context.get_characters_excluding_player.return_value.get_all_characters.return_value = [HULDA_NPC, MIKAEL_NPC, YSOLDA_NPC]
        context.get_character_names_as_text.return_value = "Hulda and Mikael"
        self.sentences = SentenceQueue()
        self.turns = GameMasterTurns(context, self.sentences)
        self.requests: list[tuple[str, str]] = [] # (speaker, the last message sent with the request)
        self.output_manager = MagicMock()
        self.output_manager.generate_response.side_effect = self.__generate_response
        self.conversation_type = MagicMock(spec=npc_event)
        self.conversation_type.generate_npc_prompt.return_value = "NPC prompt"
        self.conversation_type.is_director_mode.return_value = False
        self.live_messages = MagicMock()
        self.live_messages.get_persistent_messages.return_value = []
        self.__patch = patch("src.conversation.game_master.GameMaster")
        self.game_master = self.__patch.start().return_value
        self.game_master.choose_next_speaker.side_effect = picks

    def __generate_response(self, request, _characters, queue, _actions, single_speaker):
        self.requests.append((single_speaker.name, request.get_last_message().text))
        queue.put(Sentence(SentenceContent(single_speaker, f"{single_speaker.name} line {len(self.requests)}.", SentenceTypeEnum.SPEECH, False), "", 0))
        queue.is_more_to_come = False
        queue.put(Sentence(SentenceContent(single_speaker, "", SentenceTypeEnum.SPEECH, True), "", 0))

    def run(self, allow_player_first: bool = False):
        """Runs one turn, playing each reply as soon as it is ready"""
        turn_id = self.turns.start_turn(allow_player_first)
        self.sentences.is_more_to_come = True
        thread = threading.Thread(target=self.turns.run_turn, args=(turn_id, lambda: self.conversation_type, lambda: self.output_manager, self.live_messages))
        thread.start()
        try:
            while self.turns.play_next_reply(2):
                pass
            thread.join(2)
        finally:
            self.turns.cancel()
            thread.join(2)
            self.__patch.stop()


def test_bystander_scene_ignores_max_npc_turns_and_stops_at_the_line_limit():
    scene = event()
    turns = EventTurns(scene, [HULDA_NPC] + [MIKAEL_NPC, HULDA_NPC] * 10, config(npc_event_max_lines_without_player=5))
    turns.run()
    assert len(turns.requests) == 5
    assert scene.npc_lines == 5
    assert scene.has_reached_limit(turns.cfg)


def test_later_picks_may_draw_in_people_nearby_but_the_first_may_not():
    turns = EventTurns(event(), [HULDA_NPC, None])
    turns.run()
    first_call, next_call = turns.game_master.choose_next_speaker.call_args_list
    assert "allow_nearby" not in first_call.kwargs
    assert next_call.kwargs["allow_nearby"] is True


def test_the_npc_who_joined_speaks_first_without_asking_the_game_master():
    state = shout_state(forced_speaker_ref_id=YSOLDA.ref_id)
    turns = EventTurns(event(), [None], state=state)
    turns.run()
    assert [speaker for speaker, _ in turns.requests] == ["Ysolda"]
    assert turns.game_master.choose_next_speaker.call_count == 1 # Only for who follows Ysolda
    assert state.forced_speaker_ref_id == ""


def test_a_nearby_pick_ends_the_turn_and_shout_adds_them():
    scene = event()
    turns = EventTurns(scene, [HULDA_NPC, YSOLDA])
    turns.run()
    assert [speaker for speaker, _ in turns.requests] == ["Hulda"]
    assert turns.shout.pending_add is YSOLDA
    assert scene.pending_add is None


def test_picking_the_player_ends_the_turn_and_the_event_adds_them():
    scene = event(player_noticeable=True, npc_lines=3)
    turns = EventTurns(scene, [HULDA_NPC, PLAYER])
    turns.run()
    assert scene.pending_add is PLAYER
    assert turns.shout.pending_add is None


def test_a_nearby_pick_from_a_turn_thrown_away_is_ignored():
    scene = event()
    turns = EventTurns(scene, [])
    picks = iter([HULDA_NPC, YSOLDA])
    def choose(*_args, **_kwargs):
        pick = next(picks)
        if pick is YSOLDA:
            turns.turns.on_player_input() # The turn is thrown away while the Game Master decides
        return pick
    turns.game_master.choose_next_speaker.side_effect = choose
    turns.run()
    assert turns.shout.pending_add is None


def test_npc_requests_get_the_wind_down_prompt_in_the_last_lines():
    turns = EventTurns(event(npc_lines=7), [HULDA_NPC, MIKAEL_NPC, HULDA_NPC])
    turns.run()
    assert turns.requests[0][1] == "(Hulda speaks next.)"
    assert turns.requests[1][1] == "(Mikael speaks next.) Wrap it up, Hulda and Mikael."
    assert len(turns.requests) == 3 # Lines 8, 9 and 10


def test_a_failed_turn_marks_the_event_as_failed():
    scene = event()
    turns = EventTurns(scene, [HULDA_NPC])
    turns.conversation_type.generate_npc_prompt.side_effect = RuntimeError("broken")
    turns.run()
    assert scene.has_failed


# Conversation

def event_conversation(scene: NpcEvent, has_player: bool = False) -> Conversation:
    context = MagicMock()
    context.npc_event = scene
    context.config = config()
    context.config.end_conversation_keyword = "goodbye"
    context.config.max_count_events = 5
    context.get_context_ingame_events.return_value = []
    context.npcs_in_conversation.contains_player_character.return_value = has_player
    context.npcs_in_conversation.active_character_count.return_value = 2
    conversation = Conversation(context, MagicMock(), MagicMock(), MagicMock(), None, False, False)
    conversation._Conversation__conversation_type = npc_event(context.config)
    conversation._Conversation__game_master_turns = MagicMock()
    conversation._Conversation__game_master_turns.play_next_reply.return_value = False
    conversation._Conversation__game_master_turns.nobody_left_to_speak.return_value = False
    conversation._Conversation__llm_client.is_too_long.return_value = False
    return conversation


def test_event_type_is_used_without_the_player_and_with_two_npcs():
    conversation = event_conversation(event())
    select = conversation._Conversation__select_conversation_type
    assert isinstance(select(conversation.context.config), npc_event)
    conversation.context.npc_event = None
    assert isinstance(select(conversation.context.config), radiant)


def test_once_the_player_joined_it_is_a_normal_shout_conversation():
    conversation = event_conversation(event(player_joined=True), has_player=True)
    conversation.context.shout = shout_state()
    assert isinstance(conversation._Conversation__select_conversation_type(conversation.context.config), shout)


def test_event_type_is_used_with_game_master_mode_off():
    conversation = event_conversation(event())
    conversation.context.config.multi_npc_game_master_mode = False
    assert isinstance(conversation._Conversation__select_conversation_type(conversation.context.config), npc_event)


def test_event_type_needs_a_game_master_client():
    conversation = event_conversation(event())
    conversation.output_manager.game_master_client = None
    assert isinstance(conversation._Conversation__select_conversation_type(conversation.context.config), radiant)


def test_event_type_is_a_game_master_type_without_greeting_or_director_mode():
    event_type = npc_event(config(multi_conversation_director_mode=True))
    assert isinstance(event_type, multi_npc_game_master)
    assert not event_type.is_director_mode()
    assert event_type.get_user_message(MagicMock(), MagicMock()) is None


def test_start_starts_the_first_turn():
    conversation = event_conversation(event())
    with patch.object(conversation, "_Conversation__start_generating_npc_sentences") as start:
        assert conversation.start_conversation() == (comm_consts.KEY_REPLYTYPE_NPCTALK, None)
    start.assert_called_once()


def test_start_opens_with_the_event_as_an_ingame_event():
    conversation = event_conversation(event())
    with patch.object(conversation, "_Conversation__start_generating_npc_sentences"):
        conversation.start_conversation()
    messages = conversation._Conversation__messages.get_persistent_messages()
    assert len(messages) == 1
    assert isinstance(messages[0], UserMessage)
    assert messages[0].is_director_message
    assert not messages[0].is_system_generated_message # Kept in summaries
    assert messages[0].text == "" and messages[0].count_ingame_events() == 1 # As an in-game event, like a custom game event
    assert event().text in messages[0].get_ingame_events_text()


@pytest.mark.parametrize("scene, state, has_player, expected, starts_turn", [
    (event(pending_add=PLAYER), None, False, comm_consts.KEY_REPLYTYPE_NPCEVENT_ADDACTOR, False),
    (event(), shout_state(pending_add=YSOLDA), False, comm_consts.KEY_REPLYTYPE_SHOUT_ADDACTOR, False),
    (event(), shout_state(forced_speaker_ref_id=YSOLDA.ref_id), False, comm_consts.KEY_REPLYTYPE_NPCTALK, True),
    (event(player_joined=True), None, True, comm_consts.KEY_REPLYTYPE_PLAYERTALK, False),
    (event(npc_lines=10), None, False, comm_consts.KEY_REPLYTYPE_ENDCONVERSATION, False),
    (event(has_failed=True), None, False, comm_consts.KEY_REPLYTYPE_ENDCONVERSATION, False),
    (event(npc_lines=4), None, False, comm_consts.KEY_REPLYTYPE_NPCTALK, True), # The Game Master gave no valid choice: the scene goes on
])
def test_what_follows_the_npcs_turn(scene, state, has_player, expected, starts_turn):
    conversation = event_conversation(scene, has_player)
    conversation.context.shout = state
    with patch.object(conversation, "_Conversation__start_generating_npc_sentences") as start, \
         patch.object(conversation, "retrieve_sentence_from_queue", return_value=None):
        assert conversation.continue_conversation() == (expected, None)
    assert start.called == starts_turn


def test_bystander_player_is_never_listened_to():
    conversation = event_conversation(event(npc_lines=4))
    stt = MagicMock()
    stt.is_listening = False
    stt.has_player_spoken = False
    conversation._Conversation__stt = stt
    conversation._Conversation__mic_input = True
    conversation._Conversation__allow_interruption = True
    with patch.object(conversation, "_Conversation__start_generating_npc_sentences"), \
         patch.object(conversation, "retrieve_sentence_from_queue", return_value=None):
        conversation.continue_conversation()
    stt.start_listening.assert_not_called()


def test_bystander_player_steps_in_with_the_interrupt_hotkey():
    conversation = event_conversation(event(npc_lines=4))
    conversation.request_interrupt()
    with patch.object(conversation, "_Conversation__start_generating_npc_sentences") as start:
        assert conversation.continue_conversation() == (comm_consts.KEY_REPLYTYPE_PLAYERTALK, None)
    start.assert_not_called()


def test_interrupt_hotkey_is_ignored_without_the_player_outside_npc_events():
    conversation = event_conversation(None)
    conversation.request_interrupt()
    assert not conversation._Conversation__is_interrupt_requested


def conversation_waiting_for_last_line(sentence: Sentence) -> Conversation:
    """The next line is in the queue while the last one is still playing in game"""
    conversation = event_conversation(event(player_joined=True), has_player=True)
    conversation._Conversation__sentences.put(sentence)
    conversation.last_sentence_start_time = time.time()
    conversation.last_sentence_audio_length = 5
    return conversation


def test_line_thrown_away_by_an_interruption_is_not_in_the_history():
    line = Sentence(SentenceContent(npc("Hulda"), "Not spoken.", SentenceTypeEnum.SPEECH, False), "", 2.0)
    conversation = conversation_waiting_for_last_line(line)
    threading.Timer(0.1, conversation.request_interrupt).start() # Pressed while the last line plays
    with patch.object(conversation, "add_sentence_to_history") as add:
        assert conversation.continue_conversation() == (comm_consts.KEY_REPLYTYPE_PLAYERTALK, None)
    add.assert_not_called()


def test_interrupt_hotkey_works_while_the_llm_has_not_answered():
    conversation = event_conversation(event(player_joined=True), has_player=True)
    sentences = conversation._Conversation__sentences
    sentences.is_more_to_come = True # Waiting for the LLM
    threading.Timer(0.1, conversation.request_interrupt).start()
    late_line = threading.Timer(2, sentences.put, [Sentence(SentenceContent(npc("Hulda"), "Too late.", SentenceTypeEnum.SPEECH, False), "", 2.0)])
    late_line.start() # Ends the wait if the hotkey does not
    started_at = time.time()
    try:
        assert conversation.continue_conversation() == (comm_consts.KEY_REPLYTYPE_PLAYERTALK, None)
    finally:
        late_line.cancel()
    assert time.time() - started_at < 1


def test_line_sent_to_the_game_is_in_the_history():
    line = Sentence(SentenceContent(npc("Hulda"), "Spoken.", SentenceTypeEnum.SPEECH, False), "", 2.0)
    conversation = conversation_waiting_for_last_line(line)
    conversation.last_sentence_audio_length = 0.1
    with patch.object(conversation, "add_sentence_to_history") as add:
        assert conversation.continue_conversation() == (comm_consts.KEY_REPLYTYPE_NPCTALK, line)
    add.assert_called_once_with(line)


def test_the_player_joining_updates_the_event():
    scene = event(pending_add=PLAYER)
    conversation = event_conversation(scene)
    player = SimpleNamespace(name="Dragonborn", ref_id=PLAYER.ref_id, is_player_character=True)
    conversation.context.add_or_update_characters.return_value = SimpleNamespace(added_npcs=[player], removed_npcs=[])
    conversation.add_or_update_character([HULDA_NPC, MIKAEL_NPC, player])
    assert scene.player_joined
    assert scene.pending_add is None


def test_someone_nearby_joining_the_scene_is_a_shout_join():
    scene = event()
    state = shout_state(pending_add=YSOLDA)
    conversation = event_conversation(scene)
    conversation.context.shout = state
    conversation.context.add_or_update_characters.return_value = SimpleNamespace(added_npcs=[YSOLDA_NPC], removed_npcs=[])
    conversation.context.npcs_in_conversation.get_all_names.return_value = ["Hulda", "Mikael", "Ysolda"]
    with patch("src.conversation.conversation.overheard_message"), patch("src.conversation.conversation.nearby_join_message"), \
         patch.object(conversation, "generate_add_or_remove_messages", return_value=[]):
        conversation.add_or_update_character([HULDA_NPC, MIKAEL_NPC, YSOLDA_NPC])
    assert state.pending_add is None
    assert state.forced_speaker_ref_id == YSOLDA.ref_id
    assert not scene.player_joined


def test_join_does_not_need_messages_heard_by():
    """Later joiners hear the whole scene: their request has every line, including those before they joined"""
    turns = EventTurns(event(), [None], state=shout_state(forced_speaker_ref_id=YSOLDA.ref_id))
    cfg = turns.cfg
    history = [UserMessage(cfg, "Hulda: You liar!", "", True)]
    turns.live_messages.get_persistent_messages.return_value = history
    with patch("src.conversation.game_master.messages_heard_by") as heard_by:
        turns.run()
    heard_by.assert_not_called()


def test_once_the_player_joined_npcs_only_hear_what_was_said_while_they_were_there():
    turns = EventTurns(event(player_joined=True), [None], state=shout_state(forced_speaker_ref_id=YSOLDA.ref_id))
    turns.live_messages.get_persistent_messages.return_value = [UserMessage(turns.cfg, "Hulda: You liar!", "", True)]
    with patch("src.conversation.game_master.messages_heard_by", side_effect=lambda history, _speaker: history) as heard_by:
        turns.run()
    heard_by.assert_called()


# The check (server side)

def event_check_manager(characters: dict[int, SimpleNamespace]):
    """A GameStateManager with only what npc_event_check uses. load_character returns the character of the actor's refid"""
    from src.game_manager import GameStateManager
    manager = object.__new__(GameStateManager)
    manager._GameStateManager__conversation_start_count = 0
    manager._GameStateManager__config = config()
    manager._GameStateManager__rememberer = MagicMock()
    manager._GameStateManager__game = MagicMock()
    manager._GameStateManager__npc_event_count = 0
    manager._GameStateManager__pending_npc_event = None
    manager._GameStateManager__get_npc_event_client = MagicMock()
    manager._GameStateManager__chat_manager = MagicMock() # Has a Game Master client
    manager.load_character = lambda actor_json, _world_id=None: characters[actor_json[comm_consts.KEY_ACTOR_REFID]]
    return manager


def test_no_npc_event_is_generated_without_a_game_master_model():
    manager = event_check_manager({})
    manager._GameStateManager__chat_manager.game_master_client = None
    manager._GameStateManager__get_npc_initiative_client = MagicMock(return_value=None)
    with patch("src.conversation.npc_event.generate_event") as generate:
        reply = manager.npc_event_check({})
    assert reply[comm_consts.KEY_NPCEVENT_ID] == 0
    generate.assert_not_called()


def test_check_names_everyone_nearby_but_describes_only_those_sent_in_full():
    player = SimpleNamespace(name="Dragonborn", ref_id="000014", is_player_character=True)
    hulda = SimpleNamespace(name="Hulda", ref_id="000101", is_player_character=False)
    manager = event_check_manager({20: player, 101: hulda})
    request = {
        comm_consts.KEY_ACTORS: [{comm_consts.KEY_ACTOR_REFID: 20}, {comm_consts.KEY_ACTOR_REFID: 101}],
        comm_consts.KEY_NPCEVENT_NEARBY_REFIDS: [101, 102, 104, 105],
        comm_consts.KEY_NPCEVENT_NEARBY_NAMES: ["Hulda", "Mikael", "Whiterun Guard", "Whiterun Guard"],
        comm_consts.KEY_EVENT_HINT: " a thief grabs her purse ",
    }
    with patch("src.conversation.npc_event.generate_event", return_value=([HULDA, MIKAEL], "They argue.")) as generate, \
         patch("src.conversation.context.build_bios_and_summaries_text", return_value="BIOS") as bios:
        reply = manager.npc_event_check(request)
    people = generate.call_args.args[2]
    assert [p.name for p in people] == ["Hulda", "Mikael", "Whiterun Guard", "Whiterun Guard (2)"]
    assert [p.refid for p in people] == [101, 102, 104, 105]
    assert bios.call_args.args[0] == [hulda] # Only the nearest, sent in full
    assert generate.call_args.args[3] == "Dragonborn"
    assert generate.call_args.args[9] == "a thief grabs her purse"
    assert reply[comm_consts.KEY_NPCEVENT_PARTICIPANT_REFIDS] == [HULDA.refid, MIKAEL.refid]
    assert reply[comm_consts.KEY_EVENT_TEXT] == "They argue." # So the game can show it
    pending = manager._GameStateManager__pending_npc_event
    assert pending.text == "They argue." and pending.player.name == "Dragonborn"


def test_generate_event_fills_the_hint():
    client = MagicMock()
    client.request_call.return_value = '{"participants": ["Hulda", "Mikael"], "event": "E"}'
    cfg = config(npc_event_generator_prompt="Wish: {hint}.")
    generate_event(cfg, client, [HULDA, MIKAEL], "Dragonborn", "Whiterun", 12, "", "", hint="a duel")
    assert system_text(client.request_call.call_args.args[0]) == "Wish: a duel."
    generate_event(cfg, client, [HULDA, MIKAEL], "Dragonborn", "Whiterun", 12, "", "")
    assert system_text(client.request_call.call_args.args[0]) == "Wish: ."


# Conversation, Nearby and Random Events

def events_config(**overrides):
    cfg = config(conversation_event_generator_prompt="{if talk_event}\nConversation {names} for {player_name} ({player_description}). Wish: {hint}.\n"
                                                     "{bios_and_summaries}\n{else if nearby_event}\nNearby {names}.\n{else}\nRandom {names}.\n{end if}")
    for name, value in overrides.items():
        setattr(cfg, name, value)
    return cfg


def test_conversation_event_fills_its_prompt_and_adds_the_conversation_before_the_reply_format():
    client = MagicMock()
    client.request_call.return_value = '{"participants": [], "event": "A cart crashes into the stall."}'
    text = generate_conversation_event(events_config(), client, "conversation", ["Hulda", "Dragonborn"], "Dragonborn", "A tall Nord",
                                       "Whiterun", 12, "", "BIOS", "a crash", ["Hulda: Fresh bread!", "Dragonborn: Two, please."])
    assert text == "A cart crashes into the stall." # Nobody in the list, and it still happened
    request = client.request_call.call_args.args[0]
    assert system_text(request) == "Conversation Hulda, Dragonborn for Dragonborn (A tall Nord). Wish: a crash.\nBIOS"
    last = request.get_last_message().text
    assert last.startswith("The conversation so far:\nHulda: Fresh bread!\nDragonborn: Two, please.\n\nReply only with JSON")


def test_conversation_event_without_the_conversation_sends_only_the_reply_format():
    client = MagicMock()
    client.request_call.return_value = '{"participants": ["Nazeem"], "event": "A fight breaks out."}'
    assert generate_conversation_event(events_config(), client, "nearby", ["Ysolda"], "Dragonborn", "", "Whiterun", 12, "", "", "", []) == "A fight breaks out."
    request = client.request_call.call_args.args[0]
    assert system_text(request) == "Nearby Ysolda."
    assert request.get_last_message().text.startswith("Reply only with JSON")


@pytest.mark.parametrize("reply", ['{"participants": []}', "no json", '{"participants": ["Hulda"], "event": "  "}'])
def test_conversation_event_with_no_event_gives_none(reply):
    client = MagicMock()
    client.request_call.return_value = reply
    assert generate_conversation_event(events_config(), client, "random", ["Hulda"], "Dragonborn", "", "Whiterun", 12, "", "", "", []) is None


def test_conversation_event_is_cancelled_when_the_conversation_ends():
    client = MagicMock()
    client.request_call.return_value = '{"participants": [], "event": "E"}'
    assert generate_conversation_event(events_config(), client, "random", ["Hulda"], "Dragonborn", "", "Whiterun", 12, "", "", "", [], lambda: True) is None


def event_added_conversation(conversation_type, state: ShoutState | None = None) -> Conversation:
    """A conversation the player is in, during the player's turn"""
    conversation = event_conversation(None, has_player=True)
    conversation.context.shout = state
    conversation.context.have_actors_changed = False
    conversation.context.get_context_ingame_events.return_value = []
    conversation.context.config.max_count_events = 5
    conversation.context.npcs_in_conversation.contains_multiple_npcs.return_value = True
    conversation._Conversation__conversation_type = conversation_type
    return conversation


def test_a_nearby_event_switches_shout_on_before_the_narration_and_lets_the_player_react_first():
    cfg = config()
    conversation = event_added_conversation(multi_npc_game_master(cfg))
    conversation._Conversation__messages.add_message(UserMessage(cfg, "Hello there.", "Dragonborn", False))
    ysolda_character = SimpleNamespace(name="Ysolda")
    with patch.object(conversation, "_Conversation__update_conversation_type") as update_type, \
         patch.object(conversation, "_Conversation__start_generating_npc_sentences") as start:
        conversation.add_event("A thief grabs Ysolda's purse.", "nearby", {"max_people": 4}, [YSOLDA], {YSOLDA.refid: ysolda_character})
    state = conversation.context.shout
    assert isinstance(state, ShoutState) and state.max_people == 4
    assert state.nearby == [YSOLDA] and state.characters == {YSOLDA.refid: ysolda_character}
    assert state.event_ignores_max_people # The people it is about may join past Most People in Conversation
    messages = conversation._Conversation__messages.get_persistent_messages()
    assert messages[-1].text == "" and messages[-1].is_director_message # As an in-game event, like a custom game event
    assert "A thief grabs Ysolda's purse." in messages[-1].get_ingame_events_text()
    assert getattr(messages[0], "shout_marker", None) == state.start_marker is not None # Shout starts before the narration
    update_type.assert_called_once()
    start.assert_called_once_with(allow_player_first=True)


def test_a_conversation_event_leaves_shout_as_it_is():
    cfg = config()
    conversation = event_added_conversation(multi_npc_game_master(cfg))
    with patch.object(conversation, "_Conversation__start_generating_npc_sentences") as start:
        conversation.add_event("The bell rings.", "conversation", None, [], {})
    assert conversation.context.shout is None
    start.assert_called_once_with(allow_player_first=True)


def test_a_conversation_event_during_shout_keeps_most_people_in_conversation():
    conversation = event_added_conversation(multi_npc_game_master(config()), ShoutState(event_ignores_max_people=True)) # After an earlier Nearby Event
    with patch.object(conversation, "_Conversation__start_generating_npc_sentences"):
        conversation.add_event("The bell rings.", "conversation", None, [], {})
    assert not conversation.context.shout.event_ignores_max_people


def test_the_interrupt_sent_when_the_event_was_picked_does_not_stop_the_reaction():
    conversation = event_added_conversation(multi_npc_game_master(config()))
    conversation.request_interrupt()
    with patch.object(conversation, "_Conversation__start_generating_npc_sentences"):
        conversation.add_event("The bell rings.", "conversation", None, [], {})
    assert not conversation._Conversation__is_interrupt_requested


@pytest.mark.parametrize("picked_player, npc_reacts", [(True, False), (False, True)])
def test_one_on_one_asks_the_game_master_once_who_reacts_first(picked_player, npc_reacts):
    from src.conversation.conversation_type import pc_to_npc
    conversation = event_added_conversation(pc_to_npc(config()))
    with patch("src.conversation.conversation.GameMaster") as game_master_class, \
         patch.object(conversation, "_Conversation__start_generating_npc_sentences") as start:
        game_master_class.return_value.picked_player = picked_player
        conversation.add_event("The bell rings.", "conversation", None, [], {})
    assert game_master_class.return_value.choose_next_speaker.call_args.kwargs["allow_player"] is True
    assert game_master_class.return_value.choose_next_speaker.call_args.kwargs["after_event"] is True
    assert isinstance(game_master_class.call_args.args[1], multi_npc_game_master)
    assert start.called == npc_reacts


def test_one_on_one_with_director_mode_on_still_lets_the_player_react_first():
    """Only a conversation an NPC started is a one-on-one with Director Mode on: the player speaks in it, so the Game Master's
    question who reacts first is never skipped as in a directed conversation"""
    from src.config.definitions.game_definitions import GameEnum
    from src.conversation.conversation_type import pc_to_npc
    cfg = config(multi_conversation_director_mode=True)
    cfg.game.base_game = GameEnum.SKYRIM
    conversation = event_added_conversation(pc_to_npc(cfg))
    with patch("src.conversation.conversation.GameMaster") as game_master_class, \
         patch.object(conversation, "_Conversation__start_generating_npc_sentences"):
        game_master_class.return_value.picked_player = True
        conversation.add_event("The bell rings.", "conversation", None, [], {})
    assert not game_master_class.call_args.args[1].is_director_mode()


def test_the_first_pick_after_an_event_may_give_the_player_the_turn():
    turns = EventTurns(event(player_joined=True), [None])
    turns.game_master.picked_player = True
    turns.run(allow_player_first=True)
    assert turns.requests == [] # Nobody spoke: the player reacts first
    assert turns.game_master.choose_next_speaker.call_args.kwargs["allow_player"] is True
    assert turns.game_master.choose_next_speaker.call_args.kwargs["after_event"] is True # Asked who reacts to the event first


def test_the_first_pick_without_an_event_is_not_asked_who_reacts_first():
    turns = EventTurns(event(player_joined=True), [None, None])
    turns.game_master.picked_player = False
    turns.run()
    assert turns.game_master.choose_next_speaker.call_args_list[0].kwargs["after_event"] is False


def test_without_a_player_pick_the_first_speaker_falls_back_to_an_npc():
    turns = EventTurns(event(player_joined=True), [None, None])
    turns.game_master.picked_player = False
    turns.run(allow_player_first=True)
    assert [speaker for speaker, _ in turns.requests] == ["Hulda"]


def test_a_mic_wait_ends_when_an_event_is_picked():
    conversation = event_added_conversation(multi_npc_game_master(config()))
    stt = MagicMock()
    stt.is_listening = True
    def wait_for_speech():
        conversation.stop_waiting_for_player() # The event is picked while the player is expected to speak
        return ""
    stt.get_latest_transcription.side_effect = wait_for_speech
    conversation._Conversation__stt = stt
    conversation._Conversation__mic_input = True
    conversation.context.npcs_in_conversation.get_player_character.return_value = SimpleNamespace(name="Dragonborn")
    assert conversation.process_player_input("") == (None, False, None)
    stt.cancel_wait.assert_called_once()
    stt.stop_listening.assert_called_once()


def test_stopping_the_wait_does_nothing_while_nobody_waits():
    conversation = event_added_conversation(multi_npc_game_master(config()))
    conversation._Conversation__stt = MagicMock()
    conversation.stop_waiting_for_player()
    conversation._Conversation__stt.cancel_wait.assert_not_called()


def mic_conversation() -> Conversation:
    """A conversation in the player's turn, with the mic on"""
    conversation = event_added_conversation(multi_npc_game_master(config()))
    conversation._Conversation__stt = MagicMock()
    conversation._Conversation__stt.is_listening = True
    conversation._Conversation__mic_input = True
    conversation.context.npcs_in_conversation.get_player_character.return_value = SimpleNamespace(name="Dragonborn")
    return conversation


def test_an_event_picked_just_before_the_wait_starts_skips_the_wait():
    conversation = mic_conversation()
    conversation.stop_waiting_for_player() # The pick arrived while the player input request was still on its way
    assert conversation.process_player_input("") == (None, False, None)
    stt = conversation._Conversation__stt
    stt.get_latest_transcription.assert_not_called()
    stt.stop_listening.assert_called_once()


def test_the_event_request_forgets_a_pick_no_wait_used():
    conversation = mic_conversation()
    conversation.stop_waiting_for_player() # Picked while the NPCs were talking: no wait for speech followed
    conversation.forget_event_picked()
    conversation._Conversation__stt.get_latest_transcription.return_value = "Hello."
    conversation._Conversation__events_refresh_time = 0 # Returns right after the wait, asking for fresh in-game events
    assert conversation.process_player_input("") == ("Hello.", True, None) # The next player turn waits for speech again


def test_without_the_game_details_what_was_said_goes_back_to_the_game():
    conversation = mic_conversation()
    conversation._Conversation__stt.get_latest_transcription.return_value = "Hello."
    conversation._Conversation__events_refresh_time = 9999 # Said quickly: only the missing details send it back
    assert conversation.process_player_input("", details_follow=True) == ("Hello.", True, None)
    conversation._Conversation__stt.stop_listening.assert_called_once() # So the NPC gets to answer


def test_the_game_details_are_left_alone_when_they_follow():
    manager = conversation_event_manager([], None, {})
    talk = manager._GameStateManager__talk
    talk.process_player_input.return_value = ("Hello.", True, None)
    reply = manager.player_input({comm_consts.KEY_REQUESTTYPE_PLAYERINPUT: "", comm_consts.KEY_PLAYERINPUT_DETAILSFOLLOW: True})
    assert reply == {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REQUESTTYPE_TTS, comm_consts.KEY_TRANSCRIBE: "Hello."}
    talk.process_player_input.assert_called_once_with("", True)
    manager._GameStateManager__update_context.assert_not_called() # The details come with the next request


def conversation_event_manager(members: list, player, loaded: dict[int, SimpleNamespace]):
    """A GameStateManager with a running conversation, and only what conversation_event uses"""
    from src.game_manager import GameStateManager
    manager = event_check_manager(loaded)
    cfg = manager._GameStateManager__config
    cfg.multi_npc_game_master_mode = True
    cfg.game.base_game = "Skyrim"
    cfg.player_character_description = "Default description"
    for kind in ("conversation", "nearby", "random"): # Send the Conversation So Far and Include Me (Conversation page): off unless a test turns them on
        setattr(cfg, f"{kind}_event_send_history", False)
        setattr(cfg, f"{kind}_event_include_player", False)
    talk = MagicMock()
    talk.is_directed.return_value = False
    talk.context.npcs_in_conversation.get_player_character.return_value = player
    talk.context.get_characters_excluding_player.return_value.get_all_characters.return_value = members
    talk.context.location, talk.context.ingame_time, talk.context.weather = "Whiterun", 12, ""
    talk.event_history_lines.return_value = ["Hulda: Fresh bread!"]
    manager._GameStateManager__talk = talk
    manager._GameStateManager__event_cancel_count = 0
    manager._GameStateManager__chat_manager = MagicMock()
    manager._GameStateManager__apply_shout = MagicMock(return_value=True)
    manager._GameStateManager__update_context = MagicMock()
    return manager


def test_random_event_names_everyone_and_describes_the_nearest_in_or_out_of_the_conversation():
    hulda = SimpleNamespace(name="Hulda", ref_id="000065", is_player_character=False) # refid 101
    player = MagicMock()
    player.name = "Dragonborn"
    player.get_custom_character_value.return_value = "A tall Nord"
    ysolda = SimpleNamespace(name="Ysolda", ref_id="000067", is_player_character=False) # refid 103
    manager = conversation_event_manager([hulda], player, {103: ysolda})
    cfg = manager._GameStateManager__config
    cfg.random_event_include_player, cfg.random_event_send_history = True, True # Mantella's settings (Conversation page)
    request = {
        comm_consts.KEY_EVENT_KIND: "random",
        comm_consts.KEY_EVENT_HINT: "a theft",
        comm_consts.KEY_EVENT_NEARBY_REFIDS: [103, 101, 104],
        comm_consts.KEY_EVENT_NEARBY_NAMES: ["Ysolda", "Hulda", "Whiterun Guard"],
        comm_consts.KEY_EVENT_NEARBY_ACTORS: [{comm_consts.KEY_ACTOR_REFID: 103}],
        comm_consts.KEY_EVENT_MAXDESCRIBED: 2,
    }
    with patch("src.conversation.npc_event.generate_conversation_event", return_value="A thief runs past.") as generate, \
         patch("src.conversation.context.build_bios_and_summaries_text", return_value="BIOS") as bios:
        reply = manager.conversation_event(request)
    assert reply[comm_consts.KEY_EVENT_RESULT] is True
    assert reply[comm_consts.KEY_EVENT_TEXT] == "A thief runs past." # So the game can show it
    args = generate.call_args.args
    assert args[2] == "random"
    assert args[3] == ["Ysolda", "Hulda", "Whiterun Guard", "Dragonborn"]
    assert args[5] == "A tall Nord"
    assert args[10] == "a theft"
    assert args[11] == ["Hulda: Fresh bread!"]
    assert bios.call_args.args[0] == [ysolda, hulda] # The nearest 2, in the conversation or not
    text, kind, _settings, nearby, characters = manager._GameStateManager__talk.add_event.call_args.args
    assert (text, kind) == ("A thief runs past.", "random")
    assert [p.name for p in nearby] == ["Ysolda", "Whiterun Guard"] # Only who is not in the conversation can be brought in
    assert characters == {103: ysolda}


def test_an_event_while_directing_is_never_about_the_player():
    hulda = SimpleNamespace(name="Hulda", ref_id="000065", is_player_character=False) # refid 101
    player = MagicMock()
    player.name, player.ref_id = "Dragonborn", "000014" # refid 20
    manager = conversation_event_manager([hulda], player, {})
    manager._GameStateManager__talk.is_directed.return_value = True
    manager._GameStateManager__config.random_event_include_player = True
    request = {
        comm_consts.KEY_EVENT_KIND: "random",
        comm_consts.KEY_EVENT_NEARBY_REFIDS: [101, 20, 104],
        comm_consts.KEY_EVENT_NEARBY_NAMES: ["Hulda", "Dragonborn", "Whiterun Guard"],
    }
    with patch("src.conversation.npc_event.generate_conversation_event", return_value="A thief runs past.") as generate,          patch("src.conversation.context.build_bios_and_summaries_text", return_value="BIOS"):
        manager.conversation_event(request)
    assert generate.call_args.args[3] == ["Hulda", "Whiterun Guard"] # Include Player is ignored, and the player is never one of the people nearby
    assert generate.call_args.kwargs["hides_player"] is True
    _text, _kind, _settings, nearby, _characters = manager._GameStateManager__talk.add_event.call_args.args
    assert [p.name for p in nearby] == ["Whiterun Guard"]


def test_an_event_while_not_directing_can_be_about_the_player():
    player = MagicMock()
    player.name = "Dragonborn"
    manager = conversation_event_manager([], player, {})
    manager._GameStateManager__config.conversation_event_include_player = True
    request = {comm_consts.KEY_EVENT_KIND: "conversation"}
    with patch("src.conversation.npc_event.generate_conversation_event", return_value=None) as generate,          patch("src.conversation.context.build_bios_and_summaries_text", return_value="BIOS"):
        manager.conversation_event(request)
    assert generate.call_args.args[3] == ["Dragonborn"]
    assert generate.call_args.kwargs["hides_player"] is False


def test_an_event_while_shouting_takes_the_bios_shout_was_sent():
    player = MagicMock()
    player.name = "Dragonborn"
    ysolda = SimpleNamespace(name="Ysolda", ref_id="000067", is_player_character=False) # refid 103
    manager = conversation_event_manager([], player, {})
    talk = manager._GameStateManager__talk
    talk.context.shout = ShoutState()
    talk.context.shout.characters[103] = ysolda # Sent in full with an earlier request, so the game doesn't send her again
    request = {
        comm_consts.KEY_EVENT_KIND: "nearby",
        comm_consts.KEY_EVENT_NEARBY_REFIDS: [103],
        comm_consts.KEY_EVENT_NEARBY_NAMES: ["Ysolda"],
        comm_consts.KEY_EVENT_MAXDESCRIBED: 6,
    }
    with patch("src.conversation.npc_event.generate_conversation_event", return_value="A thief runs past.") as generate, \
         patch("src.conversation.context.build_bios_and_summaries_text", return_value="BIOS") as bios:
        manager.conversation_event(request)
    assert generate.call_args.args[3] == ["Ysolda"]
    assert bios.call_args.args[0] == [ysolda]
    talk.forget_event_picked.assert_called_once()


def test_conversation_event_names_the_people_in_the_conversation_without_the_player_or_history_when_off():
    hulda = SimpleNamespace(name="Hulda", ref_id="000065", is_player_character=False) # refid 101
    player = MagicMock()
    player.name = "Dragonborn"
    player.get_custom_character_value.return_value = None
    manager = conversation_event_manager([hulda], player, {})
    cfg = manager._GameStateManager__config
    cfg.conversation_event_include_player, cfg.conversation_event_send_history = False, False
    request = {comm_consts.KEY_EVENT_KIND: "conversation"}
    with patch("src.conversation.npc_event.generate_conversation_event", return_value=None) as generate, \
         patch("src.conversation.context.build_bios_and_summaries_text", return_value="BIOS") as bios:
        reply = manager.conversation_event(request)
    assert reply[comm_consts.KEY_EVENT_RESULT] is False # Nothing happened
    args = generate.call_args.args
    assert args[3] == ["Hulda"]
    assert args[5] == "Default description"
    assert args[11] == []
    assert bios.call_args.args[0] == [hulda]
    manager._GameStateManager__talk.add_event.assert_not_called()


def test_events_need_game_master_mode():
    manager = conversation_event_manager([], MagicMock(), {})
    manager._GameStateManager__config.multi_npc_game_master_mode = False
    with patch("src.conversation.npc_event.generate_conversation_event") as generate:
        reply = manager.conversation_event({comm_consts.KEY_EVENT_KIND: "conversation"})
    assert reply[comm_consts.KEY_EVENT_RESULT] is False and reply[comm_consts.KEY_GAMEMASTERMODE] is False
    generate.assert_not_called()


def test_events_need_a_game_master_model():
    manager = conversation_event_manager([], MagicMock(), {})
    manager._GameStateManager__chat_manager.game_master_client = None
    manager._GameStateManager__get_npc_initiative_client = MagicMock(return_value=None)
    with patch("src.conversation.npc_event.generate_conversation_event") as generate:
        reply = manager.conversation_event({comm_consts.KEY_EVENT_KIND: "conversation"})
    assert reply[comm_consts.KEY_EVENT_RESULT] is False
    generate.assert_not_called()


def test_default_event_generator_prompts_are_valid_and_ask_for_the_hint():
    from src.config.definitions.prompt_definitions import PromptDefinitions as P
    npc_event_prompt = P.get_skyrim_npc_event_generator_prompt_config_value().value
    assert "{hint}" in npc_event_prompt
    assert P.PromptChecker(P.ALLOWED_PROMPT_VARIABLES_NPC_EVENT_GENERATOR).apply_constraint(npc_event_prompt).is_success
    prompt = P.get_skyrim_conversation_event_generator_prompt_config_value().value
    assert P.PromptChecker(P.ALLOWED_PROMPT_VARIABLES_CONVERSATION_EVENT_GENERATOR, "skyrim_conversation_event_generator_prompt").apply_constraint(prompt).is_success
    for kind in ("talk_event", "nearby_event", "random_event"):
        assert "{hint}" in P.apply_prompt_conditions("skyrim_conversation_event_generator_prompt", prompt, {kind}), kind


def test_ending_the_conversation_cancels_the_event_being_generated():
    manager = conversation_event_manager([], MagicMock(), {})
    def generate(*args, **kwargs):
        manager.interrupt_conversation({comm_consts.KEY_EVENT_CANCEL: True}) # The player pressed End meanwhile
        return "E" if not args[-1]() else None
    with patch("src.conversation.npc_event.generate_conversation_event", side_effect=generate), \
         patch("src.conversation.context.build_bios_and_summaries_text", return_value=""):
        reply = manager.conversation_event({comm_consts.KEY_EVENT_KIND: "conversation"})
    assert reply[comm_consts.KEY_EVENT_RESULT] is False
    manager._GameStateManager__talk.add_event.assert_not_called()


def test_picking_an_event_stops_the_npcs_and_a_wait_for_speech():
    manager = conversation_event_manager([], MagicMock(), {})
    manager.interrupt_conversation({comm_consts.KEY_EVENT_KIND: "random"})
    talk = manager._GameStateManager__talk
    talk.request_interrupt.assert_called_once()
    talk.stop_waiting_for_player.assert_called_once()
