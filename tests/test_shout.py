import logging
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src import utils
from src.config.definitions.game_definitions import GameEnum
from src.conversation.bystanders import (EventPerson, ShoutState, NEWCOMERS_REMEMBER_EVERYTHING, NEWCOMERS_REMEMBER_LAST_LINE,
                                         NEWCOMERS_REMEMBER_SINCE_SHOUT)
from src.conversation.action import Action
from src.conversation.conversation import Conversation
from src.conversation.conversation_type import multi_npc_game_master, pc_to_npc, radiant, shout
from src.conversation.game_master import GameMaster, GameMasterTurns, is_player_line, messages_heard_by
from src.http.communication_constants import communication_constants as comm_consts
from src.llm.message_thread import message_thread
from src.llm.messages import AssistantMessage, UserMessage, join_message, leave_message, nearby_join_message, overheard_message
from src.llm.sentence import Sentence
from src.llm.sentence_content import SentenceContent, SentenceTypeEnum
from src.llm.sentence_queue import SentenceQueue
from src.config.definitions.conversation_definitions import MAX_NPC_TURNS_KINDS, max_npc_turns_identifier

YSOLDA = EventPerson("Ysolda", 103, "000103")
GUARD = EventPerson("Whiterun Guard", 104, "000104")


def config(**overrides):
    cfg = MagicMock()
    cfg.prompt_profile_manager.resolve_prompt.side_effect = lambda _prompt_type, text: text
    cfg.game_master_use_decision_model = False
    cfg.game_master_history_message_count = 0
    cfg.game_master_include_turn_thoughts = False
    for each_kind in MAX_NPC_TURNS_KINDS: # Each kind of conversation has its own
        setattr(cfg, max_npc_turns_identifier(each_kind), 3)
    cfg.multi_npc_game_master_mode = False
    cfg.multi_conversation_director_mode = False
    cfg.llm_response_timeout = 10
    cfg.shout_decision_nearby_criteria = "{name} was addressed."
    cfg.shout_decision_addressed_criteria = "{speaker} spoke to {name}."
    cfg.shout_decision_not_addressed_criteria = "{speaker} did not speak to {name}."
    cfg.shout_decision_event_part_criteria = "{name} is part of it."
    cfg.shout_decision_event_not_part_criteria = "{name} only watched."
    cfg.shout_group_answer_note = "{name} answers {speaker} with {names}."
    cfg.game_master_addressed_threshold = 0.5
    for name, value in overrides.items():
        setattr(cfg, name, value)
    return cfg


def shout_state(**overrides) -> ShoutState:
    values = dict(nearby=[YSOLDA, GUARD])
    values.update(overrides)
    return ShoutState(**values)


def npc(name: str, ref_id: str = ""):
    return SimpleNamespace(name=name, ref_id=ref_id or name.lower(), is_player_character=False)


PLAYER = SimpleNamespace(name="Dragonborn", ref_id="000014", is_player_character=True)
HULDA_NPC = npc("Hulda", "000101")
YSOLDA_NPC = npc("Ysolda", YSOLDA.ref_id)


# Limits

def test_nearby_people_are_offered_when_every_limit_allows_it():
    assert shout_state().nearby_choices(config(), ["Hulda", "Dragonborn"], 1) == [YSOLDA, GUARD]


def test_nobody_is_offered_when_nobody_is_nearby():
    assert shout_state(nearby=[]).nearby_choices(config(), ["Hulda"], 1) == []


def test_the_others_nearby_are_offered_right_after_someone_joined():
    state = shout_state(pending_add=YSOLDA)
    assert state.on_npc_joined(YSOLDA.ref_id)
    assert state.nearby_choices(config(), ["Hulda", "Ysolda"], 2) == [GUARD]


def test_most_people_counts_the_player():
    assert shout_state(max_people=3).nearby_choices(config(), ["Hulda", "Mikael"], 2) == []
    assert shout_state(max_people=4).nearby_choices(config(), ["Hulda", "Mikael"], 2) == [YSOLDA, GUARD]


def test_a_nearby_event_lets_people_in_past_most_people_only_for_the_reaction_to_it():
    state = shout_state(max_people=3, event_ignores_max_people=True)
    assert state.nearby_choices(config(), ["Hulda", "Mikael"], 2, after_event=True) == [YSOLDA, GUARD]
    assert state.free_places(2, after_event=True) == 2
    assert state.nearby_choices(config(), ["Hulda", "Mikael"], 2) == [] # Afterwards the limit applies again
    assert state.free_places(2) == 0


def test_a_conversation_event_keeps_most_people():
    state = shout_state(max_people=3, event_ignores_max_people=False)
    assert state.nearby_choices(config(), ["Hulda", "Mikael"], 2, after_event=True) == []


def test_nobody_more_is_offered_while_a_pick_waits_to_be_added():
    assert shout_state(pending_add=YSOLDA).nearby_choices(config(), ["Hulda"], 1) == []


def test_settings_from_the_game_are_applied_and_missing_ones_kept():
    state = shout_state()
    state.apply_settings({"max_people": None, "max_described": 2})
    assert (state.max_people, state.max_described) == (6, 2)


# Picks that join or can't be added

def test_the_pick_who_joins_speaks_next():
    state = shout_state(pending_add=YSOLDA, answering_pick=True)
    assert state.on_npc_joined(YSOLDA.ref_id)
    assert state.forced_speaker_ref_id == YSOLDA.ref_id
    assert state.pending_add is None and not state.answering_pick


def test_someone_the_player_adds_is_not_a_shout_join():
    state = shout_state(pending_add=YSOLDA)
    assert not state.on_npc_joined(GUARD.ref_id)
    assert state.pending_add is YSOLDA and not state.forced_speaker_ref_id


def test_an_answer_that_could_not_be_added_starts_the_turn_again():
    state = shout_state(pending_add=YSOLDA, answering_pick=True)
    assert state.apply_add_failed(YSOLDA.refid)
    assert state.answer_again and state.pending_add is None


def test_a_pick_between_npc_lines_that_could_not_be_added_gives_the_player_the_turn():
    state = shout_state(pending_add=YSOLDA, answering_pick=False)
    assert state.apply_add_failed(YSOLDA.refid)
    assert not state.answer_again


def test_everyone_asked_for_joins_in_one_step_and_the_first_of_them_speaks_next():
    state = shout_state(answering_pick=True)
    state.request_adds([YSOLDA, GUARD])
    assert state.pending_adds() == [YSOLDA, GUARD]
    assert state.on_npc_joined(GUARD.ref_id) and state.on_npc_joined(YSOLDA.ref_id) # In whatever order the game lists them
    assert state.forced_speaker_ref_id == YSOLDA.ref_id
    assert state.pending_adds() == [] and not state.answering_pick


def test_someone_who_cant_join_is_skipped_and_the_other_speaks_next():
    state = shout_state(answering_pick=True)
    state.request_adds([YSOLDA, GUARD])
    state.add_still_to_answer([(YSOLDA.ref_id, YSOLDA.name), (GUARD.ref_id, GUARD.name)], "Dragonborn", True)
    assert state.apply_add_failed(YSOLDA.refid, "in a scene")
    assert state.pending_adds() == [GUARD] and not state.answer_again
    assert list(state.still_to_answer) == [GUARD.ref_id] # Ysolda no longer has to answer
    assert state.on_npc_joined(GUARD.ref_id)
    assert state.forced_speaker_ref_id == GUARD.ref_id


def test_the_turn_starts_again_only_when_nobody_asked_for_could_join():
    state = shout_state(answering_pick=True)
    state.request_adds([YSOLDA, GUARD])
    state.apply_add_failed(YSOLDA.refid)
    assert not state.answer_again
    state.apply_add_failed(GUARD.refid)
    assert state.answer_again and not state.pending_add

    state = shout_state(answering_pick=True, answer_first_ref_id=HULDA_NPC.ref_id) # Hulda, in the conversation, answers first anyway
    state.request_adds([YSOLDA])
    state.apply_add_failed(YSOLDA.refid)
    assert not state.answer_again


def test_the_players_next_line_forgets_who_still_had_to_join_or_answer():
    state = shout_state(answer_first_ref_id=HULDA_NPC.ref_id)
    state.request_adds([YSOLDA, GUARD])
    state.add_still_to_answer([(YSOLDA.ref_id, YSOLDA.name)], "Dragonborn", True)
    state.clear_pick()
    assert state.pending_adds() == [] and not state.still_to_answer and not state.answer_first_ref_id


def test_at_the_reply_limit_only_the_people_an_npc_spoke_to_are_dropped():
    state = shout_state()
    state.add_still_to_answer([(YSOLDA.ref_id, YSOLDA.name)], "Dragonborn", True)
    state.add_still_to_answer([(GUARD.ref_id, GUARD.name), (YSOLDA.ref_id, YSOLDA.name)], "Hulda", False)
    assert state.drop_unprotected_answers() == [GUARD.name]
    assert list(state.still_to_answer) == [YSOLDA.ref_id] # The player spoke to her first, so she still answers


def test_only_a_line_aimed_at_several_people_names_the_others():
    state = shout_state()
    state.add_still_to_answer([(YSOLDA.ref_id, YSOLDA.name)], "Dragonborn", True)
    assert state.take_still_to_answer(YSOLDA.ref_id).others == []
    state.add_still_to_answer([(YSOLDA.ref_id, YSOLDA.name), (GUARD.ref_id, GUARD.name)], "Dragonborn", True)
    assert state.take_still_to_answer(YSOLDA.ref_id).others == [GUARD.name]
    assert state.take_still_to_answer(YSOLDA.ref_id) is None # They answer once


