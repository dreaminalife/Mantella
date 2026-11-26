"""Sorting each line: into the running conversation, or kept as a heard line"""
import logging
from types import SimpleNamespace

import pytest

from src.heard_lines.heard_line import GameTime, HeardLine, HeardPerson
from src.heard_lines.heard_lines_receiver import GameHeardLinesReceiver
from src.heard_lines.heard_lines_store import HeardLinesStore
from src.heard_lines.vanilla_dialogue_lists import ShippedLists
from tests.heard_lines.lines import (CARLOTTA, DIBELLA_QUEST, DRAGON_NEWS_SCENE, FOLLOWING_MEETING_QUEST, HELPER_QUEST, HULDA, JOURNAL_MEETING_QUEST,
                                    MEETING_QUEST, MIKAEL, ONCE_MEETING_QUEST, ONCE_QUEST, PLAYER, REF, SAADIA, SONG_SCENE, STORY_QUEST, TAVERN_QUEST,
                                    TOWN_QUEST, TOWN_STORY_QUEST, WORLD, at, hand_over, menu_line, scene_line, settings)

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


def receive(store, text, conversation=None, config=None, lists: ShippedLists | None = None):
    """Without Mantella's own list files, unless the test gives some"""
    GameHeardLinesReceiver(config or settings(), store, lists or ShippedLists()).receive(text, WORLD, conversation)


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
    (["i want you to.."], "I Want You To..", True),                      # Exact, any case
    (["*orders for you*"], "Other aspects & orders for you.", True),
    (["*orders for you*"], "What orders do you have?", False),
    (["goodbye. (remove from mantella conversation)"], "Goodbye. (Remove from Mantella conversation)", True),
    ([], "I want you to..", False),
])
def test_lines_that_only_open_a_menu_are_dropped(store, patterns, asked, dropped):
    receive(store, hand_over(menu_line(asked, "As you wish.", heard_by=[HULDA, SAADIA])), config=settings(menu_only=patterns))
    assert (store.kept == {}) == dropped


def test_a_menu_only_pattern_never_drops_scene_lines(store):
    receive(store, hand_over(scene_line("I want you to..")), config=settings(menu_only=["*"]))
    assert store.kept["Saadia"] == ["I want you to.."]


def test_menu_talk_off_leaves_out_menu_talk_and_keeps_scenes(store):
    talk = FakeConversation("Hulda", "Mikael")
    receive(store, hand_over(menu_line("Heard any rumors?", "Plenty.", time=START + 1), menu_line("", "Never seen you before.", time=START + 1),
                             scene_line("Hello.", time=START + 1)), talk, config=settings(menu_talk=False))
    assert [line.words for line in talk.heard] == ["Hello."]
    assert store.kept == {"Carlotta": ["Hello."], "Saadia": ["Hello."]}


def test_scenes_off_leaves_out_scenes_and_keeps_menu_talk(store):
    talk = FakeConversation("Hulda", "Mikael")
    receive(store, hand_over(scene_line("Hello.", time=START + 1), scene_line("Hey, you.", to=PLAYER, scene=0, time=START + 1),
                             menu_line("Heard any rumors?", "Plenty.", time=START + 1)), talk, config=settings(scenes=False))
    assert [line.words for line in talk.heard] == ["Plenty."]
    assert store.kept == {}


@pytest.mark.parametrize("ignored", [["mikael"], ["MIK*"], ["carlotta"]]) # The speaker, or the one spoken to
def test_an_ignored_npcs_lines_are_left_out_for_everyone(store, ignored):
    talk = FakeConversation("Saadia")
    receive(store, hand_over(scene_line("Hello.", time=START + 1), scene_line("Hi.", speaker=HULDA, to=SAADIA, heard_by=[HULDA, SAADIA], time=START + 1)), talk,
            config=settings(ignored_npcs=ignored))
    assert [line.words for line in talk.heard] == ["Hi."]
    assert store.kept == {"Hulda": ["Hi."]}


