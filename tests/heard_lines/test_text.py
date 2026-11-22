"""How heard lines read for the AI: third person with the NPCs' names, never "you\""""
import re

import pytest

from src.heard_lines.heard_line import PLAYER_REF_ID, GameTime, HandOver, HeardPerson
from src.heard_lines.heard_lines_text import PlainHeardLinesText
from tests.heard_lines.lines import CARLOTTA, HULDA, MIKAEL, REF, SAADIA, SCENE, at, hand_over, menu_line, scene_line

TEXT = PlainHeardLinesText()
LISTENER = {name: HeardPerson(REF[name], name) for name in REF}
NOON = GameTime(at(12)["day"], 12)

TURNED_DOWN = [scene_line("Your beauty outshines the sun.", time=1, hour=11.0),
               scene_line("Leave me be, Mikael.", speaker=CARLOTTA, to=MIKAEL, time=2, hour=11.01)]


def lines(*items):
    return list(HandOver.parse(hand_over(*items)).lines)


def entries(listener: str, *items, now: GameTime | None = NOON, hide_player: bool = False) -> list[str]:
    return [text for _ts, text in TEXT.memory_entries(lines(*items), LISTENER[listener], now, hide_player)]


def assert_no_you(text: str):
    assert not re.search(r"\byou\b", text, re.IGNORECASE), text


def test_the_speaker_and_the_person_spoken_to_get_the_same_text():
    expected = ['Earlier today in The Bannered Mare, Mikael said to Carlotta: "Your beauty outshines the sun." '
                'Carlotta answered: "Leave me be, Mikael." Kit was there.']
    assert entries("Mikael", *TURNED_DOWN) == expected
    assert entries("Carlotta", *TURNED_DOWN) == expected
    assert_no_you(expected[0])


def test_anyone_else_nearby_overheard_it():
    [text] = entries("Saadia", *TURNED_DOWN)
    assert text == ('Earlier today in The Bannered Mare, Saadia overheard Mikael say to Carlotta: "Your beauty outshines the sun." '
                    'Carlotta answered: "Leave me be, Mikael." Kit was there.')
    assert_no_you(text)


def test_the_same_speaker_going_on_and_someone_else_joining_in():
    [text] = entries("Saadia", scene_line("One.", time=1), scene_line("Two.", time=2),
                     scene_line("Enough!", speaker=SAADIA, to=MIKAEL, time=3), scene_line("To nobody.", speaker=CARLOTTA, to=None, time=4))
    assert 'Mikael went on: "Two."' in text
    assert 'Saadia said to Mikael: "Enough!"' in text
    assert 'Carlotta said: "To nobody."' in text


def test_a_line_said_to_nobody():
    assert entries("Saadia", scene_line("Lovely weather.", to=None)) == [
        'Earlier today in The Bannered Mare, Saadia overheard Mikael say: "Lovely weather." Kit was there.']


def test_without_the_player_nearby_kit_was_not_there():
    [text] = entries("Saadia", scene_line("Psst.", player_there=False))
    assert "Kit was there" not in text


def test_menu_talk_for_the_npc_talked_to_and_for_someone_overhearing():
    talk = menu_line("What news from the war?", "The Stormcloaks are losing.", heard_by=[HULDA, SAADIA])
    assert entries("Hulda", talk) == [
        'Earlier today in The Bannered Mare, Kit asked Hulda: "What news from the war?" Hulda answered: "The Stormcloaks are losing."']
    [overheard] = entries("Saadia", talk)
    assert overheard == ('Earlier today in The Bannered Mare, Saadia overheard Kit ask Hulda: "What news from the war?" '
                         'Hulda answered: "The Stormcloaks are losing."')
    assert_no_you(overheard)


def test_a_scene_line_without_a_scene_still_names_who_it_was_said_to():
    [text] = entries("Saadia", scene_line("Leave me be.", speaker=CARLOTTA, to=MIKAEL, scene=0))
    assert 'Saadia overheard Carlotta say to Mikael: "Leave me be."' in text and "said to Kit" not in text


def test_menu_talk_without_a_pick():
    assert entries("Hulda", menu_line("", "Need something?")) == [
        'Earlier today in The Bannered Mare, Hulda said to Kit: "Need something?"']


def test_several_menu_picks_with_one_npc_are_one_entry():
    [text] = entries("Hulda", menu_line("Rent a room.", "Ten gold.", time=1), menu_line("Any rumors?", "Not today.", time=2))
    assert text.count("Kit asked Hulda") == 2


@pytest.mark.parametrize("day, words", [(0, "Earlier today"), (-1, "Yesterday"), (-3, "3 days ago")])
def test_day_words(day, words):
    [text] = entries("Saadia", scene_line("Hi.", day=day))
    assert text.startswith(f"{words} in The Bannered Mare, ")


