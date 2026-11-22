"""End-to-end tests of the talk settings and Most Replies per Person (Conversation page), in every situation they apply to:
group conversations, Shout, Director Mode, Conversation/Nearby/Random Events and watched NPC Events. Also the Shout and event options
that moved from the MCM to Mantella's settings.

The first tests rebuild each of the six options of the old Talk Level dropdown from the new settings (the mapping table of the Talk Level
Rework design) and check they give the same speakers as the old options did: the expected speakers are the ones the old code gave.
One difference, as the design says: after an NPC talks to someone, "They answer next" makes them answer next also when the Game Master
gives the player the turn. The old options gave the player the turn right away. The tests after them cover each rule of the new settings.

Uses the fake game and model of tests/test_conversation_events_flow.py: the real Mantella code runs, with the same requests the game sends,
and a fake model whose picks and "spoken to" answers are scripted, so every run gives the same result. FakeModel.also lists, each time the
Game Master is asked who else a line was spoken to (or who an event or direction is about), the people it answers with.

Every test runs twice: with a Game Master LLM, and with a decision model (Game Master Use Decision Model), which is asked through the
Decisions API with one yes/no question per person instead of "also". The same scripted picks and answers drive both.
"""
import logging
import re
from unittest.mock import MagicMock, patch

import pytest

from src.config.config_loader import ConfigLoader
from src.config.definitions.conversation_definitions import (AT_REPLY_LIMIT, EXTRA_ROUNDS, MAX_EXTRA_ROUNDS, MAX_NPC_TURNS_KINDS, MAX_REPLIES_IN_BETWEEN,
                                                             REPLIES_IN_BETWEEN, WHEN_NPC_TALKS, WHEN_YOU_TALK, WHO_MUST_SPEAK, AtReplyLimitEnum,
                                                             WhoAnswersFirstEnum, WhoMustSpeakEnum, max_npc_turns_identifier, talk_key, use_own_key)
from src.game_manager import GameStateManager
from tests.test_conversation_events_flow import (FakeGame, FakeModel, HULDA, MIKAEL, OLFINA, PLAYER, SAADIA, YSOLDA, game, manager, model,  # noqa: F401 (fixtures)
                                                 shout_state)


def fake_decisions_api(model: FakeModel):
    """Stands in for the Decisions API (post_decision_with_retry), answering with the model's scripted picks and "also" people"""
    def post(url, headers, body, timeout, should_retry=lambda: True):
        questions = body["questions"]
        choices = list(questions["next_speaker"]["criteria"])
        model.game_master_choices.append(choices)
        model.game_master_requests.append(str(body))
        conversation = "\n".join(body["state"]["conversation"])
        if model.after_event and model.event and model.event in conversation:
            pick, model.after_event = model.after_event, ""
        elif model.picks:
            pick = model.picks.pop(0)
        else:
            pick = PLAYER.name if PLAYER.name in choices else choices[0]
        if pick not in choices:
            model.mistakes.append(f"{pick} was not offered: {choices}")
        answers = {"next_speaker": {"choice": pick, "probabilities": {pick: 1.0}}}
        asked = {name: question for name, question in questions.items() if name.startswith("addressed_")}
        also = model.also.pop(0) if asked and model.also else []
        for name, question in asked.items():
            match = re.search(r"addressed to (.+)\?$", question["instructions"]) or re.search(r"^Is (.+?) part of what just happened", question["instructions"])
            person = match.group(1) if match else ""
            # As with an LLM, the next speaker counts as spoken to only if someone else is too
            answers[name] = {"type": "noul", "noul": 1.0 if person in also or (also and person == pick) else 0.0}
        response = MagicMock(status_code=200, text="")
        response.json.return_value = {"answers": answers}
        return response
    return post


@pytest.fixture(params=["llm", "decision_model"], autouse=True)
def game_master_kind(request, manager: GameStateManager, model: FakeModel, default_config: ConfigLoader, monkeypatch):
    """Runs each test with a Game Master LLM, and again with a decision model"""
    if request.param == "decision_model":
        default_config.game_master_use_decision_model = True
        monkeypatch.setattr(model, "service_name", "OpenRouter", raising=False)
        monkeypatch.setattr(model, "api_key", "test", raising=False)
        with patch("src.conversation.game_master.post_decision_with_retry", side_effect=fake_decisions_api(model)) as post:
            yield request.param
        assert post.call_count == len(model.game_master_choices) # Every Game Master decision went through the decision model
    else:
        yield request.param

MUST_EVERYONE, MUST_YOU, MUST_NPC, MUST_NOBODY = WhoMustSpeakEnum.EVERYONE, WhoMustSpeakEnum.YOU_TALK_TO, WhoMustSpeakEnum.NPC_TALKS_TO, WhoMustSpeakEnum.NOBODY
NEXT, IN_TURN, GM_DECIDES = WhoAnswersFirstEnum.ANSWER_NEXT, WhoAnswersFirstEnum.EACH_IN_TURN, WhoAnswersFirstEnum.GAME_MASTER
NOT_ANSWERED_YOU, EVERYONE_LEFT, NOBODY_LEFT = AtReplyLimitEnum.NOT_ANSWERED_YOU, AtReplyLimitEnum.EVERYONE_LEFT, AtReplyLimitEnum.NOBODY


def talk(config: ConfigLoader, kind: str, must=(MUST_YOU,), after_you=NEXT, after_npc=NEXT, at_limit=NOT_ANSWERED_YOU, between=1, rounds=1):
    """Sets the talk settings of a kind and turns its "Use its own settings" on. Kind "" sets the shared set (All Conversations)"""
    if kind:
        setattr(config, use_own_key(kind), True)
    setattr(config, talk_key(kind, WHO_MUST_SPEAK), frozenset(must))
    setattr(config, talk_key(kind, WHEN_YOU_TALK), after_you)
    setattr(config, talk_key(kind, WHEN_NPC_TALKS), after_npc)
    setattr(config, talk_key(kind, AT_REPLY_LIMIT), at_limit)
    setattr(config, talk_key(kind, REPLIES_IN_BETWEEN), between)
    setattr(config, talk_key(kind, EXTRA_ROUNDS), rounds)


# The six options of the old Talk Level dropdown, rebuilt from the new settings (the design's mapping table). "No limit" is the highest number,
# and "reply limit - 1" is the highest number too, as Replies in between never goes past it
EVERYONE = "Everyone Gets to Speak"
EVEN_BY_NPC = "Anyone Spoken To Answers, Even by an NPC"
OTHERS_CAN_RESPOND = "Anyone You Speak To Answers, Others Can Respond"
NO_EXTRA = "Anyone You Speak To Answers, No Extra Replies"
WITHIN_LIMIT = "Anyone You Speak To Answers, Within the Reply Limit"
GAME_MASTER_PICKS = "Game Master Picks Every Speaker"
OLD_OPTIONS = {
    EVERYONE: dict(must=[MUST_EVERYONE], between=0, rounds=MAX_EXTRA_ROUNDS),
    EVEN_BY_NPC: dict(must=[MUST_YOU, MUST_NPC], between=MAX_REPLIES_IN_BETWEEN, rounds=MAX_EXTRA_ROUNDS),
    OTHERS_CAN_RESPOND: dict(must=[MUST_YOU], between=MAX_REPLIES_IN_BETWEEN, rounds=MAX_EXTRA_ROUNDS),
    NO_EXTRA: dict(must=[MUST_YOU], between=0, rounds=MAX_EXTRA_ROUNDS),
    WITHIN_LIMIT: dict(must=[MUST_YOU], at_limit=NOBODY_LEFT),
    GAME_MASTER_PICKS: dict(must=[MUST_NOBODY], after_you=GM_DECIDES, after_npc=GM_DECIDES),
}


def old_option(config: ConfigLoader, kind: str, option: str):
    """A kind's own talk settings set to what one of the old Talk Level options did"""
    talk(config, kind, **OLD_OPTIONS[option])


