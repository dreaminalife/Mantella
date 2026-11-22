"""Sorting each line: into the running conversation, or kept as a heard line"""
import logging
from types import SimpleNamespace

import pytest

from src.heard_lines.heard_line import GameTime, HeardLine, HeardPerson
from src.heard_lines.heard_lines_receiver import GameHeardLinesReceiver
from src.heard_lines.heard_lines_store import HeardLinesStore
from tests.heard_lines.lines import CARLOTTA, HULDA, MIKAEL, REF, SAADIA, WORLD, at, hand_over, menu_line, scene_line, settings

START = 1000 # When the conversation started


class FakeStore(HeardLinesStore):
    def __init__(self):
        self.kept: dict[str, list[str]] = {}
        self.times: list[GameTime] = []

    def note_now(self, world_id, now):
        self.times.append(now)

    def add(self, world_id, listener, lines):
        assert world_id == WORLD
        self.kept.setdefault(listener.name, []).extend(line.words for line in lines)

    def lines_for(self, world_id, name, ref_id):
        return []

    def latest_now(self, world_id):
        return None


class FakeConversation:
    """What the receiver reads from Conversation"""
    def __init__(self, *names: str, passes: bool = True, stopping: bool = False, ended: bool = False):
        self.ref_ids = {REF[name] for name in names}
        self.passes = passes
        self.is_stopping = stopping
        self.has_already_ended = ended
        self.start_ts = START
        self.heard: list[HeardLine] = []

    def passes_ingame_events(self) -> bool:
        return self.passes

    def contains_character(self, ref_id: str) -> bool:
        return ref_id in self.ref_ids

    def add_heard_dialogue(self, lines):
        self.heard.extend(lines)


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


def receive(store, text, conversation=None, config=None):
    GameHeardLinesReceiver(config or settings(), store).receive(text, WORLD, conversation)


def test_a_line_heard_in_the_conversation_goes_into_it_once_and_outsiders_keep_it(store):
    talk = FakeConversation("Mikael", "Carlotta")
    receive(store, hand_over(scene_line("Hello.", time=START + 1)), talk)
    [line] = talk.heard
    assert line.words == "Hello." and [p.name for p in line.heard_by] == ["Mikael", "Carlotta"] # Only the people in the conversation
    assert store.kept == {"Saadia": ["Hello."]}


def test_a_one_on_one_conversation_still_hears(store):
    talk = FakeConversation("Saadia")
    receive(store, hand_over(scene_line("Hello.", time=START + 1)), talk)
    assert [p.name for p in talk.heard[0].heard_by] == ["Saadia"]
    assert store.kept == {"Mikael": ["Hello."], "Carlotta": ["Hello."]}


def test_a_line_caught_before_the_conversation_started_is_kept_as_a_heard_line(store):
    talk = FakeConversation("Saadia")
    receive(store, hand_over(scene_line("Before.", time=START - 1)), talk)
    assert not talk.heard and store.kept["Saadia"] == ["Before."]


def test_without_a_conversation_everything_is_kept(store):
    receive(store, hand_over(scene_line("Hello.", time=START + 1)), None) # A start, or the NPC Initiative and NPC Event checks
    assert store.kept == {"Mikael": ["Hello."], "Carlotta": ["Hello."], "Saadia": ["Hello."]}


@pytest.mark.parametrize("talk", [
    FakeConversation("Saadia", passes=False),        # Radiant, or an NPC Event the player only watches
    FakeConversation("Saadia", stopping=True),       # The game ended it after sending this request
    FakeConversation("Saadia", ended=True),
])
def test_a_conversation_that_passes_nothing_on_keeps_lines_as_heard_lines(store, talk):
    receive(store, hand_over(scene_line("Hello.", time=START + 1)), talk)
    assert not talk.heard and store.kept["Saadia"] == ["Hello."]


def test_someone_who_joins_in_this_request_keeps_the_line_as_a_heard_line(store):
    talk = FakeConversation("Mikael") # Saadia's join comes with this request's actors, after the lines are sorted
    receive(store, hand_over(scene_line("Hello.", time=START + 1)), talk)
    assert "Saadia" in store.kept and "Mikael" not in store.kept


def test_someone_who_leaves_in_this_request_still_hears_the_line_in_the_conversation(store):
    talk = FakeConversation("Mikael", "Saadia") # Saadia's leave comes with this request's actors, after the lines are sorted
    receive(store, hand_over(scene_line("Hello.", time=START + 1)), talk)
    assert [p.name for p in talk.heard[0].heard_by] == ["Mikael", "Saadia"]
    assert "Saadia" not in store.kept