def test_the_reason_the_game_could_not_add_someone_is_logged(caplog):
    state = shout_state(pending_add=YSOLDA)
    with caplog.at_level(logging.DEBUG):
        assert state.apply_add_failed(YSOLDA.refid, "in a scene")
    assert f"{YSOLDA.name} could not be added to the conversation: in a scene" in caplog.text


def test_someone_who_could_not_be_added_is_left_out_until_they_drop_off_the_list():
    state = shout_state(pending_add=YSOLDA)
    state.apply_add_failed(YSOLDA.refid)
    state.update_nearby([YSOLDA, GUARD])
    assert state.nearby == [GUARD]
    state.update_nearby([GUARD]) # Ysolda walked off
    state.update_nearby([YSOLDA, GUARD]) # And came back
    assert state.nearby == [YSOLDA, GUARD]


def test_ref_id_from_the_game_refid_ignores_the_load_order():
    assert utils.mask_load_order(utils.convert_to_skyrim_hex_format(str(0x0A01B2C3))) == "01B2C3"
    assert utils.mask_load_order(utils.convert_to_skyrim_hex_format(str(0xFE000803 - 2**32))) == "000803" # FE000803 from a light plugin


# Newcomer memory

def thread_with_lines(cfg, *lines: str) -> message_thread:
    thread = message_thread(cfg, "System prompt")
    for line in lines:
        thread.add_message(UserMessage(cfg, line, "Dragonborn", False))
    return thread


def note_position(thread: message_thread) -> list[str]:
    return [m.text if isinstance(m, UserMessage) else type(m).__name__ for m in thread.get_persistent_messages()]


def test_overheard_note_goes_after_the_marked_message():
    cfg = config()
    thread = thread_with_lines(cfg, "Quiet.")
    assert thread.mark_last_persistent_message(1)
    thread.add_message(UserMessage(cfg, "GUARDS!", "Dragonborn", False))
    thread.insert_overheard_message(overheard_message(YSOLDA_NPC, cfg), 1)
    assert note_position(thread) == ["Quiet.", "overheard_message", "GUARDS!"]


def test_the_mark_survives_a_reload_of_the_thread():
    """Turning Shout on changes the conversation type, which reloads the thread with copies of every message"""
    cfg = config()
    thread = thread_with_lines(cfg, "Quiet.")
    thread.mark_last_persistent_message(1)
    thread.reload_message_thread("New prompt", lambda _messages, _percent: False, 1.0)
    thread.add_message(UserMessage(cfg, "GUARDS!", "Dragonborn", False))
    thread.insert_overheard_message(overheard_message(YSOLDA_NPC, cfg), 1)
    assert note_position(thread) == ["Quiet.", "overheard_message", "GUARDS!"]


def test_system_generated_messages_are_not_marked():
    """Some prompt changes remove system generated messages, which would take the mark with them"""
    cfg = config()
    thread = thread_with_lines(cfg, "Quiet.")
    thread.add_message(UserMessage(cfg, "Hello there.", "Dragonborn", True))
    thread.mark_last_persistent_message(1)
    marked = [m.text for m in thread.get_persistent_messages() if getattr(m, "shout_marker", None) == 1]
    assert marked == ["Quiet."]


def test_without_the_mark_the_note_goes_before_everything_that_is_left():
    cfg = config()
    thread = message_thread(cfg, "System prompt")
    assert not thread.mark_last_persistent_message(1) # Nothing was said yet
    thread.add_message(UserMessage(cfg, "GUARDS!", "Dragonborn", False))
    thread.insert_overheard_message(overheard_message(YSOLDA_NPC, cfg), None)
    assert note_position(thread) == ["overheard_message", "GUARDS!"]
    assert thread.get_openai_messages()[0]["role"] == "system"

    thread = thread_with_lines(cfg, "Left after shortening.")
    thread.insert_overheard_message(overheard_message(YSOLDA_NPC, cfg), 7) # The marked message was dropped
    assert note_position(thread) == ["overheard_message", "Left after shortening."]


def test_someone_who_left_after_shouting_started_hears_only_since_they_left():
    cfg = config()
    thread = thread_with_lines(cfg, "Quiet.")
    thread.mark_last_persistent_message(1)
    thread.add_message(overheard_message(YSOLDA_NPC, cfg))
    thread.add_message(UserMessage(cfg, "Thanks, guard.", "Dragonborn", False))
    thread.add_message(leave_message(YSOLDA_NPC, cfg))
    thread.add_message(UserMessage(cfg, "Ysolda, come back!", "Dragonborn", False))
    thread.insert_overheard_message(overheard_message(YSOLDA_NPC, cfg), 1)
    assert note_position(thread) == ["Quiet.", "overheard_message", "Thanks, guard.", "leave_message", "overheard_message", "Ysolda, come back!"]
    heard = messages_heard_by(thread.get_persistent_messages(), YSOLDA_NPC)
    assert heard[-1].text == "Ysolda, come back!"


def test_newcomer_hears_everything_after_the_overheard_note():
    cfg = config()
    quiet = UserMessage(cfg, "A quiet word.", "Dragonborn", False)
    loud = UserMessage(cfg, "GUARDS!", "Dragonborn", False)
    history = [quiet, overheard_message(YSOLDA_NPC, cfg), loud]
    assert messages_heard_by(history, YSOLDA_NPC)[1:] == [loud]
    assert quiet not in messages_heard_by(history, YSOLDA_NPC)


def npc_reply(cfg, speaker, text: str) -> AssistantMessage:
    message = AssistantMessage(cfg)
    message.add_sentence(Sentence(SentenceContent(speaker, text, SentenceTypeEnum.SPEECH, False), "", 1.0))
    return message


def test_last_line_choice_puts_the_note_before_the_players_last_line():
    cfg = config()
    thread = thread_with_lines(cfg, "Quiet.")
    thread.mark_last_persistent_message(1)
    thread.add_message(UserMessage(cfg, "GUARDS!", "Dragonborn", False))
    thread.add_message(npc_reply(cfg, HULDA_NPC, "Calm down."))
    thread.add_message(UserMessage(cfg, "Is there a healer here?", "Dragonborn", False))
    thread.add_message(npc_reply(cfg, HULDA_NPC, "Try the temple."))
    thread.insert_overheard_message(overheard_message(YSOLDA_NPC, cfg), 1, is_player_line)
    assert note_position(thread) == ["Quiet.", "GUARDS!", "AssistantMessage", "overheard_message", "Is there a healer here?", "AssistantMessage"]


def test_last_line_choice_never_goes_before_shouting_started():
    cfg = config()
    thread = thread_with_lines(cfg, "Quiet.")
    thread.mark_last_persistent_message(1)
    thread.add_message(npc_reply(cfg, HULDA_NPC, "What was that?")) # Nothing said by the player since shouting started
    thread.insert_overheard_message(overheard_message(YSOLDA_NPC, cfg), 1, is_player_line)
    assert note_position(thread) == ["Quiet.", "overheard_message", "AssistantMessage"]


def test_last_line_choice_skips_lines_with_only_events_and_narration():
    cfg = config()
    thread = thread_with_lines(cfg, "Quiet.")
    thread.mark_last_persistent_message(1)
    thread.add_message(UserMessage(cfg, "GUARDS!", "Dragonborn", False))
    thread.add_message(UserMessage(cfg, "The scene continues.", "", True))
    thread.insert_overheard_message(overheard_message(YSOLDA_NPC, cfg), 1, is_player_line)
    assert note_position(thread) == ["Quiet.", "overheard_message", "GUARDS!", "The scene continues."]


def test_everything_choice_puts_the_note_before_the_whole_conversation():
    cfg = config()
    thread = thread_with_lines(cfg, "Quiet.")
    thread.mark_last_persistent_message(1)
    thread.add_message(UserMessage(cfg, "GUARDS!", "Dragonborn", False))
    thread.insert_overheard_message(overheard_message(YSOLDA_NPC, cfg), None)
    assert note_position(thread) == ["overheard_message", "Quiet.", "GUARDS!"]


def test_newcomers_remember_setting_is_applied_and_bad_values_fall_back():
    state = shout_state()
    assert state.newcomers_remember == NEWCOMERS_REMEMBER_SINCE_SHOUT
    state.apply_settings({"newcomers_remember": NEWCOMERS_REMEMBER_LAST_LINE})
    assert state.newcomers_remember == NEWCOMERS_REMEMBER_LAST_LINE
    state.apply_settings({"newcomers_remember": None})
    assert state.newcomers_remember == NEWCOMERS_REMEMBER_LAST_LINE
    state.apply_settings({"newcomers_remember": 9})
    assert state.newcomers_remember == NEWCOMERS_REMEMBER_SINCE_SHOUT


def test_overheard_note_says_they_can_hear_and_counts_as_a_join():
    message = overheard_message(YSOLDA_NPC, config())
    assert isinstance(message, join_message)
    assert message.text == "*Ysolda is nearby now and can hear the conversation*"


@pytest.mark.parametrize("already_there, expected", [
    ([], "*Ysolda joins the conversation*"),
    (["Yuji"], "*Ysolda joins the conversation that had already started*"),
    (["Yuji", "Camilla Valerius", "Sven"], "*Ysolda joins the conversation that had already started*"),
])
def test_nearby_join_note_says_the_conversation_had_already_started_without_naming_anyone(already_there, expected):
    message = nearby_join_message(YSOLDA_NPC, config(), already_there)
    assert isinstance(message, join_message)
    assert message.text == expected


