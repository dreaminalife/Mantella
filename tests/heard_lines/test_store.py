"""Keeping heard lines in each NPC's folder"""
import json
import os
import threading
from types import SimpleNamespace

import pytest

from src.heard_lines.heard_line import GameTime, HandOver, HeardPerson
from src.heard_lines.heard_lines_store import JsonHeardLinesStore
from tests.heard_lines.lines import REF, WORLD, START_DAY, at, hand_over, menu_line, scene_line, settings

SAADIA = HeardPerson(REF["Saadia"], "Saadia")
MIKAEL = HeardPerson(REF["Mikael"], "Mikael")


def lines(*items):
    return list(HandOver.parse(hand_over(*items)).lines)


def now(hour: float, day: int = 0) -> GameTime:
    time = at(hour, day)
    return GameTime(time["day"], time["hour"])


@pytest.fixture
def config():
    return settings()


@pytest.fixture
def store(tmp_path, config) -> JsonHeardLinesStore:
    return JsonHeardLinesStore(SimpleNamespace(conversation_folder_path=str(tmp_path)), config)


def words(store: JsonHeardLinesStore, person: HeardPerson = SAADIA, world: str = WORLD) -> list[str]:
    return [line.words for line in store.lines_for(world, person.name, person.ref_id)]


def test_lines_are_kept_in_the_npcs_folder_oldest_first(store, tmp_path):
    store.add(WORLD, SAADIA, lines(scene_line("Second.", time=20), scene_line("First.", time=10)))
    assert words(store) == ["First.", "Second."]
    assert os.path.exists(tmp_path / WORLD / f"Saadia - {REF['Saadia']}" / "heard_lines.json")
    assert words(store, MIKAEL) == []


def test_lines_older_than_the_days_kept_are_forgotten(store, config):
    store.add(WORLD, SAADIA, lines(scene_line("Day one.", day=0, time=1), scene_line("Day three.", day=2, time=2)))
    store.note_now(WORLD, now(12, day=3)) # 3 days after the first line
    assert words(store) == ["Day one.", "Day three."]
    store.note_now(WORLD, now(13, day=3)) # Just over 3 days
    assert words(store) == ["Day three."]
    config.heard_lines_days_kept = 5 # A new setting applies at once, also to lines not yet dropped from the file
    assert words(store) == ["Day one.", "Day three."]


def test_the_most_lines_drops_the_oldest_first(store, config):
    config.heard_lines_most_per_npc = 3
    store.add(WORLD, SAADIA, lines(*(scene_line(f"Line {i}.", time=i) for i in range(5))))
    assert words(store) == ["Line 2.", "Line 3.", "Line 4."]
    store.add(WORLD, SAADIA, lines(scene_line("Line 5.", time=5)))
    assert words(store) == ["Line 3.", "Line 4.", "Line 5."]


def test_a_repeat_is_kept_once_while_the_first_copy_is_kept(store):
    store.add(WORLD, SAADIA, lines(scene_line("Praise Talos!", time=1, day=0)))
    store.note_now(WORLD, now(12, day=1))
    store.add(WORLD, SAADIA, lines(scene_line("Praise Talos!", time=2, day=1), scene_line("Praise Talos!", speaker={"id": 5, "name": "Heimskr"}, time=3, day=1)))
    kept = store.lines_for(WORLD, "Saadia", SAADIA.ref_id)
    assert [(line.speaker.name, line.time) for line in kept] == [("Mikael", 1), ("Heimskr", 3)] # Another speaker saying it is not a repeat



def test_the_same_answer_to_another_question_is_not_a_repeat(store):
    hulda = HeardPerson(REF["Hulda"], "Hulda")
    store.add(WORLD, hulda, lines(menu_line("Any rumors?", "Nothing new.", time=1)))
    store.add(WORLD, hulda, lines(menu_line("What's the news?", "Nothing new.", time=2), menu_line("Any rumors?", "Nothing new.", time=3)))
    assert [(line.asked, line.time) for line in store.lines_for(WORLD, "Hulda", hulda.ref_id)] == [("Any rumors?", 1), ("What's the news?", 2)]

def test_a_repeat_after_the_first_copy_expired_is_kept_again(store):
    store.add(WORLD, SAADIA, lines(scene_line("Praise Talos!", time=1, day=0)))
    store.note_now(WORLD, now(12, day=4))
    store.add(WORLD, SAADIA, lines(scene_line("Praise Talos!", time=2, day=4)))
    assert [line.time for line in store.lines_for(WORLD, "Saadia", SAADIA.ref_id)] == [2]


