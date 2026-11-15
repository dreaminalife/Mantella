"""Unit tests of the pieces behind the talk settings (Conversation page): which set applies to a turn, what the ticked boxes allow,
what the page greys out, the answer order, and who still has to answer. The whole flows are in tests/test_answer_rules_flow.py"""
from itertools import combinations
from types import SimpleNamespace

import pytest

from src.config.definitions.conversation_definitions import (CONVERSATION_EVENT, DIRECTOR, EVENT_KINDS, EXTRA_ROUNDS, GROUP, NEARBY_EVENT, NPC_EVENT,
                                                             RANDOM_EVENT, REPLIES_IN_BETWEEN, SHARED, SHOUT, TALK_KINDS, WHEN_NPC_TALKS, WHEN_YOU_TALK,
                                                             WHO_MUST_SPEAK, AtReplyLimitEnum, WhoAnswersFirstEnum, WhoMustSpeakEnum, greyed_choices,
                                                             must_speak_problem, resolve_ticks, talk_key, use_own_key, who_still_speaks_now)
from src.conversation.answer_rules import AnswerOrder, conversation_situation, turn_rules
from src.conversation.bystanders import ShoutState, TurnAnswers

M = WhoMustSpeakEnum
F = WhoAnswersFirstEnum
A = AtReplyLimitEnum
EVERYONE, YOU, NPC, NOBODY = M.EVERYONE.display_name, M.YOU_TALK_TO.display_name, M.NPC_TALKS_TO.display_name, M.NOBODY.display_name


class FakeConversationType:
    def __init__(self, director: bool = False):
        self.director = director

    def is_director_mode(self) -> bool:
        return self.director


class FakeShout(FakeConversationType):
    pass


class FakeGroup(FakeConversationType):
    pass


def config(**values) -> SimpleNamespace:
    return SimpleNamespace(**values)


def own_set(kind: str, must, after_you=F.ANSWER_NEXT, after_npc=F.ANSWER_NEXT, at_limit=A.NOT_ANSWERED_YOU, between=1, rounds=1, use_own=True) -> dict:
    values = {talk_key(kind, WHO_MUST_SPEAK): frozenset(must), talk_key(kind, WHEN_YOU_TALK): after_you, talk_key(kind, WHEN_NPC_TALKS): after_npc,
              talk_key(kind, "who_still_speaks_at_reply_limit"): at_limit, talk_key(kind, REPLIES_IN_BETWEEN): between, talk_key(kind, EXTRA_ROUNDS): rounds}
    if kind:
        values[use_own_key(kind)] = use_own
    return values


# Which kind of conversation, and whose talk settings

@pytest.mark.parametrize("conversation_type, expected", [
    (FakeGroup(), GROUP),
    (FakeShout(), SHOUT),
    (FakeGroup(director=True), DIRECTOR),
    (FakeShout(director=True), DIRECTOR), # Directing wins over shouting
])
def test_the_kind_of_a_conversation(conversation_type, expected):
    assert conversation_situation(conversation_type, FakeShout) == expected


TURNS = {
    GROUP: dict(conversation_type=FakeGroup()),
    SHOUT: dict(conversation_type=FakeShout()),
    DIRECTOR: dict(conversation_type=FakeGroup(director=True)),
    CONVERSATION_EVENT: dict(conversation_type=FakeGroup(), event_kind="conversation"),
    NEARBY_EVENT: dict(conversation_type=FakeShout(), event_kind="nearby"),
    RANDOM_EVENT: dict(conversation_type=FakeGroup(director=True), event_kind="random"),
    NPC_EVENT: dict(conversation_type=FakeShout(), is_watched_scene=True),
}


def rules_of(cfg, kind: str):
    turn = TURNS[kind]
    return turn_rules(cfg, turn["conversation_type"], FakeShout, turn.get("is_watched_scene", False), turn.get("event_kind", ""))


@pytest.mark.parametrize("kind", TALK_KINDS)
@pytest.mark.parametrize("use_own", [True, False])
def test_each_kind_uses_its_own_set_while_its_switch_is_on_and_the_shared_set_otherwise(kind, use_own):
    cfg = config(**own_set(SHARED, [M.EVERYONE], at_limit=A.EVERYONE_LEFT, between=4, rounds=5),
                 **own_set(kind, [M.NPC_TALKS_TO], after_npc=F.EACH_IN_TURN, at_limit=A.NOBODY, between=2, rounds=3, use_own=use_own))
    rules = rules_of(cfg, kind)
    assert rules.situation == kind
    if use_own:
        assert (rules.settings_from, rules.must_speak, rules.after_npc, rules.at_reply_limit, rules.replies_in_between, rules.extra_rounds) \
            == (kind, frozenset([M.NPC_TALKS_TO]), F.EACH_IN_TURN, A.NOBODY, 2, 3)
    else:
        assert (rules.settings_from, rules.must_speak, rules.after_npc, rules.at_reply_limit, rules.replies_in_between, rules.extra_rounds) \
            == (SHARED, frozenset([M.EVERYONE]), F.ANSWER_NEXT, A.EVERYONE_LEFT, 4, 5)