@pytest.mark.parametrize("already_there", [[], ["Yuji"], ["Yuji", "Tamda", "Sven"]])
def test_join_note_of_someone_reacting_to_an_event_does_not_have_them_walk_over(already_there):
    message = nearby_join_message(YSOLDA_NPC, config(), already_there, reacts_to_event=True)
    assert isinstance(message, join_message)
    assert message.text == "*Ysolda reacts to what just happened*"


# Conversation

def shout_conversation(state: ShoutState | None, has_player: bool = True) -> Conversation:
    context = MagicMock()
    context.npc_event = None
    context.shout = state
    context.started_by_npc = False
    context.config = config()
    context.config.end_conversation_keyword = "goodbye"
    context.npcs_in_conversation.contains_player_character.return_value = has_player
    context.npcs_in_conversation.active_character_count.return_value = 2
    conversation = Conversation(context, MagicMock(), MagicMock(), MagicMock(), None, False, False)
    conversation._Conversation__conversation_type = shout(context.config)
    conversation._Conversation__game_master_turns = MagicMock()
    conversation._Conversation__game_master_turns.play_next_reply.return_value = False
    conversation._Conversation__llm_client.is_too_long.return_value = False
    return conversation


def test_shout_type_is_used_with_game_master_mode_off_and_one_npc():
    conversation = shout_conversation(shout_state())
    assert isinstance(conversation._Conversation__select_conversation_type(conversation.context.config), shout)


def test_shout_type_needs_the_player_and_a_game_master_client():
    conversation = shout_conversation(shout_state(), has_player=False)
    assert isinstance(conversation._Conversation__select_conversation_type(conversation.context.config), radiant)
    conversation = shout_conversation(shout_state())
    conversation.output_manager.game_master_client = None
    assert isinstance(conversation._Conversation__select_conversation_type(conversation.context.config), pc_to_npc)


def test_private_conversation_is_not_a_shout_conversation():
    conversation = shout_conversation(None)
    assert isinstance(conversation._Conversation__select_conversation_type(conversation.context.config), pc_to_npc)


@pytest.mark.parametrize("director", [False, True])
def test_shout_type_is_a_game_master_type_that_follows_director_mode(director):
    cfg = config(multi_conversation_director_mode=director)
    cfg.game.base_game = GameEnum.SKYRIM
    shout_type = shout(cfg)
    assert isinstance(shout_type, multi_npc_game_master)
    assert shout_type.is_director_mode() is director
    assert not shout(cfg, can_direct=False).is_director_mode() # A conversation an NPC started is never directed


@pytest.mark.parametrize("state, expected, starts_turn", [
    (shout_state(pending_add=YSOLDA), comm_consts.KEY_REPLYTYPE_SHOUT_ADDACTOR, False),
    (shout_state(forced_speaker_ref_id=YSOLDA.ref_id), comm_consts.KEY_REPLYTYPE_NPCTALK, True),
    (shout_state(answer_again=True), comm_consts.KEY_REPLYTYPE_NPCTALK, True),
    (shout_state(), comm_consts.KEY_REPLYTYPE_PLAYERTALK, False),
])
def test_what_follows_the_npcs_turn(state, expected, starts_turn):
    conversation = shout_conversation(state)
    with patch.object(conversation, "_Conversation__start_generating_npc_sentences") as start, \
         patch.object(conversation, "retrieve_sentence_from_queue", return_value=None):
        assert conversation.continue_conversation() == (expected, None)
    assert start.called == starts_turn


def test_turning_shout_on_marks_where_it_started():
    cfg = config()
    conversation = shout_conversation(None)
    thread = thread_with_lines(cfg, "Quiet.")
    conversation._Conversation__messages = thread
    conversation.start_shout({"max_people": 4})
    state = conversation.context.shout
    assert isinstance(state, ShoutState) and state.max_people == 4
    assert state.start_marker is not None
    assert getattr(thread.get_persistent_messages()[-1], "shout_marker") == state.start_marker
    assert conversation.context.have_actors_changed


def test_shout_joiner_gets_the_overheard_note_where_shouting_started():
    cfg = config()
    conversation = shout_conversation(None)
    thread = thread_with_lines(cfg, "A quiet word.")
    conversation._Conversation__messages = thread
    conversation.start_shout(None)
    state = conversation.context.shout
    state.pending_add = YSOLDA
    thread.reload_message_thread("Shout prompt", lambda _messages, _percent: False, 1.0) # As the switch to the Shout type does
    thread.add_message(UserMessage(cfg, "GUARDS!", "Dragonborn", False))
    conversation.context.add_or_update_characters.return_value = SimpleNamespace(added_npcs=[YSOLDA_NPC], removed_npcs=[])
    conversation.context.npcs_in_conversation.get_all_names.return_value = ["Dragonborn", HULDA_NPC.name, YSOLDA_NPC.name]
    conversation.add_or_update_character([PLAYER, HULDA_NPC, YSOLDA_NPC])
    assert note_position(thread) == ["A quiet word.", "overheard_message", "GUARDS!", "nearby_join_message"]
    joined = thread.get_persistent_messages()[-1]
    assert joined.text == f"*{YSOLDA_NPC.name} joins the conversation that had already started*"
    assert state.forced_speaker_ref_id == YSOLDA.ref_id


@pytest.mark.parametrize("someone_spoke_since, expected", [
    (False, f"*{YSOLDA_NPC.name} reacts to what just happened*"),
    (True, f"*{YSOLDA_NPC.name} joins the conversation that had already started*"),
])
def test_someone_who_joins_right_after_an_event_speaks_up_about_it(someone_spoke_since, expected):
    cfg = config()
    conversation = shout_conversation(None)
    thread = thread_with_lines(cfg, "A quiet word.")
    conversation._Conversation__messages = thread
    conversation.context.get_context_ingame_events.return_value = []
    conversation.context.config.max_count_events = 5
    with patch.object(conversation, "_Conversation__update_conversation_type"), \
         patch.object(conversation, "_Conversation__start_generating_npc_sentences"):
        conversation.add_event("Sven and Lucia start a brawl.", "nearby", None, [YSOLDA], {})
    thread.reload_message_thread("Shout prompt", lambda _messages, _percent: False, 1.0) # As the switch to the Shout type does: every message is copied
    if someone_spoke_since:
        thread.add_message(npc_reply(cfg, HULDA_NPC, "Not again."))
    conversation.context.shout.pending_add = YSOLDA
    conversation.context.add_or_update_characters.return_value = SimpleNamespace(added_npcs=[YSOLDA_NPC], removed_npcs=[])
    conversation.context.npcs_in_conversation.get_all_names.return_value = ["Dragonborn", HULDA_NPC.name, YSOLDA_NPC.name]
    conversation.add_or_update_character([PLAYER, HULDA_NPC, YSOLDA_NPC])
    assert thread.get_persistent_messages()[-1].text == expected


@pytest.mark.parametrize("remember, expected", [
    (NEWCOMERS_REMEMBER_SINCE_SHOUT, ["A quiet word.", "overheard_message", "GUARDS!", "AssistantMessage", "A healer?", "nearby_join_message"]),
    (NEWCOMERS_REMEMBER_LAST_LINE, ["A quiet word.", "GUARDS!", "AssistantMessage", "overheard_message", "A healer?", "nearby_join_message"]),
    (NEWCOMERS_REMEMBER_EVERYTHING, ["overheard_message", "A quiet word.", "GUARDS!", "AssistantMessage", "A healer?", "nearby_join_message"]),
])
def test_shout_joiner_note_follows_newcomers_remember(remember, expected):
    cfg = config()
    conversation = shout_conversation(None)
    thread = thread_with_lines(cfg, "A quiet word.")
    conversation._Conversation__messages = thread
    conversation.start_shout({"newcomers_remember": remember})
    state = conversation.context.shout
    state.pending_add = YSOLDA
    thread.add_message(UserMessage(cfg, "GUARDS!", "Dragonborn", False))
    thread.add_message(npc_reply(cfg, HULDA_NPC, "Calm down."))
    thread.add_message(UserMessage(cfg, "A healer?", "Dragonborn", False))
    conversation.context.add_or_update_characters.return_value = SimpleNamespace(added_npcs=[YSOLDA_NPC], removed_npcs=[])
    conversation.add_or_update_character([PLAYER, HULDA_NPC, YSOLDA_NPC])
    assert note_position(thread) == expected


def test_someone_the_player_adds_while_shouting_gets_a_normal_join_note():
    cfg = config()
    conversation = shout_conversation(None)
    thread = thread_with_lines(cfg, "Hello.")
    conversation._Conversation__messages = thread
    conversation.start_shout(None)
    conversation.context.add_or_update_characters.return_value = SimpleNamespace(added_npcs=[YSOLDA_NPC], removed_npcs=[])
    conversation.add_or_update_character([PLAYER, YSOLDA_NPC])
    last = thread.get_persistent_messages()[-1]
    assert type(last) is join_message