def test_an_older_save_forgets_every_npcs_lines_after_it_in_that_world_only(store):
    for world in (WORLD, "OtherWorld"):
        store.note_now(world, now(10))
        store.add(world, SAADIA, lines(scene_line("Before.", hour=9, time=1), scene_line("After.", hour=11, time=2)))
        store.add(world, MIKAEL, lines(scene_line("Mikael after.", hour=11.5, time=3)))
        store.note_now(world, now(12))
    store.note_now(WORLD, now(10)) # The save from 10:00 is loaded
    assert words(store) == ["Before."]
    assert words(store, MIKAEL) == []
    assert words(store, world="OtherWorld") == ["Before.", "After."]
    assert store.latest_now(WORLD) == now(10)


def test_the_same_time_twice_is_not_an_older_save(store):
    store.note_now(WORLD, now(12))
    store.add(WORLD, SAADIA, lines(scene_line("At noon.", hour=12, time=1)))
    store.note_now(WORLD, now(12))
    assert words(store) == ["At noon."]


def test_a_save_one_game_minute_earlier_forgets_only_that_minute(store):
    store.add(WORLD, SAADIA, lines(scene_line("Earlier.", hour=11.9, time=1), scene_line("Last minute.", hour=12, time=2)))
    store.note_now(WORLD, now(12))
    store.note_now(WORLD, now(12 - 1 / 60))
    assert words(store) == ["Earlier."]


def test_without_a_game_time_nothing_is_forgotten(store):
    store.add(WORLD, SAADIA, lines(scene_line("Long ago.", day=-10, time=1)))
    assert store.latest_now(WORLD) is None
    assert words(store) == ["Long ago."]


def test_an_npc_with_only_the_older_name_folder_keeps_lines_there(store, tmp_path):
    legacy = tmp_path / WORLD / "Saadia"
    legacy.mkdir(parents=True)
    store.add(WORLD, SAADIA, lines(scene_line("Into the old folder.")))
    assert (legacy / "heard_lines.json").exists()
    assert not (tmp_path / WORLD / f"Saadia - {REF['Saadia']}").exists() # Would make the summaries switch to an empty folder
    assert words(store) == ["Into the old folder."]


def test_the_number_added_to_repeated_names_is_left_out_of_the_folder(store, tmp_path):
    store.add(WORLD, HeardPerson("01A6AA", "Whiterun Guard 2"), lines(scene_line("Halt.")))
    assert (tmp_path / WORLD / "Whiterun Guard - 01A6AA" / "heard_lines.json").exists()
    assert [l.words for l in store.lines_for(WORLD, "Whiterun Guard", "01A6AA")] == ["Halt."]


@pytest.mark.parametrize("contents", ["{broken", '{"lines": "not a list"}', '{"lines": [{"words": "no speaker"}]}', "[]"])
def test_a_broken_file_reads_as_empty_and_is_replaced(store, tmp_path, contents):
    folder = tmp_path / WORLD / f"Saadia - {REF['Saadia']}"
    folder.mkdir(parents=True)
    (folder / "heard_lines.json").write_text(contents, encoding="utf-8")
    assert words(store) == []
    store.add(WORLD, SAADIA, lines(scene_line("New.")))
    assert words(store) == ["New."]


def test_a_broken_time_file_counts_as_no_time(store, tmp_path):
    (tmp_path / WORLD).mkdir()
    (tmp_path / WORLD / "heard_lines_now.json").write_text("{broken", encoding="utf-8")
    assert store.latest_now(WORLD) is None


def test_two_threads_writing_at_once_lose_nothing(store, config):
    config.heard_lines_most_per_npc = 200

    def write(start: int):
        for i in range(start, start + 20):
            store.add(WORLD, SAADIA, lines(scene_line(f"Line {i}.", time=i)))

    threads = [threading.Thread(target=write, args=(start,)) for start in (0, 100)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(words(store)) == 40


def test_two_stores_after_a_settings_reload_share_the_lock(tmp_path, config):
    game = SimpleNamespace(conversation_folder_path=str(tmp_path))
    old, new = JsonHeardLinesStore(game, config), JsonHeardLinesStore(game, settings(most=1))
    old.add(WORLD, SAADIA, lines(scene_line("Old.", time=1)))
    new.add(WORLD, SAADIA, lines(scene_line("New.", time=2)))
    assert JsonHeardLinesStore._lock is old._lock is new._lock
    assert words(old) == ["New."] # The new store saved with its own limit (1), so the old store reads that too


def test_the_file_is_plain_json(store, tmp_path):
    store.add(WORLD, SAADIA, lines(scene_line("Readable.")))
    data = json.loads((tmp_path / WORLD / f"Saadia - {REF['Saadia']}" / "heard_lines.json").read_text(encoding="utf-8"))
    assert data["lines"][0]["words"] == "Readable."
    assert START_DAY <= data["lines"][0]["day"] < START_DAY + 1
