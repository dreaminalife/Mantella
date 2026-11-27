"""Saving the one-line bio of an NPC who has none: the file is only ever added to, and each NPC goes in once"""
import json
import os

import pandas as pd
import pytest

from src.games import new_npc_bios


def _entry(name="Aveline", base_id="0A1B2C"):
    return {
        "name": name,
        "base_id": base_id,
        "race": "Breton",
        "bio": f"You are a female Breton {name}.",
        "gender": "Female",
        "ref_id": "0A1B2D",
        "skyrim_voice_folder": "FemaleEvenToned",
        "voice_accent": "en",
    }


def _read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


# --- Which file ---

def test_empty_path_is_the_personal_file(tmp_path):
    personal = str(tmp_path / "character_overrides")
    assert new_npc_bios.bio_editor_file("", personal) == os.path.join(personal, "character_overrides.csv")
    assert os.path.isdir(personal)


def test_folder_path_gets_the_default_file_name(tmp_path):
    folder = tmp_path / "mine"
    folder.mkdir()
    assert new_npc_bios.bio_editor_file(str(folder), str(tmp_path)) == os.path.join(str(folder), "character_overrides.csv")


def test_file_name_alone_goes_in_the_personal_folder_and_gets_csv(tmp_path):
    personal = str(tmp_path / "character_overrides")
    assert new_npc_bios.bio_editor_file("my_bios", personal) == os.path.join(personal, "my_bios.csv")


def test_json_path_is_kept(tmp_path):
    path = str(tmp_path / "bios.json")
    assert new_npc_bios.bio_editor_file(path, str(tmp_path / "x")) == path


# --- Already there ---

@pytest.mark.parametrize("row_id, wanted_id, expected", [
    ("0A1B2C", "0A1B2C", True),
    ("A1B2C", "0A1B2C", True),   # leading zeros don't count
    ("0a1b2c", "0A1B2C", True),  # nor case
    ("", "0A1B2C", True),        # a row with no ID covers everyone with the name
    ("0A1B2C", "", True),
    ("0F0F0F", "0A1B2C", False), # someone else with the same name
])
def test_has_entry(row_id, wanted_id, expected):
    assert new_npc_bios.has_entry([{"name": "aveline", "base_id": row_id}], "Aveline", wanted_id) is expected


def test_has_entry_other_name():
    assert not new_npc_bios.has_entry([{"name": "Lydia", "base_id": "0A1B2C"}], "Aveline", "0A1B2C")


# --- CSV ---

def test_new_csv_file(tmp_path):
    path = str(tmp_path / "sub" / "character_overrides.csv")
    assert new_npc_bios.add_entry(path, _entry())
    df = _read(path)
    assert df.to_dict("records") == [_entry()]


def test_appends_without_touching_existing_rows(tmp_path):
    path = str(tmp_path / "character_overrides.csv")
    columns = list(_entry().keys()) + ["notes"]
    original = ",".join(columns) + "\r\n" + 'Adara,013385,Redguard,"A bio, with a comma",Female,01F306,FemaleChild,en,NA\r\n'
    with open(path, "w", encoding="utf-8", newline="") as file:
        file.write(original)

    assert new_npc_bios.add_entry(path, _entry())

    with open(path, "r", encoding="utf-8", newline="") as file:
        text = file.read()
    assert text.startswith(original) # every byte of the old content is unchanged
    assert text.count("\r\n") == 3 and "\n" not in text.replace("\r\n", "") # the file's own line endings
    assert _read(path).iloc[1]["name"] == "Aveline"
    assert not os.path.exists(path + new_npc_bios.BACKUP_SUFFIX) # nothing rewritten, so no backup needed


def test_appends_after_a_last_line_without_newline(tmp_path):
    path = str(tmp_path / "character_overrides.csv")
    with open(path, "w", encoding="utf-8", newline="") as file:
        file.write(",".join(_entry().keys()) + "\r\n" + "Lydia,0A2C94,Nord,Housecarl,Female,0A2C8E,FemaleEvenToned,en")
    assert new_npc_bios.add_entry(path, _entry())
    assert list(_read(path)["name"]) == ["Lydia", "Aveline"]


def test_missing_columns_are_added_after_a_backup(tmp_path):
    path = str(tmp_path / "character_overrides.csv")
    original = "name,base_id,race,bio\r\nAdara,013385,Redguard,NA\r\n"
    with open(path, "w", encoding="utf-8", newline="") as file:
        file.write(original)

    assert new_npc_bios.add_entry(path, _entry())

    with open(path + new_npc_bios.BACKUP_SUFFIX, "r", encoding="utf-8", newline="") as file:
        assert file.read() == original
    df = _read(path)
    assert df.iloc[0]["base_id"] == "013385" and df.iloc[0]["bio"] == "NA" # read and written back as the exact text
    assert df.iloc[0]["voice_accent"] == "" # an empty cell adds nothing to Adara
    assert df.iloc[1].to_dict() == _entry()