def test_an_ignored_npc_still_overhears_other_peoples_scenes(store):
    receive(store, hand_over(scene_line("Hello.")), config=settings(ignored_npcs=["saadia"]))
    assert store.kept == {"Mikael": ["Hello."], "Carlotta": ["Hello."], "Saadia": ["Hello."]}


def test_an_ignored_npcs_menu_talk_is_left_out(store):
    receive(store, hand_over(menu_line("Heard any rumors?", "Plenty.", heard_by=[HULDA, SAADIA])), config=settings(ignored_npcs=["hulda"]))
    assert store.kept == {}


@pytest.mark.parametrize("config, why", [
    (settings(menu_talk=False), "Left out: dialogue menu talk is off (Remember Dialogue Menu Talk)"),
    (settings(ignored_npcs=["hulda"]), "Left out: Hulda is on Ignored NPCs"),
])
def test_at_full_a_line_left_out_by_a_setting_says_which(store, caplog, config, why):
    written = full_log(caplog)
    receive(store, hand_over(menu_line("Heard any rumors?", "Plenty.")), config=config)
    [text] = written()
    assert text.endswith(why)


def test_at_full_unprompted_menu_talk_shows_no_empty_question(store, caplog):
    """A forced greeting, or a line that carries on by itself: no menu choice led to it"""
    written = full_log(caplog)
    receive(store, hand_over(menu_line("", "Never done an honest day's work.")))
    [text] = written()
    assert 'Hulda to Kit: "Never done an honest day\'s work."' in text and 'answering' not in text


def test_at_full_a_scene_left_out_says_scenes_are_off(store, caplog):
    written = full_log(caplog)
    receive(store, hand_over(scene_line("Hello.")), config=settings(scenes=False))
    [text] = written()
    assert text.endswith("Left out: scenes are off (Remember Scenes)")


@pytest.mark.parametrize("quest, kept", [
    (MEETING_QUEST, False),        # Small talk from two NPCs meeting
    (JOURNAL_MEETING_QUEST, True), # Two NPCs meeting, but in a quest the player follows
    (ONCE_MEETING_QUEST, True),    # Two NPCs meeting, but it runs once
    (STORY_QUEST, True),
    (DIBELLA_QUEST, True),         # A journal quest with no journal category
    (HELPER_QUEST, True),          # A story scene with no journal entry, like Wuunferth's arrest
    (ONCE_QUEST, True),
    (TOWN_QUEST, True),            # Repeats, but only a list leaves it out
    (TAVERN_QUEST, True),
    (FOLLOWING_MEETING_QUEST, True), # Two NPCs meeting, but only after a step of The Heart of Dibella
    (None, True),                  # No quest: a plugin from before quests were sent
])
def test_by_default_only_small_talk_from_two_npcs_meeting_is_left_out(store, quest, kept):
    receive(store, hand_over(scene_line("Hello.", quest=quest)))
    assert (store.kept != {}) == kept


def test_remember_scenes_when_npcs_meet_keeps_their_small_talk(store):
    receive(store, hand_over(scene_line("Did you raise your prices?", quest=MEETING_QUEST)), config=settings(npcs_meet=True))
    assert store.kept["Saadia"] == ["Did you raise your prices?"]


def test_background_chatter_is_left_out_of_the_conversation_too(store):
    talk = FakeConversation("Saadia")
    receive(store, hand_over(scene_line("Did you raise your prices?", quest=MEETING_QUEST, time=START + 1),
                             scene_line("I've been ordered to arrest you.", quest=STORY_QUEST, time=START + 1)), talk)
    assert [line.words for line in talk.heard] == ["I've been ordered to arrest you."]
    assert store.kept == {"Mikael": ["I've been ordered to arrest you."], "Carlotta": ["I've been ordered to arrest you."]}


@pytest.mark.parametrize("story_quests", [["dialoguewhiteruncarlotta*"], ["dialoguewhiteruncarlottaolfinascene1"], ["*"]])
def test_quests_that_arent_background_chatter_are_remembered(store, story_quests):
    receive(store, hand_over(scene_line("Did you raise your prices?", quest=MEETING_QUEST)), config=settings(story_quests=story_quests))
    assert store.kept["Saadia"] == ["Did you raise your prices?"]