def test_switching_to_private_tells_who_is_left_once_the_actors_are_updated():
    conversation = shout_conversation(shout_state())
    context = conversation.context
    context.npcs_in_conversation.get_player_character.return_value = PLAYER
    context.get_character_names_as_text.return_value = "Hulda"
    conversation.stop_shout()
    context.add_ingame_event.assert_not_called() # Who is left is only known after the game's updated list arrives
    conversation.update_context(None, 12, [], "", {})
    conversation.update_context(None, 12, [], "", {})
    context.add_ingame_event.assert_called_once_with(
        f"{PLAYER.name} is now speaking privately with Hulda.")
    context.get_character_names_as_text.assert_called_with(False)


@pytest.mark.parametrize("has_more_npcs", [False, True])
def test_npc_prompt_actions_are_the_shout_switches_however_many_npcs_there_are(has_more_npcs):
    follow = Action("mantella_npc_follow", "Follow", "Follow", "", "To follow, begin your response with '{key}:'.", False, False, True, False, False)
    wait = Action("mantella_npc_wait", "Wait", "Wait", "", "To wait, begin your response with '{key}:'.", False, False, True, True, False)
    shout_type = shout(config(all_actions=[follow, wait], actions_one_on_one=["Follow"], actions_shout=["Wait"]))
    context = MagicMock()
    context.npcs_in_conversation.contains_multiple_npcs.return_value = has_more_npcs
    shout_type.generate_npc_prompt(context, HULDA_NPC)
    actions = context.generate_system_message.call_args.args[1]
    assert [a.keyword for a in actions] == ["Wait"]


# Game Master

def llm_client(reply: str):
    client = MagicMock()
    client.token_limit = 32000
    client.get_count_tokens = lambda text: len(text.split())
    client.request_call.return_value = reply
    return client


def game_master(state: ShoutState, npcs, ai_client, cfg=None):
    context = MagicMock()
    context.config = cfg or config()
    context.npc_event = None
    context.shout = state
    context.get_characters_excluding_player.return_value.get_all_characters.return_value = npcs
    context.npcs_in_conversation.get_player_character.return_value = PLAYER
    context.get_character_names_as_text.return_value = "Hulda"
    conversation_type = MagicMock(spec=shout)
    conversation_type.generate_game_master_prompt.return_value = "You are the game master."
    conversation_type.is_director_mode.return_value = False
    return GameMaster(context, conversation_type, ai_client)


def test_game_master_can_pick_someone_nearby_to_answer():
    client = llm_client('{"next": "Whiterun Guard", "reason": "called for the guards"}')
    gm = game_master(shout_state(), [HULDA_NPC, npc("Mikael")], client)
    assert gm.choose_next_speaker(MagicMock(), [], False, allow_nearby=True) is GUARD
    assert "Choose one of: Hulda, Mikael, Ysolda, Whiterun Guard." in client.request_call.call_args.args[0].get_last_message().text


def test_one_npc_with_nobody_offered_needs_no_game_master_call():
    client = llm_client('{"next": "Hulda", "reason": "r"}')
    gm = game_master(shout_state(nearby=[]), [HULDA_NPC], client)
    assert gm.choose_next_speaker(MagicMock(), [], False, allow_nearby=True) is HULDA_NPC # Answers the player
    assert gm.choose_next_speaker(MagicMock(), [], True, allow_nearby=True) is None # Then the player speaks
    client.request_call.assert_not_called()


def test_game_master_prompt_names_only_the_people_it_may_pick():
    state = shout_state(max_people=3) # Most People in Conversation says no
    offers = []
    client = llm_client('{"next": "Hulda", "reason": "r"}')
    gm = game_master(state, [HULDA_NPC, npc("Mikael")], client)
    gm._GameMaster__conversation_type.generate_game_master_prompt.side_effect = lambda *_args: offers.append(state.nearby_text(config(), [])) or "GM"
    gm.choose_next_speaker(MagicMock(), [], True, allow_nearby=True)
    assert offers == ["nobody"]
    assert state.game_master_offer is None
    assert state.nearby_text(config(), []) == "Ysolda, Whiterun Guard" # The NPCs' prompts still name everyone nearby


def test_one_npc_asks_the_game_master_when_someone_nearby_is_offered():
    client = llm_client('{"next": "Hulda", "reason": "r"}')
    gm = game_master(shout_state(), [HULDA_NPC], client)
    assert gm.choose_next_speaker(MagicMock(), [], False, allow_nearby=True) is HULDA_NPC
    client.request_call.assert_called_once()


# Several people answer one line

def decision_client():
    client = llm_client("")
    client.service_name = "OpenRouter"
    client.model_name = "decider"
    client.api_key = "key"
    return client


def decision_config(**overrides):
    return config(game_master_use_decision_model=True, shout_decision_nobody_criteria="{player_name} spoke to nobody.",
                  game_master_decision_instructions="Who speaks next?", game_master_decision_npc_criteria="{name} speaks.",
                  game_master_decision_player_criteria="{player_name} speaks.", **overrides)


def decision_response(choice: str, probabilities: dict[str, float], yes: list[float] | None, status_code: int = 200):
    answers = {"next_speaker": {"choice": choice, "probabilities": probabilities}}
    for number, value in enumerate(yes or [], 1):
        answers[f"addressed_{number}"] = {"type": "noul", "noul": value}
    response = MagicMock(status_code=status_code, text="rejected")
    response.json.return_value = {"answers": answers}
    return response


def player_line(text: str):
    return [UserMessage(config(), text, PLAYER.name, False)]


def test_the_decision_model_is_asked_about_each_person_with_the_same_request():
    gm = game_master(shout_state(), [HULDA_NPC], decision_client(), decision_config())
    response = decision_response("Ysolda", {"Ysolda": 0.5, "Whiterun Guard": 0.4, "Hulda": 0.1}, [0.1, 0.9, 0.8])
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response) as post:
        first = gm.choose_next_speaker(MagicMock(), player_line("Hello Ysolda and guard!"), False, allow_nearby=True)
    questions = post.call_args.args[2]["questions"]
    assert list(questions) == ["next_speaker", "addressed_1", "addressed_2", "addressed_3"]
    assert questions["addressed_2"] == {"type": "noul", "instructions": "Was Dragonborn's latest line addressed to Ysolda?",
                                        "criteria": {"true": "Dragonborn spoke to Ysolda.", "false": "Dragonborn did not speak to Ysolda."}}
    assert post.call_count == 1
    assert first is YSOLDA and gm.aimed_at == [YSOLDA, GUARD]
    assert gm.is_line_protected and gm.line_speaker == "Dragonborn"


def test_the_game_master_keeps_its_own_pick_and_reports_who_was_spoken_to():
    """Whether someone spoken to speaks first instead is up to the talk settings (GameMasterTurns, see tests/test_answer_rules_flow.py)"""
    gm = game_master(shout_state(), [HULDA_NPC, npc("Mikael")], decision_client(), decision_config())
    response = decision_response("Mikael", {"Mikael": 0.6, "Whiterun Guard": 0.3, "Ysolda": 0.1}, [0.0, 0.0, 0.9, 0.95])
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response):
        first = gm.choose_next_speaker(MagicMock(), player_line("Thanks, you two."), False, allow_nearby=True)
    assert first is not None and first.name == "Mikael"
    assert gm.aimed_at == [YSOLDA, GUARD] and not gm.picked_player and gm.scores["Whiterun Guard"] == 0.3


def test_when_nobody_was_spoken_to_the_game_masters_pick_stands():
    gm = game_master(shout_state(), [HULDA_NPC, npc("Mikael")], decision_client(), decision_config())
    response = decision_response("Hulda", {"Hulda": 0.9}, [0.1, 0.2, 0.3, 0.49])
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response):
        assert gm.choose_next_speaker(MagicMock(), player_line("Hm."), False, allow_nearby=True) is HULDA_NPC
    assert gm.aimed_at == []


def test_nobody_answers_wins_when_saying_something_to_the_room():
    gm = game_master(shout_state(), [], decision_client(), decision_config())
    response = decision_response("Nobody", {"Nobody": 0.9}, [0.9, 0.9])
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response):
        assert gm.choose_next_speaker(MagicMock(), player_line("What a day."), False, allow_nearby=True) is None
    assert gm.aimed_at == []


def test_the_threshold_decides_who_counts_as_spoken_to():
    gm = game_master(shout_state(), [HULDA_NPC], decision_client(), decision_config(game_master_addressed_threshold=0.85))
    response = decision_response("Ysolda", {"Ysolda": 0.9}, [0.1, 0.9, 0.8])
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response):
        gm.choose_next_speaker(MagicMock(), player_line("Hello Ysolda and guard!"), False, allow_nearby=True)
    assert gm.aimed_at == [YSOLDA]


def test_a_line_is_only_asked_about_once():
    state = shout_state()
    gm = game_master(state, [HULDA_NPC], decision_client(), decision_config())
    history = player_line("Hello Ysolda and guard!")
    response = decision_response("Ysolda", {"Ysolda": 0.9}, [0.1, 0.9, 0.8])
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response) as post:
        gm.choose_next_speaker(MagicMock(), history, False, allow_nearby=True)
        gm.choose_next_speaker(MagicMock(), history, False, allow_nearby=True) # E.g. the turn starts again, as nobody could join
    assert list(post.call_args.args[2]["questions"]) == ["next_speaker"]
    assert gm.aimed_at == []


