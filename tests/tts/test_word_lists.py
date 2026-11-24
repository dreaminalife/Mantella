import logging
import os
import time

import pytest

from src.tts.registry import VoiceServiceInfo
from src.tts.word_lists import IPA, MANTELLA_LIST, MY_LIST, SPELLING, WordLists, replace_words

HEADER = "word,say_as,kind,service,language\n"


def write(folder, name: str, rows: str):
    with open(os.path.join(folder, name), "w", encoding="utf-8", newline="") as f:
        f.write(HEADER + rows)


@pytest.fixture
def shipped(tmp_path):
    path = tmp_path / "shipped.csv"
    path.write_text(HEADER + "Dovahkiin,Doh-vah-keen,,,\nThu'um,Thoo-oom,,,\n", encoding="utf-8")
    return str(path)


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "pronunciation"
    return str(path)


def pocket(name="Pocket TTS"):
    return VoiceServiceInfo(name, factory=None, aliases=["pocket"]).matches


def test_mantellas_list_is_copied_in_and_the_players_list_made_once(folder, shipped):
    WordLists(folder, shipped)
    assert os.path.exists(os.path.join(folder, MANTELLA_LIST))
    with open(os.path.join(folder, MY_LIST), encoding="utf-8") as f:
        assert f.read() == HEADER
    write(folder, MY_LIST, "Auri,Or-ee,,,\n")
    WordLists(folder, shipped) # a new start: the player's list stays
    with open(os.path.join(folder, MY_LIST), encoding="utf-8") as f:
        assert "Auri" in f.read()


def test_words_are_changed_only_where_they_apply(folder, shipped):
    words = WordLists(folder, shipped)
    write(folder, "auri_mod.csv", "Auri,Ow-ree,,pocket,\nJarl Balgruuf,Yarl Bal-groof,,,\nBalgruuf,Bal-groof,,,de\n")
    text = "Dovahkiin, have you seen Auri? Jarl Balgruuf's men told me about the Thu'um."
    assert words.apply(text, pocket(), "en") == "Doh-vah-keen, have you seen Ow-ree? Yarl Bal-groof's men told me about the Thoo-oom."
    assert "Auri" in words.apply(text, VoiceServiceInfo("Other", factory=None).matches, "en") # Auri's entry is for Pocket TTS only


def test_the_players_list_wins_and_the_more_exact_entry_wins(folder, shipped):
    words = WordLists(folder, shipped)
    write(folder, "a_mod.csv", "Dovahkiin,Dova-kin,,,\n")
    write(folder, MY_LIST, "Dovahkiin,Doe-vah-keen,,,\n")
    assert words.apply("Dovahkiin", pocket(), "en") == "Doe-vah-keen"
    write(folder, "a_mod.csv", "Dovahkiin,Dova-kin,,Pocket TTS,en\n")
    time.sleep(0.01)
    os.utime(os.path.join(folder, "a_mod.csv"))
    assert words.apply("Dovahkiin", pocket(), "en") == "Dova-kin"


def test_sound_symbols_only_for_a_service_that_takes_them(folder, shipped):
    words = WordLists(folder, shipped)
    write(folder, MY_LIST, "Dovahkiin,/ˈdoʊvɑːkiːn/,ipa,,\n")
    assert words.apply("Dovahkiin!", pocket(), "en", accepts_ipa=False) == "Doh-vah-keen!"
    assert words.apply("Dovahkiin!", pocket(), "en", accepts_ipa=True, format_ipa=lambda w, ipa: f"<{ipa}>") == "</ˈdoʊvɑːkiːn/>!"


def test_a_bad_row_is_skipped_with_one_warning(folder, shipped, caplog):
    words = WordLists(folder, shipped)
    write(folder, MY_LIST, "Auri,,,,\nAuri,Or-ee,loud,,\nAuri,Or-ee,,,\n")
    with caplog.at_level(logging.DEBUG):
        assert words.apply("Auri", pocket(), "en") == "Or-ee"
        words.entries()
    assert sum("skipped" in r.message for r in caplog.records) == 2


def test_changes_are_used_from_the_next_line(folder, shipped):
    words = WordLists(folder, shipped)
    assert words.apply("Auri", pocket(), "en") == "Auri"
    write(folder, "late.csv", "Auri,Or-ee,,,\n")
    assert words.apply("Auri", pocket(), "en") == "Or-ee"


def test_add_to_my_list_adds_then_changes_the_row(folder, shipped):
    words = WordLists(folder, shipped)
    with open(os.path.join(folder, MY_LIST), "w", encoding="utf-8", newline="") as f:
        f.write(HEADER.replace("\n", "\r\n") + "Ysgramor,Is-gra-mor,,,\r\n")
    words.add_to_my_list("Auri", "Or-ee")
    words.add_to_my_list("auri", "Aw-ree")
    with open(os.path.join(folder, MY_LIST), "rb") as f:
        content = f.read()
    assert content.count(b"\r\n") == 3 and content.count(b"\n") == 3 # the file's line endings are kept
    assert b"Ysgramor" in content and b"Aw-ree" in content and b"Or-ee" not in content


def test_a_list_not_saved_as_utf8_is_still_read_with_one_warning(folder, shipped, caplog):
    words = WordLists(folder, shipped)
    with open(os.path.join(folder, "excel.csv"), "wb") as f:
        f.write((HEADER + "Ysgramor,Is-gra-mor,,,\nBjørn,Byorn,,,\n").encode("cp1252")) # how Excel saves it on a Western Windows
    with caplog.at_level(logging.DEBUG):
        assert words.apply("Ysgramor", pocket(), "en") == "Is-gra-mor"
        assert words.apply("Bjørn", pocket(), "en") == "Byorn"
    assert sum("not saved as UTF-8" in r.message for r in caplog.records) == 1
    with open(os.path.join(folder, MY_LIST), "wb") as f:
        f.write((HEADER + "Bjørn,Byorn,,,\n").encode("cp1252"))
    words.add_to_my_list("Auri", "Or-ee")
    with open(os.path.join(folder, MY_LIST), encoding="utf-8") as f:
        content = f.read()
    assert "Bjørn" in content and "Or-ee" in content # saved back as UTF-8


def test_whole_words_only_and_longest_first():
    replacements = {"balgruuf": "Bal-groof", "jarl balgruuf": "Yarl Bal-groof", "dova": "X"}
    assert replace_words("Jarl Balgruuf and BALGRUUF met Dovahkiin.", replacements) == "Yarl Bal-groof and Bal-groof met Dovahkiin."