@pytest.mark.parametrize("npcs_meet", [False, True])
def test_quests_that_are_background_chatter_are_left_out_whatever_the_switch(store, npcs_meet):
    receive(store, hand_over(scene_line("Talos is a god.", quest=TOWN_QUEST), scene_line("I've been ordered to arrest you.", quest=STORY_QUEST)),
            config=settings(chatter_quests=["dialoguewhiterunheimskr*"], npcs_meet=npcs_meet))
    assert store.kept["Saadia"] == ["I've been ordered to arrest you."]


def test_a_quest_on_both_lists_is_remembered(store):
    receive(store, hand_over(scene_line("Welcome.", quest=TAVERN_QUEST)), config=settings(chatter_quests=["wi*"], story_quests=["witavern"]))
    assert store.kept["Saadia"] == ["Welcome."]


def test_the_quest_lists_never_touch_menu_talk(store):
    line = {**menu_line("Heard any rumors?", "Plenty.", heard_by=[HULDA, SAADIA]), "quest": MEETING_QUEST}
    receive(store, hand_over(line), config=settings(chatter_quests=["*"]))
    assert store.kept["Saadia"] == ["Plenty."]


OURS = ShippedLists(menu_choices=("i want you to..",), story_quests=("dialoguewhiteruncarlottaolfinascene1",),
                    chatter_quests=("witavern", "dialoguewhiterunheimskrpreachscene"))


def test_mantellas_own_lists_work_without_any_setting(store):
    receive(store, hand_over(scene_line("Did you raise your prices?", quest=MEETING_QUEST), scene_line("Welcome.", quest=TAVERN_QUEST),
                             scene_line("Talos is a god.", quest=TOWN_QUEST), menu_line("I want you to..", "Hm?", heard_by=[HULDA, SAADIA])), lists=OURS)
    assert store.kept["Saadia"] == ["Did you raise your prices?"]


def test_the_players_entries_add_to_mantellas(store):
    receive(store, hand_over(menu_line("I want you to..", "Hm?", heard_by=[HULDA, SAADIA]), menu_line("Show me your wares.", "Take a look.", heard_by=[HULDA, SAADIA])),
            config=settings(menu_only=["show me your wares."]), lists=OURS)
    assert store.kept == {}


@pytest.mark.parametrize("config, kept", [
    (settings(menu_only=["-i want you to.."]), ["Hm?"]),                 # Remembered after all
    (settings(menu_only=["-i want*"]), ["Hm?"]),                         # A minus entry matches like any other
    (settings(menu_only=["-show me your wares."]), []),                 # Switching off something not on Mantella's list does nothing
])
def test_a_minus_switches_off_mantellas_menu_choice(store, config, kept):
    receive(store, hand_over(menu_line("I want you to..", "Hm?", heard_by=[HULDA, SAADIA])), config=config, lists=OURS)
    assert store.kept.get("Saadia", []) == kept


def test_a_minus_on_a_quest_list_sends_mantellas_quest_back_to_the_rule(store):
    # Off Mantella's story list: small talk from two NPCs meeting, so left out with the switch off and kept with it on
    receive(store, hand_over(scene_line("Did you raise your prices?", quest=MEETING_QUEST)), config=settings(story_quests=["-dialoguewhiterun*"]), lists=OURS)
    assert store.kept == {}
    receive(store, hand_over(scene_line("Did you raise your prices?", quest=MEETING_QUEST)), config=settings(story_quests=["-dialoguewhiterun*"], npcs_meet=True),
            lists=OURS)
    assert store.kept["Saadia"] == ["Did you raise your prices?"]
    # Off Mantella's chatter list: kept, as the rule doesn't touch it
    store.kept.clear()
    receive(store, hand_over(scene_line("Welcome.", quest=TAVERN_QUEST), scene_line("Talos is a god.", quest=TOWN_QUEST)), config=settings(chatter_quests=["-*"]),
            lists=OURS)
    assert store.kept["Saadia"] == ["Welcome.", "Talos is a god."]