def speakers(game: FakeGame, since: int) -> list[str]:
    return [speaker for speaker, _line in game.lines[since:]]


def start(game: FakeGame, situation: str, *npcs):
    """A conversation in the situation: a group conversation, Shout, or Director Mode (not shouting)"""
    if situation == "director":
        game.config.multi_conversation_director_mode = True
    game.start(*npcs)
    if situation == "shout":
        game.set_shout(True)


# An NPC speaks to another NPC, and the Game Master gives the player the turn

@pytest.mark.parametrize("situation", ["group_conversation", "shout"])
@pytest.mark.parametrize("rule, expected", [
    (EVERYONE, [HULDA.name, MIKAEL.name, SAADIA.name]), # Everyone still has to say something
    (EVEN_BY_NPC, [HULDA.name, MIKAEL.name]), # Mikael has to answer Hulda
    # Unlike the old options, Mikael answers Hulda next (They answer next), as the design says, then it's the player's turn.
    # The old options gave the player the turn right away
    (OTHERS_CAN_RESPOND, [HULDA.name, MIKAEL.name]),
    (NO_EXTRA, [HULDA.name, MIKAEL.name]),
    (WITHIN_LIMIT, [HULDA.name, MIKAEL.name]),
    (GAME_MASTER_PICKS, [HULDA.name]), # The player gets the turn
])
def test_an_npc_speaks_to_another_while_the_game_master_gives_the_player_the_turn(game: FakeGame, model: FakeModel, situation, rule, expected):
    old_option(game.config, situation, rule)
    start(game, situation, HULDA, MIKAEL, SAADIA)
    since = len(game.lines)
    # The player speaks to Hulda only. Hulda's line is spoken to Mikael, and the Game Master gives the player the turn
    model.picks, model.also = [HULDA.name, PLAYER.name, PLAYER.name], [[], [MIKAEL.name], [], []]
    game.say("Hulda, what's the news?")

    assert speakers(game, since) == expected
    assert game.is_player_turn


# Someone the player spoke to only gets to answer at the reply limit

@pytest.mark.parametrize("situation", ["group_conversation", "shout"])
@pytest.mark.parametrize("rule, expected", [
    (EVERYONE, [HULDA.name, SAADIA.name, MIKAEL.name]), # Past the limit once, nobody reacts
    (EVEN_BY_NPC, [HULDA.name, SAADIA.name, MIKAEL.name, HULDA.name]), # The count starts over with Mikael's answer, so Hulda reacts to him
    (OTHERS_CAN_RESPOND, [HULDA.name, SAADIA.name, MIKAEL.name, HULDA.name]),
    (NO_EXTRA, [HULDA.name, SAADIA.name, MIKAEL.name]), # Mikael answers once, nobody reacts
    (WITHIN_LIMIT, [HULDA.name, SAADIA.name]), # The limit is final
    (GAME_MASTER_PICKS, [HULDA.name, SAADIA.name]),
])
def test_someone_spoken_to_who_only_gets_to_answer_at_the_reply_limit(game: FakeGame, model: FakeModel, default_config: ConfigLoader, situation, rule, expected):
    for each_kind in MAX_NPC_TURNS_KINDS: # Each kind of conversation has its own
        setattr(default_config, max_npc_turns_identifier(each_kind), 2)
    old_option(game.config, situation, rule)
    start(game, situation, HULDA, MIKAEL, SAADIA)
    since = len(game.lines)
    # The player speaks to Hulda and Mikael. Hulda answers, then Saadia cuts in: the limit of 2 is reached before Mikael answers
    model.picks, model.also = [HULDA.name, SAADIA.name, HULDA.name, PLAYER.name], [[MIKAEL.name], [], [], []]
    game.say("Hulda, Mikael, what's the news?")

    assert speakers(game, since) == expected
    assert game.is_player_turn


@pytest.mark.parametrize("rule, expected", [
    (EVERYONE, [HULDA.name, SAADIA.name, MIKAEL.name]),
    (EVEN_BY_NPC, [HULDA.name, SAADIA.name, MIKAEL.name, HULDA.name]),
    (NO_EXTRA, [HULDA.name, SAADIA.name, MIKAEL.name]),
    (WITHIN_LIMIT, [HULDA.name, SAADIA.name]),
    (GAME_MASTER_PICKS, [HULDA.name, SAADIA.name]),
])
def test_director_mode_someone_the_direction_is_about_at_the_reply_limit(game: FakeGame, model: FakeModel, default_config: ConfigLoader, rule, expected):
    default_config.multi_npc_game_master_director_max_npc_turns = 2
    old_option(default_config, "director", rule)
    start(game, "director", HULDA, MIKAEL, SAADIA)
    since = len(game.lines)
    # The direction is about Hulda and Mikael. Hulda reacts, Saadia cuts in, and the limit of 2 is reached before Mikael reacts
    model.picks, model.also = [HULDA.name, SAADIA.name, HULDA.name, SAADIA.name], [[MIKAEL.name], [], [], []]
    game.say("Hulda and Mikael start arguing about the price of mead.")

    assert speakers(game, since) == expected
    assert game.is_player_turn
    assert not any(PLAYER.name in choices for choices in model.game_master_choices) # The Game Master never gives the director the turn


@pytest.mark.parametrize("situation", ["group_conversation", "director"])
@pytest.mark.parametrize("rule, expected", [
    (EVEN_BY_NPC, [HULDA.name, MIKAEL.name]), # Mikael has to answer Hulda, although he is the only one she could mean
    (NO_EXTRA, [HULDA.name, MIKAEL.name]), # Mikael answers Hulda next (They answer next), although he doesn't have to
])
def test_with_two_npcs_even_by_an_npc_still_makes_the_other_answer(game: FakeGame, model: FakeModel, default_config: ConfigLoader, situation, rule, expected):
    default_config.multi_npc_game_master_director_max_npc_turns = 2
    old_option(game.config, situation, rule)
    game.around = [] # Nobody nearby: Mikael is the only one Hulda's line can be meant for
    start(game, situation, HULDA, MIKAEL)
    since = len(game.lines)
    # The player speaks (or directs) to Hulda only. Hulda's line is spoken to Mikael, but the Game Master picks the player, or Hulda again in Director Mode
    next_pick = HULDA.name if situation == "director" else PLAYER.name
    model.picks, model.also = [HULDA.name, next_pick, next_pick], [[], [MIKAEL.name], []]
    game.say("Hulda tells the news." if situation == "director" else "Hulda, what's the news?")

    assert speakers(game, since)[:len(expected)] == expected
    if situation == "group_conversation":
        assert speakers(game, since) == expected and game.is_player_turn


def test_director_mode_in_shout_uses_the_director_settings(game: FakeGame, model: FakeModel, default_config: ConfigLoader):
    default_config.multi_npc_game_master_director_max_npc_turns = 2
    old_option(default_config, "director", WITHIN_LIMIT)
    old_option(default_config, "shout", OTHERS_CAN_RESPOND)
    default_config.multi_conversation_director_mode = True
    game.start(HULDA, MIKAEL, SAADIA)
    game.set_shout(True)
    since = len(game.lines)
    model.picks, model.also = [HULDA.name, SAADIA.name, HULDA.name], [[MIKAEL.name], [], [], []]
    game.say("Hulda and Mikael start arguing about the price of mead.")

    assert speakers(game, since) == [HULDA.name, SAADIA.name] # Within the Reply Limit, not Shout's settings


# A line said to the whole group

def test_a_group_conversation_line_to_everyone_is_answered_by_most_people_answering_at_once(game: FakeGame, model: FakeModel):
    game.config.group_conversation_max_answering = 2
    start(game, "group_conversation", HULDA, MIKAEL, SAADIA)
    since = len(game.lines)
    model.picks, model.also = [HULDA.name, PLAYER.name, PLAYER.name], [[MIKAEL.name, SAADIA.name], [], []]
    game.say("Hello everyone!")

    assert speakers(game, since) == [HULDA.name, MIKAEL.name] # Only 2 of the 3 have to answer
    assert game.is_player_turn


