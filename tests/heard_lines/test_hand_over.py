"""Reading what the game hands over"""
import json
import logging

import pytest

from src.heard_lines.heard_line import GameTime, HandOver, HeardLine, HeardPerson
from tests.heard_lines.lines import CARLOTTA, MIKAEL, PLAYER, REF, SAADIA, at, hand_over, menu_line, scene_line


@pytest.mark.parametrize("text", [None, "", "   ", 42])
def test_nothing_handed_over_is_nothing(text, caplog):
    with caplog.at_level(logging.WARNING):
        assert HandOver.parse(text) is None
    assert not caplog.records # The plugin not being installed is normal


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '"a text"'])
def test_broken_json_is_logged_and_skipped(text, caplog):
    with caplog.at_level(logging.WARNING):
        assert HandOver.parse(text) is None
    assert "could not read the heard lines" in caplog.text


def test_a_scene_line_is_read_in_full():
    result = HandOver.parse(hand_over(scene_line("Your beauty outshines the sun.", time=1763600000), now=at(14.5)))
    assert result and result.now == GameTime(42 + 14.5 / 24, 14.5)
    [line] = result.lines
    assert line.time == 1763600000 and line.place == "The Bannered Mare" and line.scene == 0xABCDE
    assert line.speaker == HeardPerson(REF["Mikael"], "Mikael") and line.to == HeardPerson(REF["Carlotta"], "Carlotta")
    assert line.words == "Your beauty outshines the sun." and line.asked == "" and not line.is_menu_talk
    assert [p.name for p in line.heard_by] == ["Mikael", "Carlotta", "Saadia"]
    assert line.player_there and line.player == "Kit"


def test_menu_talk_keeps_the_players_pick():
    [line] = HandOver.parse(hand_over(menu_line("What news from the war?", "The Stormcloaks are losing."))).lines
    assert line.is_menu_talk and line.asked == "What news from the war?" and line.words == "The Stormcloaks are losing."


def test_a_scene_line_without_a_scene_is_not_menu_talk():
    """The speaker had just left the scene: only the menu flag makes a line menu talk"""
    [line] = HandOver.parse(hand_over(scene_line("Leave me be.", scene=0))).lines
    assert not line.is_menu_talk and line.to.name == "Carlotta"


def test_without_now_the_time_is_unknown():
    assert HandOver.parse(hand_over(scene_line("Hello."))).now is None


@pytest.mark.parametrize("broken", [
    {"words": "No speaker.", "day": 1, "hour": 1},
    {"speaker": MIKAEL, "words": "", "day": 1, "hour": 1},
    {"speaker": MIKAEL, "words": "No time."},
    {"speaker": {"id": "x", "name": "Mikael"}, "words": "Bad id.", "day": 1, "hour": 1},
    {"speaker": {"id": 5}, "words": "No name.", "day": 1, "hour": 1},
    "not a line",
])
def test_unreadable_lines_are_skipped_and_the_rest_kept(broken):
    result = HandOver.parse(json.dumps({"player": "Kit", "lines": [broken, scene_line("Kept.")]}))
    assert [line.words for line in result.lines] == ["Kept."]


def test_missing_fields_have_plain_defaults():
    [line] = HandOver.parse(json.dumps({"lines": [{"day": 1, "hour": 2, "speaker": MIKAEL, "words": "Hm.", "unknown": True}]})).lines
    assert line.to is None and line.heard_by == () and line.scene == 0 and line.place == "" and line.player == "" and not line.player_there


def test_nobody_spoken_to():
    [line] = HandOver.parse(hand_over(scene_line("To nobody.", to=None))).lines
    assert line.to is None


def test_an_empty_heard_by_reaches_nobody():
    [line] = HandOver.parse(hand_over(scene_line("Nobody heard.", heard_by=[]))).lines
    assert line.heard_by == ()


def test_the_player_never_hears_and_nobody_is_listed_twice():
    [line] = HandOver.parse(hand_over(scene_line("Hi.", heard_by=[MIKAEL, PLAYER, SAADIA, MIKAEL]))).lines
    assert [p.name for p in line.heard_by] == ["Mikael", "Saadia"]


@pytest.mark.parametrize("form_id, ref_id", [
    (0x0001A671, "01A671"),     # Skyrim.esm: the load order index is dropped
    (0x2A01A671, "01A671"),     # Another plugin's index
    (0xFE012ABC, "000ABC"),     # A light plugin: only the last three digits
    (-0x01FED544, "000ABC"),    # 0xFE012ABC as a negative number, as Papyrus sends form ids above 0x7FFFFFFF
])
def test_form_ids_are_masked_like_load_character(form_id, ref_id):
    [line] = HandOver.parse(hand_over(scene_line("Hi.", speaker={"id": form_id, "name": "Someone"}))).lines
    assert line.speaker.ref_id == ref_id


def test_quotes_and_letters_of_other_languages_come_through():
    words = 'He said "Ysmir" – Dovahkiin, ça va? 你好'
    [line] = HandOver.parse(hand_over(scene_line(words))).lines
    assert line.words == words


def test_a_stored_line_reads_back_the_same():
    for item in (scene_line("Round trip.", to=None), menu_line("Any rumors?", "Not today.")):
        [line] = HandOver.parse(hand_over(item)).lines
        assert HeardLine.from_dict(json.loads(json.dumps(line.to_dict()))) == line