def test_a_service_that_rejects_the_yes_no_questions_is_asked_again_without_them(caplog):
    from src.conversation import game_master as game_master_module
    caplog.set_level(logging.DEBUG)
    state = shout_state()
    gm = game_master(state, [HULDA_NPC], decision_client(), decision_config())
    rejected = decision_response("", {}, None, status_code=400)
    accepted = decision_response("Ysolda", {"Ysolda": 0.9}, None)
    try:
        with patch("src.conversation.game_master.post_decision_with_retry", side_effect=[rejected, accepted, accepted]) as post:
            assert gm.choose_next_speaker(MagicMock(), player_line("Hello Ysolda and guard!"), False, allow_nearby=True) is YSOLDA
            assert list(post.call_args.args[2]["questions"]) == ["next_speaker"]
            assert gm.aimed_at == [] # One person answers, as before
            gm.choose_next_speaker(MagicMock(), player_line("Hello again, Ysolda and guard!"), False, allow_nearby=True)
            assert post.call_count == 3 # Not tried again
        assert "left out until Mantella restarts" in caplog.text
    finally:
        game_master_module.services_without_yes_no_questions.clear()


def test_answers_that_cant_be_read_leave_one_person_answering():
    gm = game_master(shout_state(), [HULDA_NPC], decision_client(), decision_config())
    response = decision_response("Ysolda", {"Ysolda": 0.9}, [0.9]) # Two answers are missing
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response):
        assert gm.choose_next_speaker(MagicMock(), player_line("Hello Ysolda and guard!"), False, allow_nearby=True) is YSOLDA
    assert gm.aimed_at == []


def test_after_an_event_the_question_is_who_is_part_of_it():
    gm = game_master(shout_state(), [HULDA_NPC], decision_client(), decision_config())
    event = UserMessage(config(), "", "", False)
    event.add_event(["Ysolda trips over the guard."])
    response = decision_response("Ysolda", {"Ysolda": 0.9}, [0.0, 0.9, 0.9])
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response) as post:
        gm.choose_next_speaker(MagicMock(), [event], True, allow_nearby=True, after_event=True)
    question = post.call_args.args[2]["questions"]["addressed_2"]
    assert question["instructions"] == "Is Ysolda part of what just happened (the last line, in brackets)?"
    assert question["criteria"] == {"true": "Ysolda is part of it.", "false": "Ysolda only watched."}
    assert gm.aimed_at == [YSOLDA, GUARD] and gm.is_line_protected and gm.line_speaker == ""


def full_conversation_event(event_ignores_max_people: bool, text: str = "Ysolda trips over the guard.", yes: list[float] | None = None,
                            max_people: int = 3, **settings) -> list[str]:
    """Hulda and Mikael fill Most People in Conversation (3, the player included). A decision model says Ysolda and the guard are part of the event.
    Who the latest line ends up aimed at"""
    state = shout_state(max_people=max_people, event_ignores_max_people=event_ignores_max_people, **settings)
    gm = game_master(state, [HULDA_NPC, npc("Mikael")], decision_client(), decision_config())
    event = UserMessage(config(), "", "", False)
    event.add_event([text])
    response = decision_response("Ysolda", {"Ysolda": 0.9}, yes or [0.0, 0.0, 0.9, 0.9])
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response):
        gm.choose_next_speaker(MagicMock(), [event], True, allow_nearby=True, after_event=True)
    return [person.name for person in gm.aimed_at]


def test_the_people_a_nearby_event_is_about_join_past_most_people():
    assert full_conversation_event(True) == ["Ysolda", "Whiterun Guard"]


def test_the_people_a_conversation_event_is_not_about_stay_out_once_full():
    assert full_conversation_event(False) == []


def test_most_people_answering_keeps_the_people_a_nearby_event_is_about_ahead_of_the_others():
    # Everyone is part of an event that names nobody, and only two may answer: the two people nearby, not Hulda and Mikael
    aimed = full_conversation_event(True, "A fight breaks out.", [0.9, 0.9, 0.9, 0.9], max_answering=2)
    assert sorted(aimed) == ["Whiterun Guard", "Ysolda"]


def test_joining_past_most_people_is_logged_only_when_it_happens(caplog):
    caplog.set_level(logging.DEBUG)
    full_conversation_event(True)
    assert "Game Master: Ysolda, Whiterun Guard join past Most People in Conversation (3), as the event is about them" in caplog.text
    caplog.clear()
    full_conversation_event(True, max_people=5) # Room for both
    assert "join past Most People in Conversation" not in caplog.text


def direction(text: str) -> list[UserMessage]:
    """A direction the player typed in Director Mode"""
    message = UserMessage(config(), text, PLAYER.name, False)
    message.is_director_message = True
    return [message]


def test_a_direction_is_asked_about_like_an_event_not_as_something_the_player_said():
    gm = game_master(shout_state(), [HULDA_NPC], decision_client(), decision_config())
    gm._GameMaster__conversation_type.is_director_mode.return_value = True
    response = decision_response("Ysolda", {"Ysolda": 0.9}, [0.0, 0.9, 0.9])
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response) as post:
        gm.choose_next_speaker(MagicMock(), direction("Ysolda drops a basket in front of the guard."), False, allow_nearby=True)
    question = post.call_args.args[2]["questions"]["addressed_2"]
    assert question["instructions"] == "Is Ysolda part of what just happened (the latest scene direction, the last line without a speaker)?"
    assert question["criteria"] == {"true": "Ysolda is part of it.", "false": "Ysolda only watched."}
    assert gm.aimed_at == [YSOLDA, GUARD] and gm.is_line_protected and gm.line_speaker == ""


def test_the_game_master_llm_lists_who_is_part_of_a_direction():
    client = llm_client('{"next": "Whiterun Guard", "also": ["Ysolda"], "reason": "both in it"}')
    gm = game_master(shout_state(), [HULDA_NPC], client)
    gm._GameMaster__conversation_type.is_director_mode.return_value = True
    assert gm.choose_next_speaker(MagicMock(), direction("Ysolda drops a basket in front of the guard."), False, allow_nearby=True) is GUARD
    request = client.request_call.call_args.args[0].get_last_message().text
    assert "for whom this is true: that person is part of it." in request


@pytest.mark.parametrize("director", [True, False])
def test_the_player_takes_a_place_in_the_conversation_only_when_not_directing(director):
    client = llm_client('{"next": "Mikael", "reason": "r"}')
    gm = game_master(shout_state(max_people=3), [HULDA_NPC, npc("Mikael")], client) # Two NPCs and the player: full, unless the player directs
    gm._GameMaster__conversation_type.is_director_mode.return_value = director
    gm.choose_next_speaker(MagicMock(), [npc_reply(config(), HULDA_NPC, "Who goes there?")], False, allow_nearby=True)
    request = client.request_call.call_args.args[0].get_last_message().text
    assert ("Ysolda" in request) is director


def test_a_direction_brings_in_the_people_it_is_about_past_most_people():
    client = llm_client('{"next": "Whiterun Guard", "also": ["Ysolda"], "reason": "both in it"}')
    gm = game_master(shout_state(max_people=2), [HULDA_NPC, npc("Mikael")], client) # Full even without the player
    gm._GameMaster__conversation_type.is_director_mode.return_value = True
    assert gm.choose_next_speaker(MagicMock(), direction("Ysolda drops a basket in front of the guard."), False, allow_nearby=True) is GUARD
    assert gm.aimed_at == [GUARD, YSOLDA]


def test_after_an_npc_line_a_directed_conversation_keeps_to_most_people():
    client = llm_client('{"next": "Mikael", "reason": "r"}')
    gm = game_master(shout_state(max_people=2), [HULDA_NPC, npc("Mikael")], client)
    gm._GameMaster__conversation_type.is_director_mode.return_value = True
    gm.choose_next_speaker(MagicMock(), [npc_reply(config(), HULDA_NPC, "Who goes there?")], False, allow_nearby=True)
    assert "Ysolda" not in client.request_call.call_args.args[0].get_last_message().text


def test_director_mode_with_one_npc_answers_once_then_the_player_directs():
    client = llm_client('{"next": "Hulda", "reason": "r"}')
    gm = game_master(shout_state(nearby=[]), [HULDA_NPC], client)
    gm._GameMaster__conversation_type.is_director_mode.return_value = True
    # Asked without the player (Director Mode): no Game Master call, and nobody again, so the NPC doesn't talk on and on
    assert gm.choose_next_speaker(MagicMock(), [], False, allow_nearby=True) is None
    assert not gm.picked_player
    client.request_call.assert_not_called()


def test_director_mode_with_one_npc_and_people_nearby_does_not_let_the_npc_talk_on_alone():
    client = llm_client('{"next": "Hulda", "reason": "she keeps going"}')
    gm = game_master(shout_state(), [HULDA_NPC], client)
    gm._GameMaster__conversation_type.is_director_mode.return_value = True
    # Hulda answers the direction: her pick stands
    assert gm.choose_next_speaker(MagicMock(), direction("A dog steals Hulda's ham."), False, allow_nearby=True) is HULDA_NPC
    # Hulda just spoke: picking her again ends the turn, so the player directs again
    assert gm.choose_next_speaker(MagicMock(), [npc_reply(config(), HULDA_NPC, "My ham!")], False, allow_nearby=True) is None
    assert not gm.picked_player


def test_director_mode_with_one_npc_still_lets_someone_nearby_join():
    client = llm_client('{"next": "Whiterun Guard", "reason": "she called him"}')
    gm = game_master(shout_state(), [HULDA_NPC], client)
    gm._GameMaster__conversation_type.is_director_mode.return_value = True
    assert gm.choose_next_speaker(MagicMock(), [npc_reply(config(), HULDA_NPC, "Guard! Stop that dog!")], False, allow_nearby=True) is GUARD