def test_the_players_entries_win_over_mantellas(store):
    receive(store, hand_over(scene_line("Did you raise your prices?", quest=MEETING_QUEST), scene_line("Welcome.", quest=TAVERN_QUEST)),
            config=settings(chatter_quests=["dialoguewhiterun*"], story_quests=["witavern"], npcs_meet=True), lists=OURS)
    assert store.kept["Saadia"] == ["Welcome."]


@pytest.mark.parametrize("quest, config, lists, why", [
    (MEETING_QUEST, settings(), None, "Left out: background chatter: it starts when two NPCs meet, and Remember Scenes When NPCs Meet is off"),
    (TAVERN_QUEST, settings(chatter_quests=["wi*"]), None, "Left out: background chatter: it is on Quests That Are Background Chatter"),
    (TAVERN_QUEST, settings(), OURS, "Left out: background chatter: it is on Mantella's own Quests That Are Background Chatter"),
])
def test_at_full_background_chatter_left_out_names_its_quest_and_why(store, caplog, quest, config, lists, why):
    written = full_log(caplog)
    receive(store, hand_over(scene_line("Hello.", quest=quest)), config=config, lists=lists)
    [text] = written()
    assert f"Quest {quest['id']}." in text and text.endswith(why)


def test_at_full_a_kept_scene_line_names_its_quest(store, caplog):
    written = full_log(caplog)
    receive(store, hand_over(scene_line("I've been ordered to arrest you.", quest=STORY_QUEST)))
    [text] = written()
    assert "Kit was there. Quest MS11. Kept by all 3 as a heard line." in text


def test_small_talk_that_follows_a_quest_is_still_left_out_when_it_is_on_the_chatter_list(store):
    receive(store, hand_over(scene_line("I still miss her.", quest=FOLLOWING_MEETING_QUEST)), config=settings(chatter_quests=["dialoguekarthwasten*"]))
    assert store.kept == {}


def song_and_dragon_news() -> str:
    """Two scenes of one town quest: Mikael's song, and the Jarl hearing about the dragon"""
    return hand_over(scene_line("One more song, what say you?", quest=TOWN_STORY_QUEST, scene_key=SONG_SCENE),
                     scene_line("And you're sure?", quest=TOWN_STORY_QUEST, scene_key=DRAGON_NEWS_SCENE))


@pytest.mark.parametrize("config, lists", [
    (settings(chatter_quests=["skyrim.esm:092a31"]), None),
    (settings(chatter_quests=["skyrim.esm:092a3*"]), None), # * works in a scene entry too
    (settings(), ShippedLists(chatter_quests=("skyrim.esm:092a31",))),
])
def test_a_scene_entry_leaves_out_only_that_scene(store, config, lists):
    receive(store, song_and_dragon_news(), config=config, lists=lists)
    assert store.kept["Saadia"] == ["And you're sure?"]


def test_a_scene_wins_over_its_quest_on_the_other_list(store):
    # The quest is story, but one of its scenes is chatter
    receive(store, song_and_dragon_news(), config=settings(story_quests=["dialoguewhiterun"], chatter_quests=["skyrim.esm:092a31"]))
    assert store.kept["Saadia"] == ["And you're sure?"]
    # The quest is chatter, but one of its scenes is story, also when only Mantella's list names the scene
    store.kept.clear()
    receive(store, song_and_dragon_news(), config=settings(chatter_quests=["dialoguewhiterun"]), lists=ShippedLists(story_quests=("skyrim.esm:09dcd5",)))
    assert store.kept["Saadia"] == ["And you're sure?"]


def test_a_wildcard_quest_entry_never_decides_ahead_of_a_scene_entry(store):
    # * on the chatter list is a quest entry: Mantella's story scene entry still keeps that scene
    receive(store, song_and_dragon_news(), config=settings(chatter_quests=["*"]), lists=ShippedLists(story_quests=("skyrim.esm:09dcd5",)))
    assert store.kept["Saadia"] == ["And you're sure?"]