def test_day_words_count_midnights_not_hours():
    late = scene_line("Late.", hour=23.5, day=-1)
    early_now = GameTime(at(0.5)["day"], 0.5) # One game hour later, after midnight
    assert entries("Saadia", late, now=early_now)[0].startswith("Yesterday")
    assert entries("Saadia", scene_line("Early.", hour=0.1), now=GameTime(at(23.9)["day"], 23.9))[0].startswith("Earlier today")


def test_without_a_game_time_only_the_place_is_said():
    assert entries("Saadia", scene_line("Hi."), now=None)[0].startswith("In The Bannered Mare, Saadia overheard")
    assert entries("Saadia", scene_line("Hi.", place=""), now=None)[0].startswith("Saadia overheard")


def test_lines_are_grouped_by_scene_and_two_scenes_in_one_place_kept_apart():
    result = TEXT.memory_entries(lines(scene_line("A1.", time=1), scene_line("A2.", time=2),
                                       scene_line("B1.", time=3, scene=SCENE + 1), scene_line("A again.", time=4)),
                                 LISTENER["Saadia"], NOON, False)
    assert [ts for ts, _text in result] == [1, 3, 4] # Each entry is placed at its first line's real time
    assert ['"A1."' in t and '"A2."' in t for _ts, t in result] == [True, False, False]


def test_the_same_scene_on_another_day_is_a_new_entry():
    assert len(entries("Saadia", scene_line("Today.", time=2), scene_line("Yesterday.", day=-1, time=1))) == 2


def test_hide_player_leaves_out_menu_talk_and_that_the_player_was_there():
    result = entries("Saadia", *TURNED_DOWN, menu_line("Hi.", "Hello.", heard_by=[HULDA, SAADIA], time=5), hide_player=True)
    assert len(result) == 1 and "Kit" not in result[0]


def test_hide_player_leaves_out_who_a_line_was_said_to_when_it_was_the_player():
    [text] = entries("Saadia", scene_line("You there!", to={"id": 0x14, "name": "Kit"}), hide_player=True)
    assert 'Saadia overheard Mikael say: "You there!"' in text and "Kit" not in text


def test_in_a_conversation_each_line_is_one_narration_sentence():
    [scene, alone] = lines(scene_line("Your beauty outshines the sun."), scene_line("Hmph.", to=None))
    assert TEXT.nearby_now(scene, False) == 'Nearby, Mikael says to Carlotta: "Your beauty outshines the sun."'
    assert TEXT.nearby_now(alone, False) == 'Nearby, Mikael says: "Hmph."'
    [menu, no_pick] = lines(menu_line("Any rumors?", "Not today."), menu_line("", "Need something?"))
    assert TEXT.nearby_now(menu, False) == 'Nearby, Kit asks Hulda: "Any rumors?" Hulda answers: "Not today."'
    assert TEXT.nearby_now(no_pick, False) == 'Nearby, Hulda says to Kit: "Need something?"'
    assert TEXT.nearby_now(menu, True) == "" # Director Mode: menu talk with the player is left out
    assert TEXT.nearby_now(scene, True) == 'Nearby, Mikael says to Carlotta: "Your beauty outshines the sun."'


def test_a_line_someone_in_the_conversation_said_or_was_told_is_not_nearby():
    [scene, alone, to_player] = lines(scene_line("Your beauty outshines the sun."), scene_line("Hmph.", to=None),
                                      scene_line("You there!", to={"id": 0x14, "name": "Kit"}))
    assert TEXT.nearby_now(scene, False, {REF["Mikael"]}) == 'Mikael says to Carlotta: "Your beauty outshines the sun."' # The speaker
    assert TEXT.nearby_now(scene, False, {REF["Carlotta"]}) == 'Mikael says to Carlotta: "Your beauty outshines the sun."' # Spoken to
    assert TEXT.nearby_now(alone, False, {REF["Mikael"]}) == 'Mikael says: "Hmph."'
    assert TEXT.nearby_now(scene, False, {REF["Hulda"], PLAYER_REF_ID}) == 'Nearby, Mikael says to Carlotta: "Your beauty outshines the sun."'
    assert TEXT.nearby_now(to_player, False, {REF["Hulda"], PLAYER_REF_ID}) == 'Mikael says to Kit: "You there!"'
    assert TEXT.nearby_now(to_player, True, {REF["Hulda"]}) == 'Nearby, Mikael says: "You there!"' # Director Mode: not the player


def test_menu_talk_with_the_player_in_the_conversation_is_not_nearby():
    [menu, no_pick] = lines(menu_line("Any rumors?", "Not today."), menu_line("", "Need something?"))
    assert TEXT.nearby_now(menu, False, {REF["Saadia"], PLAYER_REF_ID}) == 'Kit asks Hulda: "Any rumors?" Hulda answers: "Not today."'
    assert TEXT.nearby_now(no_pick, False, {REF["Hulda"]}) == 'Hulda says to Kit: "Need something?"' # Hulda is in it