def test_director_mode_with_one_npc_and_no_shout_answers_once_too():
    client = llm_client('{"next": "Hulda", "reason": "r"}')
    gm = game_master(None, [HULDA_NPC], client)
    gm._GameMaster__conversation_type = MagicMock(spec=multi_npc_game_master)
    gm._GameMaster__conversation_type.is_director_mode.return_value = True
    assert gm.choose_next_speaker(MagicMock(), [], False) is None
    client.request_call.assert_not_called()


def test_switching_to_private_in_director_mode_does_not_name_the_player():
    conversation = shout_conversation(shout_state())
    context = conversation.context
    context.config.multi_conversation_director_mode = True
    context.config.game.base_game = GameEnum.SKYRIM
    context.started_by_npc = False
    context.npcs_in_conversation.get_player_character.return_value = PLAYER
    context.get_character_names_as_text.return_value = "Hulda"
    conversation.stop_shout()
    conversation.update_context(None, 12, [], "", {})
    context.add_ingame_event.assert_called_once_with("Nobody else joins the conversation now.")


def test_an_npcs_line_is_not_asked_about_its_speaker_and_is_not_protected():
    gm = game_master(shout_state(), [HULDA_NPC, npc("Mikael")], decision_client(), decision_config())
    response = decision_response("Mikael", {"Mikael": 0.9}, [0.9, 0.0, 0.0])
    history = [npc_reply(config(), HULDA_NPC, "Mikael, play something.")]
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response) as post:
        gm.choose_next_speaker(MagicMock(), history, True, allow_nearby=True)
    questions = post.call_args.args[2]["questions"]
    assert [q["instructions"] for name, q in questions.items() if name != "next_speaker"] == [
        "Was Hulda's latest line addressed to Mikael?", "Was Hulda's latest line addressed to Ysolda?", "Was Hulda's latest line addressed to Whiterun Guard?"]
    assert not gm.is_line_protected and gm.line_speaker == "Hulda"


def test_the_game_master_llm_lists_who_else_was_spoken_to():
    client = llm_client('{"next": "Whiterun Guard", "also": ["Ysolda", "Nobody Known"], "reason": "both greeted"}')
    gm = game_master(shout_state(), [HULDA_NPC], client)
    assert gm.choose_next_speaker(MagicMock(), player_line("Hello Ysolda and guard!"), False, allow_nearby=True) is GUARD
    assert gm.aimed_at == [GUARD, YSOLDA]
    request = client.request_call.call_args.args[0].get_last_message().text
    assert 'In "also", list everyone else of Hulda, Ysolda, Whiterun Guard for whom this is true: Dragonborn spoke to that person.' in request


def test_the_player_keeps_the_turn_the_game_master_llm_gives_them_after_an_npcs_line():
    """Hulda turns to Mikael. The Game Master LLM gives the player the turn and lists Mikael: the player still gets it,
    so NPCs talking to each other can't keep the player waiting"""
    client = llm_client('{"next": "Dragonborn", "also": ["Mikael"], "reason": "she asked him"}')
    gm = game_master(shout_state(), [HULDA_NPC, npc("Mikael")], client)
    first = gm.choose_next_speaker(MagicMock(), [npc_reply(config(), HULDA_NPC, "Mikael, tell him.")], True, allow_nearby=True)
    assert first is None and gm.picked_player # Whether Mikael has to answer first is up to the talk settings
    assert [person.name for person in gm.aimed_at] == ["Mikael"] and not gm.is_line_protected


def test_the_player_keeps_the_turn_the_decision_model_gives_them_after_an_npcs_line(caplog):
    caplog.set_level(logging.DEBUG)
    gm = game_master(shout_state(), [HULDA_NPC, npc("Mikael")], decision_client(), decision_config())
    response = decision_response("Dragonborn", {"Dragonborn": 0.7, "Mikael": 0.2}, [0.98, 0.0, 0.0])
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response):
        first = gm.choose_next_speaker(MagicMock(), [npc_reply(config(), HULDA_NPC, "Mikael, tell him.")], True, allow_nearby=True)
    assert first is None and gm.picked_player
    assert [person.name for person in gm.aimed_at] == ["Mikael"]
    assert "Game Master: the latest line was spoken to Mikael" in caplog.text


def test_a_line_the_player_said_is_reported_as_the_players():
    gm = game_master(shout_state(), [HULDA_NPC, npc("Mikael")], decision_client(), decision_config())
    response = decision_response("Dragonborn", {"Dragonborn": 0.7, "Mikael": 0.2}, [0.0, 0.98, 0.0, 0.0])
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response):
        first = gm.choose_next_speaker(MagicMock(), player_line("Mikael, play something."), True, allow_nearby=True)
    assert first is None and gm.picked_player # Mikael answering first is up to the talk settings
    assert [person.name for person in gm.aimed_at] == ["Mikael"] and gm.is_line_protected


def test_a_rejection_that_happens_again_without_the_yes_no_questions_does_not_turn_them_off():
    """The questions were not the problem, e.g. the service failed for another reason. They are asked again next time"""
    from src.conversation import game_master as game_master_module
    gm = game_master(shout_state(), [HULDA_NPC], decision_client(), decision_config())
    rejected = decision_response("", {}, None, status_code=400)
    accepted = decision_response("Ysolda", {"Ysolda": 0.9}, [0.1, 0.9, 0.8])
    try:
        with patch("src.conversation.game_master.post_decision_with_retry", side_effect=[rejected, rejected, accepted]) as post:
            assert gm.choose_next_speaker(MagicMock(), player_line("Hello Ysolda and guard!"), False, allow_nearby=True) is None
            assert not game_master_module.services_without_yes_no_questions
            gm.choose_next_speaker(MagicMock(), player_line("Hello again, Ysolda and guard!"), False, allow_nearby=True)
            assert "addressed_1" in post.call_args.args[2]["questions"]
        assert gm.aimed_at == [YSOLDA, GUARD]
    finally:
        game_master_module.services_without_yes_no_questions.clear()


def test_while_the_player_watches_an_npc_event_the_game_masters_pick_stands():
    gm = game_master(shout_state(), [HULDA_NPC, npc("Mikael")], decision_client(), decision_config())
    gm._GameMaster__context.npc_event = SimpleNamespace(player_joined=False, noticed_player=lambda *_: None, is_winding_down=lambda _config: False, player=None,
                                                           nearby_criteria=lambda _config, _is_said_to_room: ("criteria", "{name} joins.", {"npc_event"}))
    response = decision_response("Mikael", {"Mikael": 0.9}, [0.0, 0.9, 0.9])
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response):
        first = gm.choose_next_speaker(MagicMock(), [npc_reply(config(), HULDA_NPC, "Ysolda, guard, tell him!")], False, allow_nearby=True)
    assert first.name == "Mikael" and gm.aimed_at == [YSOLDA, GUARD]


def people(*names: str) -> list:
    return [npc(name) for name in names]


def test_a_line_to_a_group_is_capped_but_people_it_names_always_answer():
    from src.conversation.game_master import cap_answering
    in_conversation = people("Hulda", "Mikael")
    nearby = [EventPerson(name, number, str(number)) for number, name in enumerate(["Saadia", "Carlotta Valentia", "Olava the Feeble", "Jon", "Sinmir"])]
    everyone = in_conversation + nearby
    kept = cap_answering(everyone, "Dragonborn: Hello everyone!", 3, 10)
    assert [p.name for p in kept] == ["Hulda", "Mikael", "Saadia"] # People in the conversation first, then the nearest
    kept = cap_answering(everyone, "Dragonborn: Sinmir, Carlotta, and the rest of you, hello!", 3, 10)
    assert [p.name for p in kept] == ["Hulda", "Carlotta Valentia", "Sinmir"] # 'the' does not name Olava the Feeble
    kept = cap_answering(everyone, "Dragonborn: sinmeer and carlota, hello!", 1, 10)
    assert [p.name for p in kept] == ["Hulda"] # Misspelled names are not found, so they fall under the cap like the rest
    kept = cap_answering(everyone, "Dragonborn: Hello everyone!", 9, 2)
    assert [p.name for p in kept] == ["Hulda", "Mikael", "Saadia", "Carlotta Valentia"] # Most People in Conversation lets two more in
    kept = cap_answering(everyone, "A fight breaks out.", 3, 10, nearby_first=True)
    assert [p.name for p in kept] == ["Saadia", "Carlotta Valentia", "Olava the Feeble"] # After a Nearby Event: the people nearby first
    kept = cap_answering(everyone, "Mikael drops his lute.", 3, 10, nearby_first=True)
    assert [p.name for p in kept] == ["Mikael", "Saadia", "Carlotta Valentia"] # People the event names still come first


def test_default_prompts_for_several_people_answering_are_valid():
    from src.config.definitions.prompt_definitions import PromptDefinitions as P
    for value, allowed in [(P.get_skyrim_shout_decision_addressed_criteria_config_value(), P.ALLOWED_PROMPT_VARIABLES_SHOUT_ADDRESSED),
                           (P.get_skyrim_shout_decision_not_addressed_criteria_config_value(), P.ALLOWED_PROMPT_VARIABLES_SHOUT_ADDRESSED),
                           (P.get_skyrim_shout_decision_event_part_criteria_config_value(), P.ALLOWED_PROMPT_VARIABLES_SHOUT_EVENT_PART),
                           (P.get_skyrim_shout_decision_event_not_part_criteria_config_value(), P.ALLOWED_PROMPT_VARIABLES_SHOUT_EVENT_PART),
                           (P.get_skyrim_shout_group_answer_note_config_value(), P.ALLOWED_PROMPT_VARIABLES_SHOUT_GROUP_ANSWER)]:
        assert P.PromptChecker(allowed).apply_constraint(value.value).is_success