def test_a_scene_on_both_lists_is_remembered_and_the_players_entry_wins(store):
    receive(store, song_and_dragon_news(), config=settings(story_quests=["skyrim.esm:092a31"], chatter_quests=["skyrim.esm:092a31"]))
    assert store.kept["Saadia"] == ["One more song, what say you?", "And you're sure?"]
    store.kept.clear()
    receive(store, song_and_dragon_news(), config=settings(story_quests=["skyrim.esm:092a31"]), lists=ShippedLists(chatter_quests=("skyrim.esm:092a31",)))
    assert store.kept["Saadia"] == ["One more song, what say you?", "And you're sure?"]


@pytest.mark.parametrize("off", ["-skyrim.esm:092a31", "-*"])
def test_a_minus_switches_off_mantellas_scene_entry(store, off):
    receive(store, song_and_dragon_news(), config=settings(chatter_quests=[off]), lists=ShippedLists(chatter_quests=("skyrim.esm:092a31",)))
    assert store.kept["Saadia"] == ["One more song, what say you?", "And you're sure?"]


def test_a_line_without_a_scene_is_decided_by_its_quest(store):
    receive(store, hand_over(scene_line("Welcome.", quest=TAVERN_QUEST)), config=settings(chatter_quests=["skyrim.esm:*"]))
    assert store.kept["Saadia"] == ["Welcome."]


@pytest.mark.parametrize("config, lists, why", [
    (settings(chatter_quests=["skyrim.esm:092a31"]), None, "Left out: background chatter: its scene is on Quests That Are Background Chatter"),
    (settings(), ShippedLists(chatter_quests=("skyrim.esm:092a31",)), "Left out: background chatter: its scene is on Mantella's own Quests That Are Background Chatter"),
])
def test_at_full_a_scene_left_out_says_its_scene_is_on_the_list(store, caplog, config, lists, why):
    written = full_log(caplog)
    receive(store, hand_over(scene_line("One more song, what say you?", quest=TOWN_STORY_QUEST, scene_key=SONG_SCENE)), config=config, lists=lists)
    [text] = written()
    assert text.endswith(why)


def test_at_full_a_scene_line_ends_with_its_quest_and_scene_and_menu_talk_with_neither(store, caplog):
    written = full_log(caplog)
    receive(store, hand_over(scene_line("One more song, what say you?", quest=TOWN_STORY_QUEST, scene_key=SONG_SCENE), scene_line("No scene."),
                             menu_line("Any rumors?", "Not today.", heard_by=[HULDA, SAADIA])))
    song, no_scene, menu = written()
    assert "Kit was there. Quest DialogueWhiterun. Scene Skyrim.esm:092A31. Kept by all 3 as a heard line." in song
    assert "Scene " not in no_scene and "Quest " not in no_scene
    assert "Scene " not in menu and "Quest " not in menu


def test_the_settings_start_empty_and_the_switch_off(default_config):
    assert default_config.heard_lines_menu_only == [] and default_config.heard_lines_story_quests == [] and default_config.heard_lines_chatter_quests == []
    assert not default_config.heard_lines_npcs_meet


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
    receive(store, hand_over(menu_line("Heard any rumors?", "Plenty."), menu_line("I want you to..", "Hm?")), config=settings(menu_only=["i want you to.."]))
    kept, left_out = sorted(written(), key=lambda text: "Left out" in text)
    assert 'Hulda, answering Kit\'s "Heard any rumors?": "Plenty."' in kept and kept.endswith("Kept by all 1 as a heard line.")
    assert '"I want you to.."' in left_out and "Left out: the pick only opens a menu" in left_out


def test_below_full_no_line_is_written_out(store, caplog):
    caplog.set_level(logging.INFO, logger="mantella.vanilla_dialogues")
    receive(store, hand_over(scene_line("Hello.")))
    assert not [r for r in caplog.records if r.levelno == logging.DEBUG]