def test_a_group_conversation_line_to_two_people_is_answered_by_both_and_nobody_joins(game: FakeGame, model: FakeModel):
    start(game, "group_conversation", HULDA, MIKAEL, SAADIA)
    since = len(game.lines)
    model.picks, model.also = [HULDA.name, PLAYER.name, PLAYER.name], [[MIKAEL.name], [], []]
    game.say("Hulda, Mikael, good evening.")

    assert speakers(game, since) == [HULDA.name, MIKAEL.name]
    assert game.joined == [] and shout_state(game.manager) is None
    assert any("answers in one or two sentences" in request for request in model.npc_requests[-2:]) # The Group Answer Note, also without Shout


def test_everyone_gets_to_speak_is_not_capped_by_most_people_answering_at_once(game: FakeGame, model: FakeModel):
    old_option(game.config, "group_conversation", EVERYONE)
    game.config.group_conversation_max_answering = 1
    start(game, "group_conversation", HULDA, MIKAEL, SAADIA)
    since = len(game.lines)
    model.picks = [HULDA.name, PLAYER.name, PLAYER.name, PLAYER.name]
    game.say("Evening.")

    assert sorted(speakers(game, since)) == sorted([HULDA.name, MIKAEL.name, SAADIA.name])


# Most Replies per Person

@pytest.mark.parametrize("situation", ["group_conversation", "shout", "director"])
def test_most_replies_per_person_leaves_out_who_has_reached_it(game: FakeGame, model: FakeModel, situation):
    setattr(game.config, f"{situation}_most_replies_per_person", 1)
    game.around = [] # Nobody nearby who could join
    start(game, situation, HULDA, MIKAEL)
    since, asked = len(game.lines), len(model.game_master_choices)
    model.picks = [HULDA.name, MIKAEL.name]
    game.say("Tell me everything.")

    assert speakers(game, since) == [HULDA.name, MIKAEL.name] # Nobody speaks twice
    choices = model.game_master_choices[asked:]
    assert HULDA.name not in choices[-1] # Hulda was not offered again
    assert len(choices) == 2 # Once both have reached it, the Game Master isn't asked: the turn is over
    assert game.is_player_turn


def test_most_replies_per_person_wins_over_who_still_has_to_answer(game: FakeGame, model: FakeModel):
    game.config.group_conversation_most_replies_per_person = 1
    old_option(game.config, "group_conversation", EVEN_BY_NPC)
    start(game, "group_conversation", HULDA, MIKAEL, SAADIA)
    since = len(game.lines)
    # Mikael's line is spoken to Hulda, who has already spoken once
    model.picks, model.also = [HULDA.name, MIKAEL.name, PLAYER.name], [[], [], [HULDA.name]]
    game.say("Hulda, what's the news?")

    assert speakers(game, since) == [HULDA.name, MIKAEL.name]


def test_at_100_most_replies_per_person_changes_nothing(game: FakeGame, model: FakeModel):
    start(game, "group_conversation", HULDA, MIKAEL)
    since = len(game.lines)
    model.picks = [HULDA.name, HULDA.name, HULDA.name]
    game.say("Tell me everything.")
    assert speakers(game, since) == [HULDA.name, HULDA.name, HULDA.name] # Up to Game Master Max NPC Turns (3)


def test_most_replies_per_person_counts_from_the_players_last_line(game: FakeGame, model: FakeModel):
    game.config.group_conversation_most_replies_per_person = 1
    start(game, "group_conversation", HULDA, MIKAEL)
    model.picks = [HULDA.name, PLAYER.name]
    game.say("Hulda?")
    since = len(game.lines)
    model.picks = [HULDA.name, PLAYER.name]
    game.say("Hulda, again?")
    assert speakers(game, since) == [HULDA.name] # She may speak again after the player's next line


# Events

@pytest.mark.parametrize("event_option, shared_option, expected", [
    (None, OTHERS_CAN_RESPOND, [HULDA.name, MIKAEL.name]), # Its switch off: the shared set. The event is about both: both react before the player's turn
    (None, GAME_MASTER_PICKS, [HULDA.name]), # The shared set: nobody has to
    (GAME_MASTER_PICKS, OTHERS_CAN_RESPOND, [HULDA.name]), # Its own settings
    (EVERYONE, GAME_MASTER_PICKS, [HULDA.name, MIKAEL.name, SAADIA.name]),
])
def test_a_conversation_event_uses_its_own_settings_or_the_shared_ones(game: FakeGame, model: FakeModel, event_option, shared_option, expected):
    if event_option:
        old_option(game.config, "conversation_event", event_option)
    old_option(game.config, "", shared_option)
    start(game, "group_conversation", HULDA, MIKAEL, SAADIA)
    since = len(game.lines)
    model.after_event, model.also, model.picks = HULDA.name, [[MIKAEL.name], [], [], []], [PLAYER.name, PLAYER.name, PLAYER.name]
    game.pick_event("conversation")

    assert game.notes[-1] == "Something happened"
    assert speakers(game, since) == expected
    assert game.is_player_turn


@pytest.mark.parametrize("kind", ["nearby", "random"])
@pytest.mark.parametrize("event_option, expected", [
    (None, [YSOLDA.name, SAADIA.name]), # The shared set: everyone the event is about reacts
    (GAME_MASTER_PICKS, [YSOLDA.name]), # Both join together, but only the Game Master's pick speaks
])
def test_nearby_and_random_events_use_their_own_settings(game: FakeGame, model: FakeModel, kind, event_option, expected):
    if event_option:
        old_option(game.config, f"{kind}_event", event_option)
    game.around = [YSOLDA, SAADIA]
    start(game, "group_conversation", HULDA)
    since = len(game.lines)
    model.after_event, model.also, model.picks = YSOLDA.name, [[SAADIA.name], [], []], [PLAYER.name, PLAYER.name]
    game.pick_event(kind)

    assert game.joined == [YSOLDA, SAADIA]
    assert speakers(game, since) == expected


def test_an_event_during_a_shout_with_its_own_settings_uses_the_shared_set(game: FakeGame, model: FakeModel, default_config: ConfigLoader):
    """The event's switch is off, so it follows All Conversations, not the Shout's own settings"""
    old_option(default_config, "shout", GAME_MASTER_PICKS)
    old_option(default_config, "", OTHERS_CAN_RESPOND)
    start(game, "shout", HULDA, MIKAEL, SAADIA)
    game.is_player_turn = True
    since = len(game.lines)
    model.after_event, model.also, model.picks = HULDA.name, [[MIKAEL.name], [], []], [PLAYER.name, PLAYER.name]
    game.pick_event("conversation")

    assert speakers(game, since) == [HULDA.name, MIKAEL.name] # Shout's own settings would let the player have the turn after Hulda


@pytest.mark.parametrize("situation", ["group_conversation", "director"])
def test_an_event_uses_the_most_replies_per_person_of_the_conversation_it_happens_in(game: FakeGame, model: FakeModel, situation):
    setattr(game.config, f"{situation}_most_replies_per_person", 1)
    start(game, situation, HULDA, MIKAEL)
    game.is_player_turn = True
    since, asked = len(game.lines), len(model.game_master_choices)
    model.after_event, model.picks = HULDA.name, [MIKAEL.name]
    game.pick_event("conversation")

    assert speakers(game, since) == [HULDA.name, MIKAEL.name] # Nobody reacts twice
    choices = model.game_master_choices[asked:]
    assert len(choices) == 2 and HULDA.name not in choices[1] # Hulda isn't offered again. Once both have reacted, the Game Master isn't asked
    assert game.is_player_turn