# Game Master turns

class ShoutTurns:
    """A GameMasterTurns for a Shout conversation, with a fake LLM that answers each request with one line, and a Game Master that makes the given picks"""
    def __init__(self, state: ShoutState, picks: list):
        self.cfg = config()
        context = MagicMock()
        context.config = self.cfg
        context.npc_event = None
        context.shout = state
        context.npcs_in_conversation.last_added_character = HULDA_NPC
        context.npcs_in_conversation.contains_multiple_npcs.return_value = True
        context.get_characters_excluding_player.return_value.get_all_characters.return_value = [HULDA_NPC, YSOLDA_NPC]
        context.get_character_names_as_text.return_value = "Hulda"
        self.sentences = SentenceQueue()
        self.turns = GameMasterTurns(context, self.sentences)
        self.requests: list[str] = []
        self.output_manager = MagicMock()
        self.output_manager.generate_response.side_effect = self.__generate_response
        self.conversation_type = MagicMock(spec=shout)
        self.conversation_type.generate_npc_prompt.return_value = "NPC prompt"
        self.conversation_type.is_director_mode.return_value = False
        self.live_messages = MagicMock()
        self.live_messages.get_persistent_messages.return_value = []
        self.__patch = patch("src.conversation.game_master.GameMaster")
        self.game_master = self.__patch.start().return_value
        self.game_master.choose_next_speaker.side_effect = picks
        self.game_master.picked_player = False
        self.game_master.aimed_at, self.game_master.scores, self.game_master.is_line_protected, self.game_master.line_speaker = [], {}, False, ""

    def __generate_response(self, request, _characters, queue, _actions, single_speaker):
        self.requests.append(single_speaker.name)
        queue.put(Sentence(SentenceContent(single_speaker, f"{single_speaker.name} line.", SentenceTypeEnum.SPEECH, False), "", 0))
        queue.is_more_to_come = False
        queue.put(Sentence(SentenceContent(single_speaker, "", SentenceTypeEnum.SPEECH, True), "", 0))

    def run(self):
        turn_id = self.turns.start_turn()
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


def test_a_nearby_answer_ends_the_turn_and_asks_for_the_add():
    state = shout_state()
    turns = ShoutTurns(state, [GUARD])
    turns.run()
    assert turns.requests == []
    assert state.pending_add is GUARD and state.answering_pick
    first_call = turns.game_master.choose_next_speaker.call_args_list[0]
    assert first_call.kwargs["allow_nearby"] is True


def test_a_pick_between_npc_lines_is_not_an_answer():
    state = shout_state()
    turns = ShoutTurns(state, [HULDA_NPC, GUARD])
    turns.run()
    assert turns.requests == ["Hulda"]
    assert state.pending_add is GUARD and not state.answering_pick


def test_answering_again_offers_nobody_nearby():
    state = shout_state(answer_again=True)
    turns = ShoutTurns(state, [HULDA_NPC, None])
    turns.run()
    assert turns.game_master.choose_next_speaker.call_args_list[0].kwargs["allow_nearby"] is False
    assert not state.answer_again


def test_someone_the_player_spoke_to_answers_in_an_extra_round_after_the_limit():
    state = shout_state()
    state.add_still_to_answer([(HULDA_NPC.ref_id, "Hulda"), (YSOLDA_NPC.ref_id, "Ysolda")], "Player", is_protected=True)
    turns = ShoutTurns(state, [HULDA_NPC] * 5) # The Game Master keeps picking Hulda
    turns.run()
    # Limit 3: Hulda takes all three replies. Ysolda answers in the one extra round, then one reply in between (the defaults)
    assert turns.requests == ["Hulda", "Hulda", "Hulda", "Ysolda", "Hulda"]
    assert not state.still_to_answer


def test_someone_an_npc_spoke_to_does_not_start_the_count_over():
    state = shout_state()
    state.add_still_to_answer([(YSOLDA_NPC.ref_id, "Ysolda")], "Hulda", is_protected=False)
    turns = ShoutTurns(state, [HULDA_NPC] * 5)
    turns.run()
    assert turns.requests == ["Hulda", "Hulda", "Hulda"]


def test_when_the_game_master_gives_the_player_the_turn_someone_who_owes_an_npc_an_answer_still_answers():
    """Only "Anyone an NPC talks to" puts them there, and then they must speak before the player's turn"""
    state = shout_state()
    state.add_still_to_answer([(YSOLDA_NPC.ref_id, "Ysolda")], "Hulda", is_protected=False)
    turns = ShoutTurns(state, [HULDA_NPC, None, None])
    turns.game_master.picked_player = True # Only read once the Game Master returns None
    turns.run()
    assert turns.requests == ["Hulda", "Ysolda"]
    assert not state.still_to_answer


def test_when_the_game_master_gives_the_player_the_turn_someone_the_player_spoke_to_still_answers():
    state = shout_state()
    state.add_still_to_answer([(YSOLDA_NPC.ref_id, "Ysolda")], "Player", is_protected=True)
    turns = ShoutTurns(state, [HULDA_NPC, None, None])
    turns.game_master.picked_player = True
    turns.run()
    assert turns.requests == ["Hulda", "Ysolda"]


def test_the_shout_joiner_speaks_first_and_hears_only_since_the_overheard_note():
    state = shout_state(forced_speaker_ref_id=YSOLDA.ref_id)
    turns = ShoutTurns(state, [None])
    with patch("src.conversation.game_master.messages_heard_by", side_effect=lambda history, _npc: history) as heard_by:
        turns.run()
    assert turns.requests == ["Ysolda"]
    heard_by.assert_called_once()


# Bios and memories of the people nearby

def test_game_master_gets_the_characters_of_the_people_it_may_pick():
    ysolda, guard = SimpleNamespace(name="Ysolda"), SimpleNamespace(name="Whiterun Guard")
    state = shout_state(characters={YSOLDA.refid: ysolda, GUARD.refid: guard})
    state.game_master_offer = [GUARD]
    assert state.characters_for_game_master(config(), ["Hulda"]) == [guard]
    state.game_master_offer = []
    assert state.characters_for_game_master(config(), ["Hulda"]) == [] # The limits keep everyone out
    state.game_master_offer = None
    assert state.characters_for_game_master(config(), ["Hulda"]) == [ysolda, guard] # No decision: everyone nearby


def test_only_the_nearest_most_people_described_get_their_bios():
    ysolda, guard = SimpleNamespace(name="Ysolda"), SimpleNamespace(name="Whiterun Guard")
    state = shout_state(characters={YSOLDA.refid: ysolda, GUARD.refid: guard})
    state.apply_settings({"max_described": 1})
    state.game_master_offer = [YSOLDA, GUARD] # Both may be picked, by name
    assert state.characters_for_game_master(config(), []) == [ysolda] # Only the nearest is described
    state.update_nearby([GUARD, YSOLDA]) # The guard came closer
    assert state.characters_for_game_master(config(), []) == [guard]
    state.apply_settings({"max_described": 0})
    assert state.characters_for_game_master(config(), []) == []


def test_while_the_game_still_looks_the_log_says_so_instead_of_nobody(caplog):
    state = shout_state(nearby=[])
    state.is_nearby_known = False # Shout was just switched on, and the game's list hasn't arrived
    caplog.set_level(logging.DEBUG)
    assert state.nearby_text(config(), []) == "nobody" # Nobody can be picked yet
    state.nearby_text(config(), []) # Logged once
    state.update_nearby([]) # The game looked and found nobody
    state.nearby_text(config(), [])
    nearby = [r.message for r in caplog.records if r.message.startswith("Shout: ") and "nearby" in r.message]
    assert nearby == ["Shout: the game is still looking for who is nearby", "Shout: nearby now: nobody"]


def test_whose_bios_the_game_master_uses_is_logged_when_it_changes(caplog):
    ysolda, guard = SimpleNamespace(name="Ysolda"), SimpleNamespace(name="Whiterun Guard")
    state = shout_state(characters={YSOLDA.refid: ysolda, GUARD.refid: guard})
    state.apply_settings({"max_described": 1})
    state.game_master_offer = [YSOLDA, GUARD]
    caplog.set_level(logging.DEBUG)
    state.characters_for_game_master(config(), [])
    state.characters_for_game_master(config(), []) # Same people: not logged again
    used = [r.message for r in caplog.records if "the Game Master is using" in r.message]
    assert used == ["Shout: the Game Master is using the bios and memories of the nearest person: Ysolda. The 1 other person nearby is known by name only."]
    state.update_nearby([GUARD, YSOLDA]) # The guard came closer
    state.characters_for_game_master(config(), [])
    assert "of the nearest person: Whiterun Guard." in caplog.records[-1].message


def test_people_the_game_has_not_sent_in_full_are_left_out():
    state = shout_state(characters={GUARD.refid: SimpleNamespace(name="Whiterun Guard")})
    assert [c.name for c in state.characters_for_game_master(config(), [])] == ["Whiterun Guard"]