def test_only_the_watched_npc_event_uses_its_own_settings_by_default():
    for kind in TALK_KINDS:
        assert rules_of(config(), kind).settings_from == (NPC_EVENT if kind == NPC_EVENT else SHARED)


def test_the_defaults():
    shared = rules_of(config(), GROUP)
    assert (shared.must_speak, shared.after_you, shared.after_npc, shared.at_reply_limit, shared.replies_in_between, shared.extra_rounds) \
        == (frozenset([M.YOU_TALK_TO]), F.ANSWER_NEXT, F.ANSWER_NEXT, A.NOT_ANSWERED_YOU, 1, 1)
    watched = rules_of(config(), NPC_EVENT)
    assert (watched.must_speak, watched.after_npc) == (frozenset([M.NOBODY]), F.GAME_MASTER)


def test_a_watched_npc_event_never_uses_when_you_talk_to_someone():
    """Its own set has no such setting: nobody "you" talk to is in the scene"""
    cfg = config(**own_set(NPC_EVENT, [M.NOBODY], after_you=F.EACH_IN_TURN))
    assert rules_of(cfg, NPC_EVENT).after_you == F.GAME_MASTER


def test_an_event_uses_the_most_replies_per_person_of_the_conversation_it_happens_in():
    cfg = config(group_conversation_most_replies_per_person=2, **own_set(RANDOM_EVENT, [M.EVERYONE]))
    rules = turn_rules(cfg, FakeGroup(), FakeShout, is_watched_scene=False, event_kind="random")
    assert (rules.situation, rules.settings_from, rules.most_replies) == (RANDOM_EVENT, RANDOM_EVENT, 2)


def test_shout_uses_its_own_people_who_must_answer_a_group_line():
    cfg = config(shout_most_replies_per_person=3, group_conversation_max_answering=2)
    rules = turn_rules(cfg, FakeShout(), FakeShout, is_watched_scene=False, shout_max_answering=4)
    assert (rules.most_replies, rules.max_answering) == (3, 4)
    assert turn_rules(cfg, FakeGroup(), FakeShout, is_watched_scene=False).max_answering == 2


@pytest.mark.parametrize("value", [None, "Anyone you talk to", [M.NOBODY, M.YOU_TALK_TO], frozenset()])
def test_a_missing_or_impossible_who_must_speak_falls_back_to_the_default(value):
    """A string (not read through the config loader), boxes that can't go together, or no box at all"""
    rules = turn_rules(config(who_must_speak=value), FakeGroup(), FakeShout, is_watched_scene=False)
    assert rules.must_speak == frozenset([M.YOU_TALK_TO])


@pytest.mark.parametrize("value, expected", [(None, 100), (0, 1), (True, 100), ("7", 100), (7, 7)])
def test_most_replies_per_person_is_always_a_usable_number(value, expected):
    rules = turn_rules(config(group_conversation_most_replies_per_person=value), FakeGroup(), FakeShout, is_watched_scene=False)
    assert rules.most_replies == expected


@pytest.mark.parametrize("between, reply_limit, expected", [
    (1, 3, 1), (5, 3, 2), (29, 10, 9), # Never more than the reply limit minus 1, whatever is saved
    (3, 1, 0), # With a reply limit of 1, never any
    (0, 10, 0),
])
def test_replies_in_between_is_capped_at_the_reply_limit_minus_1(between, reply_limit, expected):
    rules = turn_rules(config(replies_in_between=between), FakeGroup(), FakeShout, is_watched_scene=False)
    assert rules.free_picks(reply_limit) == expected


# Who must speak before your turn: which boxes go together

ALL_BOXES = [EVERYONE, YOU, NPC, NOBODY]
ALLOWED = [{EVERYONE}, {YOU}, {NPC}, {NOBODY}, {YOU, NPC}, {EVERYONE, NPC}]


@pytest.mark.parametrize("ticked", [set(combo) for size in range(len(ALL_BOXES) + 1) for combo in combinations(ALL_BOXES, size)])
def test_which_ticked_boxes_can_be_saved(ticked):
    """All 16 combinations: at least one; Nobody in particular alone; Everyone in the conversation never with Anyone you talk to"""
    names = {option.display_name: option for option in M}
    assert (must_speak_problem([names[name] for name in ticked]) == "") == (ticked in ALLOWED)