def test_an_event_in_director_mode_follows_the_shared_set_and_its_own_max_npc_turns(game: FakeGame, model: FakeModel, default_config: ConfigLoader):
    default_config.multi_npc_game_master_director_max_npc_turns = 5 # Not Director Mode's: the event's own, 2
    default_config.conversation_event_max_npc_turns = 2
    old_option(default_config, "director", WITHIN_LIMIT)
    old_option(default_config, "", OTHERS_CAN_RESPOND)
    start(game, "director", HULDA, MIKAEL, SAADIA)
    game.is_player_turn = True
    since = len(game.lines)
    model.after_event, model.also, model.picks = HULDA.name, [[MIKAEL.name], [], []], [SAADIA.name, HULDA.name]
    game.pick_event("conversation")

    # The shared set (Others Can Respond), not Director Mode's own: Mikael reacts after the limit of 2, then one reply in between
    assert speakers(game, since) == [HULDA.name, SAADIA.name, MIKAEL.name, HULDA.name]


# Watched NPC Events

def npc_event(game: FakeGame, model: FakeModel, picks: list[str], also: list[list[str]] | None = None):
    model.participants = [HULDA.name, MIKAEL.name]
    model.picks, model.also = picks, also or []
    game.npc_event_check()


@pytest.mark.parametrize("rule, expected", [
    (GAME_MASTER_PICKS, [HULDA.name, HULDA.name, HULDA.name]), # Today's behaviour: Mikael may stay silent
    (EVERYONE, [HULDA.name, HULDA.name, HULDA.name, MIKAEL.name]), # Mikael speaks past the line limit
])
def test_a_watched_npc_event_everyone_gets_to_speak(game: FakeGame, model: FakeModel, default_config: ConfigLoader, rule, expected):
    default_config.npc_event_max_lines_without_player = 3
    old_option(default_config, "npc_event", rule)
    game.around = [HULDA, MIKAEL]
    npc_event(game, model, [HULDA.name, HULDA.name, HULDA.name])

    assert speakers(game, 0) == expected
    assert not game.is_running # The scene ended


@pytest.mark.parametrize("rule, expected", [
    (GAME_MASTER_PICKS, [HULDA.name, HULDA.name]), # Mikael is spoken to, but the Game Master's pick stands
    (EVEN_BY_NPC, [HULDA.name, MIKAEL.name]), # Mikael answers Hulda
])
def test_a_watched_npc_event_someone_spoken_to_answers(game: FakeGame, model: FakeModel, default_config: ConfigLoader, rule, expected):
    default_config.npc_event_max_lines_without_player = 2
    old_option(default_config, "npc_event", rule)
    game.around = [HULDA, MIKAEL, SAADIA] # Saadia nearby, so the Game Master is asked who each line was spoken to
    npc_event(game, model, [HULDA.name, HULDA.name], [[MIKAEL.name]])

    assert speakers(game, 0) == expected
    assert game.joined == []


def test_a_watched_npc_event_of_two_even_by_an_npc_makes_the_other_answer(game: FakeGame, model: FakeModel, default_config: ConfigLoader):
    default_config.npc_event_max_lines_without_player = 2
    old_option(default_config, "npc_event", EVEN_BY_NPC)
    game.around = [HULDA, MIKAEL] # Nobody else nearby: Mikael is the only one Hulda's line can be meant for
    npc_event(game, model, [HULDA.name, HULDA.name], [[MIKAEL.name]])

    assert speakers(game, 0) == [HULDA.name, MIKAEL.name]


def test_a_watched_npc_event_everyone_gets_to_speak_also_who_joined_during_the_scene(game: FakeGame, model: FakeModel, default_config: ConfigLoader):
    default_config.npc_event_max_lines_without_player = 3
    old_option(default_config, "npc_event", EVERYONE)
    game.around = [HULDA, MIKAEL, YSOLDA, SAADIA]
    # Hulda's line is spoken to Ysolda and Saadia: both join, and Ysolda answers. Saadia only joined together with her
    npc_event(game, model, [HULDA.name, YSOLDA.name, HULDA.name], [[], [YSOLDA.name, SAADIA.name]])

    assert game.joined == [YSOLDA, SAADIA]
    assert speakers(game, 0) == [HULDA.name, YSOLDA.name, HULDA.name, MIKAEL.name, SAADIA.name] # Past the line limit: Mikael, then Saadia, who joined
    assert not game.is_running


def test_shout_everyone_gets_to_speak_also_who_joined_to_answer_an_npc(game: FakeGame, model: FakeModel):
    old_option(game.config, "shout", EVERYONE)
    game.around = [YSOLDA, SAADIA]
    start(game, "shout", HULDA)
    since = len(game.lines)
    # Hulda's line is spoken to Ysolda and Saadia, who join. Ysolda answers, then the Game Master gives the player the turn
    model.picks, model.also = [HULDA.name, YSOLDA.name, PLAYER.name, PLAYER.name], [[], [YSOLDA.name, SAADIA.name], [], []]
    game.say("Evening.")

    assert game.joined == [YSOLDA, SAADIA]
    assert speakers(game, since) == [HULDA.name, YSOLDA.name, SAADIA.name] # Saadia joined, so she says something too
    assert game.is_player_turn


def test_a_watched_npc_event_ends_when_everyone_reached_most_replies_per_person(game: FakeGame, model: FakeModel, default_config: ConfigLoader):
    default_config.npc_event_max_lines_without_player = 10
    default_config.npc_event_most_replies_per_person = 1
    game.around = [HULDA, MIKAEL]
    npc_event(game, model, [HULDA.name, MIKAEL.name])

    assert speakers(game, 0) == [HULDA.name, MIKAEL.name]
    assert not game.is_running


def test_a_watched_npc_event_at_its_defaults_is_unchanged(game: FakeGame, model: FakeModel, default_config: ConfigLoader):
    default_config.npc_event_max_lines_without_player = 2
    game.around = [HULDA, MIKAEL, YSOLDA, SAADIA]
    npc_event(game, model, [HULDA.name, MIKAEL.name], [[YSOLDA.name, SAADIA.name]])

    assert game.joined == [YSOLDA, SAADIA] # Both joined in one step
    assert speakers(game, 0) == [HULDA.name, MIKAEL.name] # Nobody is made to answer
    assert not game.is_running


# Settings apply from the next request

def test_a_rule_changed_during_a_conversation_applies_from_the_next_line(game: FakeGame, model: FakeModel):
    talk(game.config, "", after_npc=GM_DECIDES)
    start(game, "group_conversation", HULDA, MIKAEL, SAADIA)
    since = len(game.lines)
    model.picks, model.also = [HULDA.name, PLAYER.name], [[], [MIKAEL.name]]
    game.say("Hulda, what's the news?")
    assert speakers(game, since) == [HULDA.name]

    old_option(game.config, "group_conversation", EVEN_BY_NPC) # Changed on the Conversation page
    since = len(game.lines)
    model.picks, model.also = [HULDA.name, PLAYER.name, PLAYER.name], [[], [MIKAEL.name], []]
    game.say("Hulda, and then?")
    assert speakers(game, since) == [HULDA.name, MIKAEL.name]


# The options that moved from the MCM

def test_the_shout_options_come_from_mantellas_settings(game: FakeGame, model: FakeModel):
    game.config.shout_max_people, game.config.shout_max_answering, game.config.shout_newcomers_remember = 4, 2, 1
    start(game, "shout", HULDA)
    model.picks = [HULDA.name, PLAYER.name]
    game.say("Evening.")

    state = shout_state(game.manager)
    assert (state.max_people, state.max_answering, state.newcomers_remember) == (4, 2, 1)
    assert state.max_described == 6 # Most People Described still comes from the game


@pytest.mark.parametrize("shout_max_answering, group_max_answering, expected", [
    (1, 5, [HULDA.name]), # Shout's own number applies, not Group Conversation's
    (2, 1, [HULDA.name, MIKAEL.name]),
])
def test_shouts_most_people_answering_at_once_limits_who_answers(game: FakeGame, model: FakeModel, shout_max_answering, group_max_answering, expected):
    game.config.shout_max_answering, game.config.group_conversation_max_answering = shout_max_answering, group_max_answering
    game.around = [] # Nobody nearby, so only the people in the conversation can answer
    start(game, "shout", HULDA, MIKAEL)
    since = len(game.lines)
    model.picks, model.also = [HULDA.name, PLAYER.name, PLAYER.name], [[MIKAEL.name], [], []]
    game.say("Hello everyone.")

    assert speakers(game, since) == expected
    assert game.is_player_turn