def test_a_watched_npc_event_passes_nothing_on_until_the_player_joins():
    """The real conversation types behind FakeConversation.passes"""
    from tests.test_npc_event import event, event_conversation
    scene = event()
    talk = event_conversation(scene)
    assert not talk.passes_ingame_events() # The player only watches
    scene.player_joined = True
    talk.context.npcs_in_conversation.contains_player_character.return_value = True
    assert talk.passes_ingame_events()


def test_a_radiant_conversation_passes_nothing_on():
    from tests.test_npc_event import event, event_conversation
    talk = event_conversation(event())
    talk.context.npc_event = None # Two NPCs, no player
    assert not talk.passes_ingame_events()


def test_the_game_time_is_noted_before_lines_are_kept(store):
    receive(store, hand_over(scene_line("Hi."), now=at(13)))
    assert store.times == [GameTime(at(13)["day"], 13)]
    receive(store, hand_over(scene_line("Hi.")))
    assert len(store.times) == 1 # No time, nothing to check


@pytest.mark.parametrize("text", [None, "", "{broken"])
def test_nothing_readable_does_nothing(store, text):
    receive(store, text, FakeConversation("Saadia"))
    assert store.kept == {} and store.times == []


@pytest.mark.parametrize("patterns, asked, dropped", [
    (["stage1hello"], "Stage1Hello", True),                       # Exact, any case
    (["*orders for you*"], "Other aspects & orders for you.", True),
    (["*orders for you*"], "What orders do you have?", False),
    (["goodbye. (remove from mantella conversation)"], "Goodbye. (Remove from Mantella conversation)", True),
    ([], "Stage1Hello", False),
])
def test_lines_that_only_open_a_menu_are_dropped(store, patterns, asked, dropped):
    receive(store, hand_over(menu_line(asked, "As you wish.", heard_by=[HULDA, SAADIA])), config=settings(menu_only=patterns))
    assert (store.kept == {}) == dropped


def test_a_menu_only_pattern_never_drops_scene_lines(store):
    receive(store, hand_over(scene_line("Stage1Hello")), config=settings(menu_only=["*"]))
    assert store.kept["Saadia"] == ["Stage1Hello"]


def test_the_old_add_ons_list_is_the_default(default_config):
    assert default_config.heard_lines_menu_only == ["stage1hello", "i want you to..", "dialoguegenerichello", "goodbye. (remove from mantella conversation)"]


def test_one_npc_that_cant_be_written_loses_no_one_elses_lines(store):
    """A name that can't be a folder, or a full disk: only that NPC's lines are lost"""
    add = store.add

    def add_failing_for_carlotta(world_id, listener, lines):
        if listener.name == "Carlotta":
            raise OSError("The filename, directory name, or volume label syntax is incorrect")
        add(world_id, listener, lines)

    store.add = add_failing_for_carlotta
    talk = FakeConversation("Mikael")
    receive(store, hand_over(scene_line("Hello.", time=START + 1)), talk)
    assert [line.words for line in talk.heard] == ["Hello."]
    assert store.kept == {"Saadia": ["Hello."]}


def full_log(caplog):
    """Vanilla Dialogues at Full: the lines written out for each heard line"""
    caplog.set_level(logging.DEBUG, logger="mantella.vanilla_dialogues")
    return lambda: [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]


def test_at_full_each_heard_line_is_written_out_with_where_it_went(store, caplog):
    written = full_log(caplog)
    receive(store, hand_over(scene_line("Hello.", time=START + 1)), FakeConversation("Mikael"))
    [text] = written()
    assert 'Mikael to Carlotta: "Hello." in The Bannered Mare. Heard by 3 (Mikael, Carlotta, Saadia). Kit was there.' in text
    assert text.endswith("Into the conversation for Mikael; kept as a heard line by the other 2.")


def test_at_full_menu_talk_shows_the_question_and_a_left_out_pick_says_why(store, caplog):
    written = full_log(caplog)
    receive(store, hand_over(menu_line("Heard any rumors?", "Plenty."), menu_line("Stage1Hello", "Hm?")), config=settings(menu_only=["stage1hello"]))
    kept, left_out = sorted(written(), key=lambda text: "Left out" in text)
    assert 'Hulda, answering Kit\'s "Heard any rumors?": "Plenty."' in kept and kept.endswith("Kept by all 1 as a heard line.")
    assert '"Stage1Hello"' in left_out and "Left out: the pick only opens a menu" in left_out


def test_below_full_no_line_is_written_out(store, caplog):
    caplog.set_level(logging.INFO, logger="mantella.vanilla_dialogues")
    receive(store, hand_over(scene_line("Hello.")))
    assert not [r for r in caplog.records if r.levelno == logging.DEBUG]