@pytest.mark.parametrize("before, after, expected", [
    ([YOU], [YOU, NOBODY], [NOBODY]), # Nobody in particular goes with nothing else
    ([NOBODY], [NPC, NOBODY], [NPC]),
    ([YOU, NPC], [YOU, NPC, NOBODY], [NOBODY]),
    ([YOU], [EVERYONE, YOU], [EVERYONE]), # Everyone in the conversation and Anyone you talk to untick each other
    ([EVERYONE, NPC], [EVERYONE, YOU, NPC], [YOU, NPC]),
    ([YOU], [YOU, NPC], [YOU, NPC]), # These go together
    ([EVERYONE], [EVERYONE, NPC], [EVERYONE, NPC]),
    ([YOU, NPC], [NPC], [NPC]), # Unticking one of two
    ([YOU], [], [YOU]), # The last box stays ticked
])
def test_a_box_that_cant_go_with_another_unticks_it(before, after, expected):
    assert resolve_ticks(before, after) == expected


@pytest.mark.parametrize("before", [[YOU], [EVERYONE, NPC], [NOBODY]])
@pytest.mark.parametrize("clicked", ALL_BOXES)
def test_whatever_is_clicked_the_ticked_boxes_can_be_saved(before, clicked):
    after = [box for box in before if box != clicked] if clicked in before else before + [clicked]
    names = {option.display_name: option for option in M}
    assert must_speak_problem([names[name] for name in resolve_ticks(before, after)]) == ""


# What the page greys out

NOT_ANSWERED, EVERYONE_LEFT, NOBODY_NOW = A.NOT_ANSWERED_YOU.display_name, A.EVERYONE_LEFT.display_name, A.NOBODY.display_name


@pytest.mark.parametrize("must", [[YOU], [EVERYONE], [NPC], [NOBODY], [YOU, NPC], [EVERYONE, NPC]])
@pytest.mark.parametrize("at_limit", [NOT_ANSWERED, EVERYONE_LEFT, NOBODY_NOW])
@pytest.mark.parametrize("extra_rounds", [0, 1, 20])
def test_what_is_greyed_out(must, at_limit, extra_rounds):
    greyed = greyed_choices(must, at_limit, extra_rounds)
    nobody = NOBODY in must
    assert set(greyed) == {"who_still_speaks_at_reply_limit", EXTRA_ROUNDS, REPLIES_IN_BETWEEN} # Whole settings only: every choice of "who still speaks" can always be picked
    assert greyed["who_still_speaks_at_reply_limit"] == nobody # Nobody is left who has to speak
    assert greyed[EXTRA_ROUNDS] == (nobody or at_limit == NOBODY_NOW)
    assert greyed[REPLIES_IN_BETWEEN] == (nobody or at_limit == NOBODY_NOW or extra_rounds == 0)


SPOKE_TO_YOU = "everyone you spoke to who hasn't answered you yet"
SPOKE_TO_NPC = "everyone an NPC spoke to who hasn't answered that NPC yet"
ALL_NPCS = "every NPC in the conversation who hasn't said anything yet"


@pytest.mark.parametrize("must, at_limit, expected", [
    ([YOU], EVERYONE_LEFT, SPOKE_TO_YOU),
    ([YOU, NPC], EVERYONE_LEFT, f"{SPOKE_TO_YOU}, and {SPOKE_TO_NPC}"),
    ([YOU, NPC], NOT_ANSWERED, SPOKE_TO_YOU), # The people who only owe an NPC an answer are dropped
    ([EVERYONE, NPC], EVERYONE_LEFT, f"{ALL_NPCS}, and {SPOKE_TO_NPC}"),
    ([EVERYONE, NPC], NOT_ANSWERED, ALL_NPCS),
    ([NPC], NOT_ANSWERED, "nobody"),
    ([NOBODY], EVERYONE_LEFT, "nobody"),
])
def test_the_page_says_who_still_speaks_with_the_boxes_ticked_now(must, at_limit, expected):
    """Who "everyone" is right now: matches who the Game Master keeps on at the limit (the tests above and in test_answer_rules_flow.py)"""
    assert who_still_speaks_now(GROUP, must, at_limit) == f"Right now, with the boxes ticked above: {expected}."
    assert who_still_speaks_now(GROUP, must, NOBODY_NOW) == "" # "Nobody, it's your turn now" says it itself