@pytest.mark.parametrize("send_history", [True, False])
def test_send_the_conversation_so_far_comes_from_mantellas_settings(game: FakeGame, model: FakeModel, send_history):
    game.config.conversation_event_send_history = send_history
    start(game, "group_conversation", HULDA)
    model.picks = [HULDA.name, PLAYER.name]
    game.say("Lovely weather.")
    model.picks = [HULDA.name]
    game.pick_event("conversation")

    assert ("Lovely weather." in model.event_requests[-1]) == send_history


@pytest.mark.parametrize("include_player", [True, False])
def test_include_me_comes_from_mantellas_settings(game: FakeGame, model: FakeModel, include_player):
    game.config.conversation_event_include_player = include_player
    start(game, "group_conversation", HULDA)
    model.picks = [HULDA.name]
    game.pick_event("conversation")

    request = model.event_requests[-1]
    assert (f"{HULDA.name}, {PLAYER.name}" in request) == include_player


MOVED_KEYS = ["mantella_shout_max_people", "mantella_shout_max_answering", "mantella_shout_newcomers_remember",
              "mantella_event_send_history", "mantella_event_include_player"]


def test_mantella_no_longer_reads_the_moved_options_from_the_game():
    from src.http.communication_constants import communication_constants as comm_consts
    values = {value for name, value in vars(comm_consts).items() if name.startswith("KEY_")}
    assert not values & set(MOVED_KEYS)