def test_default_game_master_prompt_uses_the_nearby_bios_in_shout():
    from src.config.definitions.prompt_definitions import PromptDefinitions
    value = PromptDefinitions.get_skyrim_game_master_prompt_config_value()
    assert "{nearby_bios}" in PromptDefinitions.apply_prompt_conditions("skyrim_game_master_prompt", value.value, {"shout"})
    checker = PromptDefinitions.PromptChecker(PromptDefinitions.ALLOWED_PROMPT_VARIABLES_GAME_MASTER, "skyrim_game_master_prompt")
    assert checker.apply_constraint(value.value).is_success


def test_nearby_bios_variable_fills_the_bios_of_the_people_offered(default_context, example_skyrim_player_character):
    from tests.test_context_name_variables import _npc
    context = default_context
    context.npcs_in_conversation.add_or_update_character(example_skyrim_player_character)
    context.npcs_in_conversation.add_or_update_character(_npc("Lydia", "abc"))
    belethor = _npc("Belethor", "def")
    belethor.bio = "Owner of Belethor's General Goods."
    context.shout = ShoutState(nearby=[EventPerson("Belethor", 7, "def")], characters={7: belethor})
    context.shout.game_master_offer = list(context.shout.nearby)
    prompt = context.generate_system_message("Nearby: {nearby}\n{nearby_bios}", [])
    assert prompt.startswith("Nearby: Belethor\n")
    assert "Owner of Belethor's General Goods." in prompt
    context.shout.game_master_offer = []
    assert context.generate_system_message("{nearby_bios}", []) == "nobody"
    assert context.generate_system_message("{nearby}", []) == "nobody"


# Just saying something: the player says something to the room, with nobody in the conversation yet

def room_conversation(state: ShoutState) -> Conversation:
    conversation = shout_conversation(state)
    conversation.context.get_characters_excluding_player.return_value = []
    conversation.context.npcs_in_conversation.get_player_character.return_value = PLAYER
    conversation._Conversation__get_player_voiceline = lambda _player, _text: None
    conversation.update_game_events = lambda message: message
    return conversation


def test_room_game_master_offers_the_people_nearby_and_nobody():
    client = llm_client('{"next": "Ysolda", "reason": "asked for a merchant"}')
    gm = game_master(shout_state(), [], client)
    assert gm.choose_next_speaker(MagicMock(), [], False, allow_nearby=True) is YSOLDA
    assert "Choose one of: Ysolda, Whiterun Guard, Nobody." in client.request_call.call_args.args[0].get_last_message().text


@pytest.mark.parametrize("reply", ['{"next": "Nobody", "reason": "nobody was addressed"}', '{"next": "Dragonborn", "reason": "r"}', "not json"])
def test_room_game_master_picking_nobody_or_anything_else_means_nobody_answers(reply):
    gm = game_master(shout_state(), [], llm_client(reply))
    assert gm.choose_next_speaker(MagicMock(), [], False, allow_nearby=True) is None


def test_room_with_nobody_offered_needs_no_game_master_call():
    client = llm_client('{"next": "Ysolda", "reason": "r"}')
    gm = game_master(shout_state(nearby=[]), [], client)
    assert gm.choose_next_speaker(MagicMock(), [], False, allow_nearby=True) is None
    client.request_call.assert_not_called()


def test_room_decision_model_gets_a_nobody_option():
    cfg = config(game_master_use_decision_model=True, shout_decision_nobody_criteria="{player_name} spoke to nobody.",
                 game_master_decision_instructions="Who speaks next?", game_master_decision_npc_criteria="{name} speaks.",
                 game_master_decision_player_criteria="{player_name} speaks.")
    client = llm_client("")
    client.service_name = "OpenRouter"
    client.model_name = "decider"
    client.api_key = "key"
    gm = game_master(shout_state(), [], client, cfg)
    response = MagicMock(status_code=200)
    response.json.return_value = {"answers": {"next_speaker": {"choice": "Nobody"}}}
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response) as post:
        assert gm.choose_next_speaker(MagicMock(), [], False, allow_nearby=True) is None
    criteria = post.call_args.args[2]["questions"]["next_speaker"]["criteria"]
    assert list(criteria) == ["Ysolda", "Whiterun Guard", "Nobody"]
    assert criteria["Nobody"] == "Dragonborn spoke to nobody."


def test_room_is_said_to_room_for_the_game_master_and_has_no_greeting():
    cfg = config(game_master_prompt="GAME MASTER", actions=[])
    context = MagicMock()
    context.get_characters_excluding_player.return_value = []
    shout(cfg).generate_game_master_prompt(context)
    assert context.generate_system_message.call_args.args[0] == "GAME MASTER"
    assert context.generate_system_message.call_args.kwargs["conditions"] == {"say_to_room"} # Never 'shout' as well
    assert shout(cfg).get_user_message(context, MagicMock()) is None
    context.get_characters_excluding_player.return_value = [HULDA_NPC]
    shout(cfg).generate_game_master_prompt(context)
    assert context.generate_system_message.call_args.kwargs["conditions"] == {"shout"}


def test_saying_something_to_the_room_asks_the_game_master_and_skips_the_goodbye_check():
    conversation = room_conversation(shout_state())
    with patch.object(conversation, "_Conversation__start_generating_npc_sentences") as start, \
         patch.object(conversation, "initiate_end_sequence") as end:
        conversation.process_player_input("goodbye")
    start.assert_called_once()
    end.assert_not_called()


def test_the_room_conversation_waits_for_the_player_then_ends_when_nobody_answers():
    conversation = room_conversation(shout_state())
    with patch.object(conversation, "retrieve_sentence_from_queue", return_value=None):
        assert conversation.continue_conversation() == (comm_consts.KEY_REPLYTYPE_PLAYERTALK, None) # Nothing said yet
        with patch.object(conversation, "_Conversation__start_generating_npc_sentences"):
            conversation.process_player_input("Is there a healer here?")
        assert conversation.continue_conversation() == (comm_consts.KEY_REPLYTYPE_ENDCONVERSATION, None)
    assert conversation.ended_unanswered and conversation.has_already_ended


def test_the_room_conversation_adds_the_person_who_answers_instead_of_ending():
    state = shout_state()
    conversation = room_conversation(state)
    with patch.object(conversation, "retrieve_sentence_from_queue", return_value=None), \
         patch.object(conversation, "_Conversation__start_generating_npc_sentences"):
        conversation.process_player_input("Is there a healer here?")
        state.pending_add = YSOLDA
        assert conversation.continue_conversation() == (comm_consts.KEY_REPLYTYPE_SHOUT_ADDACTOR, None)
    assert not conversation.ended_unanswered


@pytest.mark.parametrize("director", [False, True])
def test_default_room_prompts_are_valid(director):
    from src.config.definitions.prompt_definitions import PromptDefinitions
    conditions = {"say_to_room", "director"} if director else {"say_to_room"}
    prompt = PromptDefinitions.apply_prompt_conditions("skyrim_game_master_prompt", PromptDefinitions.get_skyrim_game_master_prompt_config_value().value, conditions)
    assert "{nearby_bios}" in prompt and "Nobody" in prompt
    assert PromptDefinitions.PromptChecker(PromptDefinitions.ALLOWED_PROMPT_VARIABLES_GAME_MASTER).apply_constraint(prompt).is_success
    criteria = PromptDefinitions.apply_prompt_conditions("skyrim_shout_decision_nobody_criteria",
                                                         PromptDefinitions.get_skyrim_shout_decision_nobody_criteria_config_value().value, {"director"} if director else set())
    assert PromptDefinitions.PromptChecker(PromptDefinitions.ALLOWED_PROMPT_VARIABLES_DECISION_PLAYER_CRITERIA).apply_constraint(criteria).is_success
    # Director Mode: what was typed is a direction, not something the player said
    assert ("{player_name} has just said" in prompt) is not director
    assert ("scene direction" in criteria) is director


def test_nearby_bios_are_filled_with_nobody_in_the_conversation(default_context, example_skyrim_player_character):
    from tests.test_context_name_variables import _npc
    context = default_context
    context.npcs_in_conversation.add_or_update_character(example_skyrim_player_character)
    belethor = _npc("Belethor", "def")
    belethor.bio = "Owner of Belethor's General Goods."
    context.shout = ShoutState(nearby=[EventPerson("Belethor", 7, "def")], characters={7: belethor})
    context.shout.game_master_offer = list(context.shout.nearby)
    assert "Owner of Belethor's General Goods." in context.generate_system_message("{nearby_bios}", [])


def test_dropping_the_nearby_bios_to_fit_the_prompt_is_warned_about(default_context, example_skyrim_player_character, caplog):
    from tests.test_context_name_variables import _npc
    context = default_context
    context.npcs_in_conversation.add_or_update_character(example_skyrim_player_character)
    belethor = _npc("Belethor", "def")
    belethor.bio = "Owner of Belethor's General Goods."
    context.shout = ShoutState(nearby=[EventPerson("Belethor", 7, "def")], characters={7: belethor})
    context.shout.game_master_offer = list(context.shout.nearby)
    client = context._context__client
    with patch.object(client, "is_too_long", side_effect=lambda prompt, _percent: "Owner of" in prompt), caplog.at_level("WARNING"):
        assert "Owner of" not in context.generate_system_message("{nearby_bios}", [])
    assert "The bios and memories of the people nearby could not fit" in caplog.text
    caplog.clear()
    with caplog.at_level("WARNING"):
        context.generate_system_message("{nearby_bios}", [])
    assert "people nearby could not fit" not in caplog.text