def test_who_still_speaks_is_worded_for_each_kind_of_conversation():
    assert "everyone your direction is about who hasn't spoken yet" in who_still_speaks_now(DIRECTOR, [YOU], EVERYONE_LEFT)
    assert "everyone the event is about who hasn't spoken yet" in who_still_speaks_now(NEARBY_EVENT, [YOU], EVERYONE_LEFT)
    assert "every NPC in the scene" in who_still_speaks_now(NPC_EVENT, [EVERYONE, NPC], EVERYONE_LEFT)


def test_nothing_greys_out_the_setting_that_greyed_it():
    """So nothing can get stuck: "who still speaks" is never greyed by the extra rounds, and the extra rounds never by Replies in between"""
    for at_limit in [NOT_ANSWERED, EVERYONE_LEFT, NOBODY_NOW]:
        assert not greyed_choices([YOU], at_limit, 0)["who_still_speaks_at_reply_limit"]
    assert not greyed_choices([YOU], EVERYONE_LEFT, 0)[EXTRA_ROUNDS]


# The answer order

def test_each_in_turn_groups_queue_the_oldest_first_and_one_reply_covers_two():
    order = AnswerOrder()
    order.add([("b", "B"), ("c", "C")], from_you=True, answers_first=F.EACH_IN_TURN)
    order.add([("e", "E"), ("f", "F")], from_you=False, answers_first=F.EACH_IN_TURN)
    order.add([("e", "E")], from_you=False, answers_first=F.EACH_IN_TURN)
    assert order.names() == [["B", "C"], ["E", "F"], ["E"]]
    order.spoke("c")
    order.spoke("b")
    assert order.due() == [("e", "E"), ("f", "F")]
    order.spoke("e") # Covers both lines spoken to E
    assert order.names() == [["F"]]


def test_an_npcs_they_answer_next_is_forgotten_while_people_are_still_waiting_their_turn():
    order = AnswerOrder()
    order.add([("b", "B"), ("c", "C")], from_you=True, answers_first=F.EACH_IN_TURN)
    order.spoke("b")
    assert not order.add([("e", "E")], from_you=False, answers_first=F.ANSWER_NEXT)
    assert order.names() == [["C"]]


def test_they_answer_next_is_used_up_by_the_next_reply_whoever_gives_it():
    order = AnswerOrder()
    assert order.add([("b", "B"), ("c", "C")], from_you=False, answers_first=F.ANSWER_NEXT)
    order.spoke("x")
    assert order.due() == []


def test_the_game_master_decides_adds_nobody():
    order = AnswerOrder()
    assert order.add([("b", "B")], from_you=True, answers_first=F.GAME_MASTER)
    assert order.due() == []


def test_people_who_left_or_reached_most_replies_are_no_longer_due():
    order = AnswerOrder()
    order.add([("b", "B"), ("c", "C")], from_you=True, answers_first=F.EACH_IN_TURN)
    order.drop({"b", "c"})
    assert order.groups == []


# Who still has to answer

def test_someone_spoken_to_again_keeps_the_stronger_claim():
    answers = TurnAnswers()
    answers.add_still_to_answer([("1", "Hulda"), ("2", "Mikael")], "Prisoner", is_protected=True)
    answers.add_still_to_answer([("1", "Hulda")], "Saadia", is_protected=False) # An NPC's line to her afterwards
    due = answers.still_to_answer["1"]
    assert due.is_protected and due.speaker == "Saadia" and due.others == []
    assert answers.still_to_answer["2"].others == ["Hulda"]


def test_only_answers_owed_to_npc_lines_are_dropped():
    answers = TurnAnswers()
    answers.add_still_to_answer([("1", "Hulda")], "Prisoner", is_protected=True)
    answers.add_still_to_answer([("2", "Mikael")], "Hulda", is_protected=False)
    assert answers.drop_unprotected_answers() == ["Mikael"]
    assert list(answers.still_to_answer) == ["1"]


def test_people_who_reached_most_replies_per_person_no_longer_have_to_answer():
    answers = TurnAnswers()
    answers.add_still_to_answer([("1", "Hulda"), ("2", "Mikael")], "Prisoner", is_protected=True)
    assert answers.drop_answers({"2", "3"}) == ["Mikael"]
    assert answers.take_still_to_answer("1").name == "Hulda"
    assert answers.take_still_to_answer("1") is None
    assert answers.still_to_answer == {}


def test_shout_keeps_who_still_has_to_answer_and_forgets_it_with_the_pick():
    state = ShoutState()
    state.add_still_to_answer([("1", "Hulda")], "Prisoner", is_protected=True)
    assert isinstance(state, TurnAnswers) and "1" in state.still_to_answer
    state.clear_pick()
    assert state.still_to_answer == {}


def test_every_event_kind_has_a_switch():
    assert set(EVENT_KINDS.values()) <= set(TALK_KINDS)