def test_the_game_no_longer_sends_the_moved_options():
    import glob
    import os
    spell = os.environ.get("MANTELLA_SPELL_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "Mantella-Spell"))
    scripts = glob.glob(os.path.join(spell, "Scripts", "Source", "Mantella*.psc"))
    if not scripts:
        pytest.skip(f"Mantella-Spell not found at {os.path.abspath(spell)}. Set MANTELLA_SPELL_PATH to its folder")
    for path in scripts:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        assert not [key for key in MOVED_KEYS if key in text], os.path.basename(path)


# The rules of the new talk settings, one test each. The group conversations have five NPCs, so a line can be spoken to several of them.
# The Game Master's pick counts as spoken to whenever anyone else is (as with a real Game Master LLM), so lines spoken to one person
# are scripted with the Game Master picking that person, or someone the line can't be spoken to (the player, or whoever said it)

ALL_FIVE = (HULDA, MIKAEL, SAADIA, YSOLDA, OLFINA)


def group(game: FakeGame, limit: int, *npcs):
    for each_kind in MAX_NPC_TURNS_KINDS: # Each kind of conversation has its own
        setattr(game.config, max_npc_turns_identifier(each_kind), limit)
    start(game, "group_conversation", *(npcs or ALL_FIVE))
    return len(game.lines)


@pytest.mark.parametrize("situation", ["group_conversation", "shout"])
def test_each_kind_of_conversation_stops_at_its_own_max_npc_turns(game: FakeGame, model: FakeModel, situation):
    talk(game.config, "", must=[MUST_NOBODY])
    for each_kind, limit in [("group_conversation", 2), ("shout", 3), ("conversation_event", 4), ("nearby_event", 4), ("random_event", 4)]:
        setattr(game.config, max_npc_turns_identifier(each_kind), limit)
    start(game, situation, HULDA, MIKAEL, SAADIA)
    since = len(game.lines)
    model.picks = [HULDA.name, MIKAEL.name, SAADIA.name, HULDA.name, MIKAEL.name] # The Game Master never gives the player the turn
    game.say("Evening.")

    assert len(speakers(game, since)) == {"group_conversation": 2, "shout": 3}[situation]
    assert game.is_player_turn


@pytest.mark.parametrize("event, limit", [("conversation", 1), ("conversation", 2), ("random", 2)])
def test_the_reaction_to_an_event_stops_at_the_events_own_max_npc_turns(game: FakeGame, model: FakeModel, event, limit):
    talk(game.config, "", must=[MUST_NOBODY])
    for each_kind in MAX_NPC_TURNS_KINDS:
        setattr(game.config, max_npc_turns_identifier(each_kind), 5)
    setattr(game.config, max_npc_turns_identifier(f"{event}_event"), limit)
    start(game, "group_conversation", HULDA, MIKAEL, SAADIA)
    since = len(game.lines)
    model.picks = [HULDA.name, MIKAEL.name, SAADIA.name, HULDA.name, MIKAEL.name]
    game.pick_event(event)

    assert len(speakers(game, since)) == limit
    assert game.is_player_turn


@pytest.mark.parametrize("must, expected", [
    ([MUST_NOBODY], [HULDA.name]), # They answer next sets who replies first, but holds up nothing
    ([MUST_YOU], [HULDA.name, MIKAEL.name]), # Only Who must speak puts people on the list
])
def test_only_who_must_speak_holds_up_the_players_turn(game: FakeGame, model: FakeModel, must, expected):
    talk(game.config, "", must=must)
    since = group(game, 5)
    model.picks, model.also = [HULDA.name, PLAYER.name, PLAYER.name], [[MIKAEL.name]]
    game.say("Hulda, Mikael, what's new?")

    assert speakers(game, since) == expected
    assert game.is_player_turn


def test_everyone_in_the_conversation_counts_as_owed_to_you(game: FakeGame, model: FakeModel):
    """Not dropped by "Only people who haven't answered you yet" at the reply limit"""
    talk(game.config, "", must=[MUST_EVERYONE], at_limit=NOT_ANSWERED_YOU, between=0, rounds=MAX_EXTRA_ROUNDS)
    since = group(game, 1, HULDA, MIKAEL, SAADIA)
    model.picks = [HULDA.name]
    game.say("Evening.")

    assert speakers(game, since) == [HULDA.name, MIKAEL.name, SAADIA.name]


@pytest.mark.parametrize("must, expected", [
    ([MUST_EVERYONE], [HULDA.name, MIKAEL.name, SAADIA.name]),
    ([MUST_EVERYONE, MUST_NPC], [HULDA.name, MIKAEL.name, SAADIA.name, HULDA.name]), # Mikael spoke to Hulda: she has to speak again
])
def test_everyone_and_anyone_an_npc_talks_to_make_someone_who_spoke_answer_again(game: FakeGame, model: FakeModel, must, expected):
    talk(game.config, "", must=must, after_npc=GM_DECIDES)
    since = group(game, 5, HULDA, MIKAEL, SAADIA)
    # Hulda, then Mikael, whose line is spoken to Hulda. Then the Game Master keeps giving the player the turn
    model.picks, model.also = [HULDA.name, MIKAEL.name, PLAYER.name, PLAYER.name, PLAYER.name], [[], [], [HULDA.name]]
    game.say("Evening.")

    assert speakers(game, since) == expected
    assert game.is_player_turn


@pytest.mark.parametrize("must, expected", [
    ([MUST_NOBODY], [HULDA.name, HULDA.name, HULDA.name]),
    ([MUST_YOU], [HULDA.name, HULDA.name, HULDA.name, MIKAEL.name]), # Mikael is part of the direction, so he counts as talked to
])
def test_in_director_mode_the_people_a_direction_is_about_count_as_talked_to(game: FakeGame, model: FakeModel, default_config: ConfigLoader, must, expected):
    default_config.multi_npc_game_master_director_max_npc_turns = 3
    talk(default_config, "director", must=must, between=0, rounds=MAX_EXTRA_ROUNDS)
    start(game, "director", HULDA, MIKAEL, SAADIA)
    since = len(game.lines)
    model.picks, model.also = [HULDA.name] * 5, [[MIKAEL.name]]
    game.say("Hulda and Mikael start arguing about the price of mead.")

    assert speakers(game, since) == expected


@pytest.mark.parametrize("must, expected", [
    ([MUST_NOBODY], [HULDA.name]),
    ([MUST_YOU], [HULDA.name, MIKAEL.name]), # Mikael is part of the event, so he counts as talked to
])
def test_after_an_event_the_people_it_is_about_count_as_talked_to(game: FakeGame, model: FakeModel, must, expected):
    talk(game.config, "", must=must)
    start(game, "group_conversation", HULDA, MIKAEL, SAADIA)
    since = len(game.lines)
    model.after_event, model.also, model.picks = HULDA.name, [[MIKAEL.name]], [PLAYER.name, PLAYER.name]
    game.pick_event("conversation")

    assert speakers(game, since) == expected


def test_each_answers_in_turn_then_the_game_master_picks_freely(game: FakeGame, model: FakeModel):
    talk(game.config, "", must=[MUST_NOBODY], after_you=IN_TURN)
    since = group(game, 5)
    # Spoken to Saadia, Hulda and Mikael. The Game Master keeps picking Saadia: she answers once, then Hulda and Mikael answer.
    # After that the Game Master picks freely, also Hulda again
    model.picks, model.also = [SAADIA.name, SAADIA.name, SAADIA.name, HULDA.name, PLAYER.name], [[HULDA.name, MIKAEL.name]]
    game.say("Saadia, Hulda, Mikael, what's new?")

    assert speakers(game, since) == [SAADIA.name, HULDA.name, MIKAEL.name, HULDA.name]


def test_each_answers_in_turn_the_oldest_group_first(game: FakeGame, model: FakeModel):
    """The design's B, C, E, F, G, H example: you talk to Hulda and Mikael; Hulda talks to Saadia and Ysolda; Mikael talks to Olfina"""
    talk(game.config, "", must=[MUST_NOBODY], after_you=IN_TURN, after_npc=IN_TURN)
    since = group(game, 10)
    model.picks = [HULDA.name, SAADIA.name, OLFINA.name, HULDA.name, HULDA.name, HULDA.name, PLAYER.name]
    model.also = [[MIKAEL.name], [YSOLDA.name], [OLFINA.name]]
    game.say("Hulda, Mikael, what's new?")

    assert speakers(game, since) == [HULDA.name, MIKAEL.name, SAADIA.name, YSOLDA.name, OLFINA.name, HULDA.name]


@pytest.mark.parametrize("after_npc, expected", [
    (NEXT, [HULDA.name, MIKAEL.name, OLFINA.name]), # Saadia should answer next, but Mikael is still waiting his turn: her question is forgotten
    (IN_TURN, [HULDA.name, MIKAEL.name, SAADIA.name]), # Saadia waits her turn behind Mikael
])
def test_people_you_talked_to_go_first_and_an_npcs_they_answer_next_is_forgotten(game: FakeGame, model: FakeModel, after_npc, expected):
    """The design's Lydia, Belethor and Ysolda example: you talk to Hulda and Mikael (each in turn), and Hulda talks to Saadia"""
    talk(game.config, "", must=[MUST_NOBODY], after_you=IN_TURN, after_npc=after_npc)
    since = group(game, 5)
    model.picks, model.also = [HULDA.name, SAADIA.name, OLFINA.name, PLAYER.name], [[MIKAEL.name], [SAADIA.name]]
    game.say("Hulda, Mikael, what's new?")

    assert speakers(game, since) == expected


def test_someone_due_to_answer_two_people_speaks_once(game: FakeGame, model: FakeModel):
    """Hulda and Mikael both talk to Saadia: one reply covers both"""
    talk(game.config, "", must=[MUST_NOBODY], after_you=IN_TURN, after_npc=IN_TURN)
    since = group(game, 10)
    model.picks = [HULDA.name, SAADIA.name, SAADIA.name, HULDA.name, PLAYER.name]
    model.also = [[MIKAEL.name], [SAADIA.name], [SAADIA.name]]
    game.say("Hulda, Mikael, what's new?")

    assert speakers(game, since) == [HULDA.name, MIKAEL.name, SAADIA.name, HULDA.name]


def test_the_game_master_giving_the_player_the_turn_doesnt_stop_each_answers_in_turn(game: FakeGame, model: FakeModel):
    """Nobody has to speak, but you talk to Hulda and Mikael (each in turn): Mikael still answers after Hulda"""
    talk(game.config, "", must=[MUST_NOBODY], after_you=IN_TURN)
    since = group(game, 5)
    model.picks, model.also = [HULDA.name, PLAYER.name, PLAYER.name, PLAYER.name], [[MIKAEL.name]]
    game.say("Hulda, Mikael, what's new?")

    assert speakers(game, since) == [HULDA.name, MIKAEL.name]
    assert game.is_player_turn


def test_the_people_an_npc_talks_to_each_answer_in_turn_before_the_players_turn(game: FakeGame, model: FakeModel):
    """Hulda talks to Saadia and Ysolda, who don't have to speak: after Saadia, the Game Master gives the player the turn, but Ysolda answers first"""
    talk(game.config, "", must=[MUST_YOU], after_npc=IN_TURN)
    since = group(game, 6)
    model.picks, model.also = [HULDA.name, SAADIA.name, PLAYER.name, PLAYER.name, PLAYER.name], [[], [YSOLDA.name, SAADIA.name]]
    game.say("Hulda, what's new?")

    assert speakers(game, since) == [HULDA.name, SAADIA.name, YSOLDA.name]
    assert game.is_player_turn


def test_only_the_reply_limit_stops_a_chain_of_each_answers_in_turn(game: FakeGame, model: FakeModel):
    """Each line is spoken to the next person, who answers it in turn, until the reply limit"""
    talk(game.config, "", must=[MUST_NOBODY], after_npc=IN_TURN)
    since = group(game, 3)
    model.picks = [HULDA.name, MIKAEL.name, SAADIA.name, YSOLDA.name, OLFINA.name]
    model.also = [[], [MIKAEL.name], [SAADIA.name], [YSOLDA.name]]
    game.say("Evening.")

    assert speakers(game, since) == [HULDA.name, MIKAEL.name, SAADIA.name]
    assert game.is_player_turn


@pytest.mark.parametrize("at_limit, expected", [
    (NOT_ANSWERED_YOU, [HULDA.name, HULDA.name, MIKAEL.name]), # Saadia only owes Hulda an answer
    (EVERYONE_LEFT, [HULDA.name, HULDA.name, MIKAEL.name, SAADIA.name]),
    (NOBODY_LEFT, [HULDA.name, HULDA.name]),
])
def test_who_still_speaks_at_the_reply_limit(game: FakeGame, model: FakeModel, at_limit, expected):
    talk(game.config, "", must=[MUST_YOU, MUST_NPC], after_npc=GM_DECIDES, at_limit=at_limit, between=0, rounds=MAX_EXTRA_ROUNDS)
    since = group(game, 2)
    # You talk to Hulda and Mikael. Hulda answers, then talks to Saadia, and the Game Master picks Hulda again: the reply limit of 2
    model.picks, model.also = [HULDA.name, HULDA.name], [[MIKAEL.name], [SAADIA.name]]
    game.say("Hulda, Mikael, what's new?")

    assert speakers(game, since) == expected
    assert game.is_player_turn


def test_the_answer_order_ends_at_the_reply_limit_but_who_still_has_to_speak_lasts(game: FakeGame, model: FakeModel):
    """Hulda talks to Mikael and Saadia, each in turn. Mikael answers at the limit: Saadia's turn in the answer order is gone,
    but she still has to speak, as Anyone an NPC talks to is ticked"""
    talk(game.config, "", must=[MUST_NPC], after_npc=IN_TURN, at_limit=EVERYONE_LEFT, between=0, rounds=MAX_EXTRA_ROUNDS)
    since = group(game, 2)
    model.picks, model.also = [HULDA.name, MIKAEL.name, OLFINA.name], [[], [MIKAEL.name, SAADIA.name]]
    game.say("Evening.")

    assert speakers(game, since) == [HULDA.name, MIKAEL.name, SAADIA.name]


@pytest.mark.parametrize("limit, between, expected", [
    (2, 0, [HULDA.name, HULDA.name, MIKAEL.name, SAADIA.name]), # One after another, nobody replies to them
    (2, 1, [HULDA.name, HULDA.name, MIKAEL.name, HULDA.name, SAADIA.name, HULDA.name]),
    (2, MAX_REPLIES_IN_BETWEEN, [HULDA.name, HULDA.name, MIKAEL.name, HULDA.name, SAADIA.name, HULDA.name]), # Never more than the limit - 1
    (3, 2, [HULDA.name, HULDA.name, HULDA.name, MIKAEL.name, HULDA.name, HULDA.name, SAADIA.name, HULDA.name, HULDA.name]),
])
def test_each_extra_round_is_one_person_who_still_has_to_speak_then_replies_in_between(game: FakeGame, model: FakeModel, limit, between, expected):
    talk(game.config, "", between=between, rounds=MAX_EXTRA_ROUNDS)
    since = group(game, limit)
    model.picks, model.also = [HULDA.name] * 10, [[MIKAEL.name, SAADIA.name]] # The Game Master keeps picking Hulda
    game.say("Hulda, Mikael, Saadia, what's new?")

    assert speakers(game, since) == expected
    assert game.is_player_turn


@pytest.mark.parametrize("rounds, expected", [
    (0, [HULDA.name, HULDA.name]), # A hard cutoff, like "Nobody, it's your turn now"
    (1, [HULDA.name, HULDA.name, MIKAEL.name]), # Saadia doesn't get to speak
    (2, [HULDA.name, HULDA.name, MIKAEL.name, SAADIA.name]),
])
def test_when_the_extra_rounds_run_out_its_the_players_turn(game: FakeGame, model: FakeModel, rounds, expected):
    talk(game.config, "", between=0, rounds=rounds)
    since = group(game, 2)
    model.picks, model.also = [HULDA.name] * 5, [[MIKAEL.name, SAADIA.name]]
    game.say("Hulda, Mikael, Saadia, what's new?")

    assert speakers(game, since) == expected
    assert game.is_player_turn


def test_a_free_pick_of_someone_who_still_has_to_speak_counts_as_their_turn(game: FakeGame, model: FakeModel):
    talk(game.config, "", between=1, rounds=MAX_EXTRA_ROUNDS)
    since = group(game, 2)
    # Mikael speaks in the first extra round, then the Game Master's free pick is Saadia: that is her turn, so no second round
    model.picks, model.also = [HULDA.name, HULDA.name, SAADIA.name, HULDA.name], [[MIKAEL.name, SAADIA.name]]
    game.say("Hulda, Mikael, Saadia, what's new?")

    assert speakers(game, since) == [HULDA.name, HULDA.name, MIKAEL.name, SAADIA.name]
    assert game.is_player_turn


def test_the_player_given_the_turn_during_a_round_ends_its_free_picks_and_the_next_round_starts(game: FakeGame, model: FakeModel):
    talk(game.config, "", between=2, rounds=MAX_EXTRA_ROUNDS)
    since = group(game, 2)
    # Round 1: Mikael, then the Game Master gives the player the turn while Saadia still has to speak: round 2 starts with her.
    # In round 2 nobody is left, so the Game Master giving the player the turn ends the NPCs' turn
    model.picks, model.also = [HULDA.name, HULDA.name, PLAYER.name, PLAYER.name], [[MIKAEL.name, SAADIA.name]]
    game.say("Hulda, Mikael, Saadia, what's new?")

    assert speakers(game, since) == [HULDA.name, HULDA.name, MIKAEL.name, SAADIA.name]
    assert game.is_player_turn


@pytest.mark.parametrize("free_picks, expected", [
    ([SAADIA.name, PLAYER.name], [HULDA.name, HULDA.name, MIKAEL.name, SAADIA.name]), # The second free pick gives the player the turn
    ([PLAYER.name], [HULDA.name, HULDA.name, MIKAEL.name]), # Right away
])
def test_once_nobody_still_has_to_speak_the_rounds_free_picks_still_happen(game: FakeGame, model: FakeModel, free_picks, expected):
    talk(game.config, "", between=2, rounds=MAX_EXTRA_ROUNDS)
    since = group(game, 2)
    model.picks, model.also = [HULDA.name, HULDA.name] + free_picks, [[MIKAEL.name]]
    game.say("Hulda, Mikael, what's new?")

    assert speakers(game, since) == expected
    assert game.is_player_turn


def test_after_the_reply_limit_an_npc_line_sets_who_answers_next_but_nobody_new_has_to_speak(game: FakeGame, model: FakeModel):
    """In the first extra round, Mikael talks to Saadia and Olfina. Saadia answers next (When an NPC talks to someone), but neither
    has to speak before the player's turn, although Anyone an NPC talks to is ticked"""
    talk(game.config, "", must=[MUST_YOU, MUST_NPC], between=2, rounds=MAX_EXTRA_ROUNDS)
    since = group(game, 2)
    model.picks = [HULDA.name, HULDA.name, MIKAEL.name, PLAYER.name]
    model.also = [[MIKAEL.name], [], [SAADIA.name, OLFINA.name]]
    game.say("Hulda, Mikael, what's new?")

    assert speakers(game, since) == [HULDA.name, HULDA.name, MIKAEL.name, SAADIA.name]
    assert game.is_player_turn


def test_director_mode_never_gives_the_player_the_turn_during_the_rounds(game: FakeGame, model: FakeModel, default_config: ConfigLoader):
    default_config.multi_npc_game_master_director_max_npc_turns = 3
    talk(default_config, "director", between=2, rounds=MAX_EXTRA_ROUNDS)
    start(game, "director", HULDA, MIKAEL, SAADIA)
    since, asked = len(game.lines), len(model.game_master_choices)
    model.picks, model.also = [HULDA.name, HULDA.name, HULDA.name, SAADIA.name, HULDA.name], [[MIKAEL.name, SAADIA.name]]
    game.say("Hulda, Mikael and Saadia start arguing about the price of mead.")

    # Round 1: Mikael, then Saadia (her turn) and Hulda as the two replies in between. Nobody is left for round 2
    assert speakers(game, since) == [HULDA.name, HULDA.name, HULDA.name, MIKAEL.name, SAADIA.name, HULDA.name]
    assert not any(PLAYER.name in choices for choices in model.game_master_choices[asked:])


def test_a_watched_npc_event_ends_after_the_rounds_instead_of_giving_the_player_the_turn(game: FakeGame, model: FakeModel, default_config: ConfigLoader):
    default_config.npc_event_max_lines_without_player = 2
    talk(default_config, "npc_event", must=[MUST_NPC], after_npc=GM_DECIDES, at_limit=EVERYONE_LEFT, between=1, rounds=MAX_EXTRA_ROUNDS)
    game.around = [HULDA, MIKAEL, SAADIA]
    # Hulda talks to Mikael, but the Game Master picks Hulda again: the line limit of 2. Mikael answers in the extra round, then Hulda replies
    npc_event(game, model, [HULDA.name, HULDA.name, HULDA.name], [[MIKAEL.name]])

    assert speakers(game, 0) == [HULDA.name, HULDA.name, MIKAEL.name, HULDA.name]
    assert not game.is_running


@pytest.mark.parametrize("most_replies, expected", [
    (1, [HULDA.name, MIKAEL.name, SAADIA.name, OLFINA.name]), # Hulda is due to answer Mikael, but has reached Most Replies per Person
    (100, [HULDA.name, MIKAEL.name, SAADIA.name, HULDA.name]),
])
def test_most_replies_per_person_wins_over_the_answer_order(game: FakeGame, model: FakeModel, most_replies, expected):
    talk(game.config, "", must=[MUST_NOBODY], after_npc=IN_TURN)
    game.config.group_conversation_most_replies_per_person = most_replies
    since = group(game, 10)
    # Hulda talks to Mikael. Mikael talks to Saadia and Hulda, each in turn. Then the Game Master picks Olfina
    model.picks = [HULDA.name, MIKAEL.name, SAADIA.name, OLFINA.name, PLAYER.name, PLAYER.name]
    model.also = [[], [MIKAEL.name], [HULDA.name]]
    game.say("Evening.")

    assert speakers(game, since) == expected


# The in-game test (tests/ingame/talk_settings) reads the "Turn state" line Mantella logs after each reply. Its checks must pass on the lines
# the real code logs

def ingame_check():
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(__file__), "ingame", "talk_settings", "talk_settings_ingame.py")
    spec = importlib.util.spec_from_file_location("talk_settings_ingame", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("settings, picks, also", [
    (dict(after_you=IN_TURN, between=0, rounds=MAX_EXTRA_ROUNDS), [HULDA.name, SAADIA.name], [[MIKAEL.name, SAADIA.name]]),
    (dict(must=[MUST_YOU, MUST_NPC], between=1, rounds=1), [HULDA.name, HULDA.name], [[MIKAEL.name, SAADIA.name], [OLFINA.name]]),
])
def test_the_in_game_checks_pass_on_the_turn_state_lines_mantella_logs(game: FakeGame, model: FakeModel, caplog, settings, picks, also):
    check = ingame_check()
    caplog.set_level(logging.DEBUG)
    talk(game.config, "", **settings)
    group(game, 2)
    model.picks, model.also = picks, also
    game.say("Hulda, Mikael, Saadia, what's new?")

    states = check.turn_states([("INFO", record.getMessage()) for record in caplog.records])
    assert states and states[-1]["next"] == "nobody" and states[0]["settings"] == "shared"
    report = check.Report()
    for turn in check.turns(states):
        check.check_turn(report, turn, "group_conversation")
    assert report.passed


def apply_settings(config: ConfigLoader, settings: dict[str, str], folder):
    """Settings as written in config.ini (text), read the way Mantella reads them"""
    folder.mkdir()
    (folder / "config.ini").write_text("[Conversation]\n" + "".join(f"{key} = {value}\n" for key, value in settings.items()), encoding="utf-8")
    loaded = ConfigLoader(str(folder))
    for key in settings:
        setattr(config, key, getattr(loaded, key))


def play_the_in_game_test(game: FakeGame, model: FakeModel, test_run: str):
    """What MantellaTalkTest.psc does in the game, with a Game Master that never gives the player the turn, so each kind reaches its limit"""
    model.picks = [HULDA.name, MIKAEL.name] * 30
    game.start(HULDA, MIKAEL)
    game.say("Hulda and Mikael, what do you both make of the weather today?")
    model.after_event, model.also = HULDA.name, [[MIKAEL.name]] # The event is about both, so one of them still waits at its limit of 1
    game.pick_event("conversation")
    game.set_shout(True)
    game.say("Saadia, come over here. What do you think of these two?")
    game.end()
    game.around = [HULDA, MIKAEL]
    npc_event(game, model, [HULDA.name, MIKAEL.name] * 10)


@pytest.mark.parametrize("test_run", ["normal", "director"])
def test_the_in_game_checks_pass_on_both_runs_of_the_in_game_test(game: FakeGame, model: FakeModel, caplog, tmp_path, test_run):
    """The in-game test's own settings, played through with the real code: every check passes, and each kind of conversation reaches its limit,
    so the checks of what happens at the limit are tried"""
    check = ingame_check()
    apply_settings(game.config, check.settings_for(test_run), tmp_path / "ingame")
    caplog.set_level(logging.DEBUG)
    play_the_in_game_test(game, model, test_run)

    entries = [(record.levelname, record.getMessage()) for record in caplog.records]
    report = check.check_entries(entries, test_run)
    assert report.passed
    reached = {state["situation"] for state in check.turn_states(entries) if state.get("past_limit")}
    assert reached == set(check.PLAYED[test_run])


@pytest.mark.parametrize("restarted", [True, False])
def test_the_in_game_check_says_when_mantella_wasnt_restarted_after_setup(tmp_path, capsys, restarted):
    """Mantella only reads config.ini when it starts: without a start after setup, it ran with other settings than the test's"""
    check = ingame_check()
    (tmp_path / check.START_FILE).write_text("2026-11-13 16:55:00\ndirector", encoding="utf-8")
    state = '{"spoke": "Hulda", "next": "nobody", "situation": "group_conversation", "settings": "shared", "past_limit": false, "reply_limit": 3}'
    lines = ["2026-11-13 16:52:37,752 INFO: Mantella.exe running in: \n    D:\\Workspace\\Mantella"] # Before setup
    if restarted:
        lines.append("2026-11-13 16:56:00,000 INFO: Mantella.exe running in: \n    D:\\Workspace\\Mantella")
    lines.append(f"2026-11-13 16:59:14,570 INFO: Turn state: {state}")
    (tmp_path / "logging.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    check.check(str(tmp_path))
    out = capsys.readouterr().out
    assert ("Mantella didn't start after setup" in out) is not restarted
    assert ("The director run" in out) is restarted # Only then are the turns checked


def test_the_in_game_check_doesnt_join_a_cut_short_turn_to_the_next_one(capsys):
    """The test ends a conversation it has waited too long for (a slow model), so its last turn has no end. The watched NPC Event after it is still
    a turn of its own: from Mantella's log of a real run"""
    check = ingame_check()
    def state(spoke, next_speaker, situation, past_limit=False, still=()):
        return {"spoke": spoke, "next": next_speaker, "situation": situation, "settings": situation, "still_to_speak": list(still), "answer_order": [],
                "past_limit": past_limit, "round": 0, "extra_rounds": 20, "free_picks_left": 0, "replies_in_between": 1,
                "reply_limit": 4}
    states = [state("Lyne", "Lyne", "shout", still=["Lyne"]), # Cut short here
              state("Siv", "Siv", "npc_event"), state("Siv", "Yrsa", "npc_event"), state("Yrsa", "Siv", "npc_event"),
              state("Siv", "nobody", "npc_event", past_limit=True)]
    found = check.turns(states)
    assert [[s["spoke"] for s in turn] for turn in found] == [["Lyne"], ["Siv", "Siv", "Yrsa", "Siv"]]
    report = check.Report()
    check.check_turn(report, found[1], "npc_event", {"settings": "npc_event", "reply_limit": 4, "ends_at_limit": False})
    check.check_turn(report, found[0], "shout", {"settings": "shout", "reply_limit": 4, "ends_at_limit": False})
    assert report.passed
    assert "INFO shout: cut short" in capsys.readouterr().out


@pytest.mark.parametrize("other, failed", [
    ({"group_conversation_max_npc_turns": "4"}, "group_conversation turn 1: its own limit of NPC replies in a row is used (3)"),
    ({"group_conversation_use_own_talk_settings": "True"}, "group_conversation turn 1: uses the shared talk settings"),
    ({"conversation_event_who_still_speaks_at_reply_limit": "Everyone who still has to speak"},
     "conversation_event turn 1: \"Nobody, it's your turn now\": nobody else speaks once the limit is reached"),
])
def test_the_in_game_checks_fail_when_mantella_doesnt_follow_the_settings(game: FakeGame, model: FakeModel, caplog, capsys, tmp_path, other, failed):
    """Not checks that always pass: when Mantella runs with another setting than the test wrote, the check of it fails"""
    check = ingame_check()
    apply_settings(game.config, {**check.settings_for("normal"), **other}, tmp_path / "ingame")
    caplog.set_level(logging.DEBUG)
    play_the_in_game_test(game, model, "normal")

    assert not check.check_entries([(record.levelname, record.getMessage()) for record in caplog.records], "normal").passed
    assert f"FAIL {failed}" in capsys.readouterr().out