def test_already_in_the_file_writes_nothing(tmp_path):
    path = str(tmp_path / "character_overrides.csv")
    new_npc_bios.add_entry(path, _entry())
    with open(path, "rb") as file:
        before = file.read()
    assert not new_npc_bios.add_entry(path, _entry(base_id="A1B2C"))
    with open(path, "rb") as file:
        assert file.read() == before


def test_bom_file_stays_readable(tmp_path):
    path = str(tmp_path / "character_overrides.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as file:
        file.write(",".join(_entry().keys()) + "\r\n")
    assert new_npc_bios.add_entry(path, _entry(name="Ærin"))
    with open(path, "rb") as file:
        data = file.read()
    assert data.count(b"\xef\xbb\xbf") == 1 # one BOM, at the start
    assert pd.read_csv(path, dtype=str, encoding="utf-8-sig").iloc[0]["name"] == "Ærin"


# --- JSON ---

def test_json_single_entry_becomes_a_list(tmp_path):
    path = str(tmp_path / "bios.json")
    lydia = {"name": "Lydia", "base_id": "0A2C94", "bio": "Housecarl"}
    with open(path, "w", encoding="utf-8") as file:
        json.dump(lydia, file)
    assert new_npc_bios.add_entry(path, _entry())
    with open(path, encoding="utf-8") as file:
        assert json.load(file) == [lydia, _entry()]
    assert os.path.exists(path + new_npc_bios.BACKUP_SUFFIX)


def test_unreadable_json_is_left_alone(tmp_path):
    path = str(tmp_path / "bios.json")
    with open(path, "w", encoding="utf-8") as file:
        file.write("{not json")
    with pytest.raises(Exception):
        new_npc_bios.add_entry(path, _entry())
    with open(path, encoding="utf-8") as file:
        assert file.read() == "{not json"


def test_json_already_there(tmp_path):
    path = str(tmp_path / "bios.json")
    new_npc_bios.add_entry(path, _entry())
    assert not new_npc_bios.add_entry(path, _entry())


# --- The NPC in the game ---

RACE = '[Race <BretonRace (00013741)>]'
VOICE = '[VoiceType <FemaleEvenToned (00013ADD)>]'


def _load(game, base_id="0A1B2C"):
    return game.load_external_character_info(base_id=base_id, name="Aveline", race=RACE, gender=1, ingame_voice_model=VOICE)


def test_saved_npc_loads_as_before_with_their_bio_kept(tmp_path, default_config):
    from src.games.skyrim import Skyrim
    default_config.mod_path_base = str(tmp_path)
    default_config.save_folder = str(tmp_path)
    game = Skyrim(default_config)
    before = _load(game)
    assert before.is_generic_npc
    path = new_npc_bios.bio_editor_file("", os.path.join(str(tmp_path), "data", "Skyrim", "character_overrides"))

    assert game.save_bio_for_new_npc("Aveline", "0A1B2C", "0A1B2D", RACE, 1, VOICE, path)

    # In this session and the next, they load from the file, with what they had before
    for loaded in (_load(game), _load(Skyrim(default_config))):
        assert not loaded.is_generic_npc
        assert loaded.bio == before.bio == "You are a female Breton Aveline."
        assert loaded.tts_voice_model == before.tts_voice_model
        assert loaded.csv_in_game_voice_model == before.csv_in_game_voice_model
        assert loaded.voice_accent == before.voice_accent
    # Once only
    assert not game.save_bio_for_new_npc("Aveline", "0A1B2C", "0A1B2D", RACE, 1, VOICE, path)
    assert len(_read(path)) == 1


def test_npc_with_a_bio_is_not_saved(tmp_path, default_config):
    from src.games.skyrim import Skyrim
    default_config.mod_path_base = str(tmp_path)
    default_config.save_folder = str(tmp_path)
    path = str(tmp_path / "character_overrides.csv")
    assert not Skyrim(default_config).save_bio_for_new_npc("Lydia", "0A2C8E", "0A2C94", '[Race <NordRace (00013746)>]', 1, VOICE, path)
    assert not os.path.exists(path)

# --- One person a mod or quest named, whose base every Whiterun guard shares ---

NORD = '[Race <NordRace (00013746)>]'
GUARD_VOICE = '[VoiceType <MaleNord (00013AD3)>]'
GUARD_BASE = "0B0C0D"


def _guard(game, name):
    return game.load_external_character_info(base_id=GUARD_BASE, name=name, race=NORD, gender=0, ingame_voice_model=GUARD_VOICE)


def test_named_guard_is_saved_without_the_shared_base_id(tmp_path, default_config):
    from src.games.skyrim import Skyrim
    default_config.mod_path_base = str(tmp_path)
    default_config.save_folder = str(tmp_path)
    game = Skyrim(default_config)
    # The folder Mantella loads override files from, so the next session finds him
    path = new_npc_bios.bio_editor_file("", os.path.join(str(tmp_path), "data", "Skyrim", "character_overrides"))

    assert game.save_bio_for_new_npc("Njordunn the Fox, Whiterun Guard", GUARD_BASE, "037081", NORD, 0, GUARD_VOICE, path, own_name=True)

    rows = _read(path)
    assert rows.loc[0, "name"] == "Njordunn the Fox, Whiterun Guard"
    assert rows.loc[0, "base_id"] == ""
    assert rows.loc[0, "ref_id"] == "037081"
    # Found again by his name, in this session and the next
    for loaded in (_guard(game, "Njordunn the Fox, Whiterun Guard"), _guard(Skyrim(default_config), "Njordunn the Fox, Whiterun Guard")):
        assert not loaded.is_generic_npc
        assert loaded.bio == "You are a male Nord Njordunn the Fox, Whiterun Guard."
    # Another guard with the same base doesn't get his bio
    other = _guard(Skyrim(default_config), "Hrolf the Bold, Whiterun Guard")
    assert other.is_generic_npc
    assert "Njordunn" not in other.bio
    # Once only
    assert not game.save_bio_for_new_npc("Njordunn the Fox, Whiterun Guard", GUARD_BASE, "037081", NORD, 0, GUARD_VOICE, path, own_name=True)
    assert len(_read(path)) == 1


def test_named_npc_with_a_crowd_name_is_not_saved(tmp_path, default_config):
    """Afflicted is on the character list several times (none of them a Nord), so it can't be one person's name"""
    from src.games.skyrim import Skyrim
    default_config.mod_path_base = str(tmp_path)
    default_config.save_folder = str(tmp_path)
    game = Skyrim(default_config)
    path = str(tmp_path / "character_overrides.csv")
    assert _guard(game, "Afflicted").is_generic_npc

    assert not game.save_bio_for_new_npc("Afflicted", GUARD_BASE, "037081", NORD, 0, GUARD_VOICE, path, own_name=True)
    assert not os.path.exists(path)


# --- A renamed person gets the bio of the people built like them ---

NJORDUNN = "Njordunn the Fox, Whiterun Guard"
ONE_LINE = "You are a male Nord Njordunn the Fox, Whiterun Guard."
GUARD_BIO = "These guards act as the military and police force of Whiterun"


def _renamed(game, crowd_name="Whiterun Guard"):
    return game.load_external_character_info(base_id=GUARD_BASE, name=NJORDUNN, race=NORD, gender=0, ingame_voice_model=GUARD_VOICE, crowd_name=crowd_name)


def test_renamed_guard_gets_the_guard_bio_from_the_first_line(tmp_path, default_config):
    from src.games.skyrim import Skyrim
    default_config.mod_path_base = str(tmp_path)
    default_config.save_folder = str(tmp_path)
    loaded = _renamed(Skyrim(default_config))
    assert loaded.is_generic_npc # their voice is picked as for anyone with no bio
    assert loaded.bio.startswith(ONE_LINE + "\nWhiterun Guard: " + GUARD_BIO)


def test_renamed_npc_whose_crowd_is_not_listed_keeps_the_one_line(tmp_path, default_config):
    from src.games.skyrim import Skyrim
    default_config.mod_path_base = str(tmp_path)
    default_config.save_folder = str(tmp_path)
    assert _renamed(Skyrim(default_config), crowd_name="Thalmor Soldier").bio == ONE_LINE


def test_saved_renamed_guard_keeps_the_guard_bio_and_tags(tmp_path, default_config):
    from src.games.skyrim import Skyrim
    default_config.mod_path_base = str(tmp_path)
    default_config.save_folder = str(tmp_path)
    game = Skyrim(default_config)
    guard_tags = _filled_cell(game, "Whiterun Guard", "tags")
    assert guard_tags # the shipped row has some, so this checks they're carried over
    path = new_npc_bios.bio_editor_file("", os.path.join(str(tmp_path), "data", "Skyrim", "character_overrides"))

    assert game.save_bio_for_new_npc(NJORDUNN, GUARD_BASE, "037081", NORD, 0, GUARD_VOICE, path, own_name=True, crowd_name="Whiterun Guard")

    row = _read(path).iloc[0]
    assert row["base_id"] == ""
    assert row["bio"].startswith(ONE_LINE + "\nWhiterun Guard: " + GUARD_BIO)
    assert row["tags"] == guard_tags
    # From then on he's found by his own row, with the same bio, in this session and the next
    for loaded in (_guard(game, NJORDUNN), _guard(Skyrim(default_config), NJORDUNN)):
        assert not loaded.is_generic_npc
        assert loaded.bio.startswith(ONE_LINE + "\nWhiterun Guard: " + GUARD_BIO)


def _filled_cell(game, name, column):
    rows = game.character_df.loc[game.character_df["name"].astype(str).str.lower() == name.lower(), column]
    return str(rows.iloc[0]).strip()
