"""Adding a new NPC in the Bio Editor: never overwrites or loses anything already there"""
import json

import pandas as pd
import pytest

from src.config.config_loader import ConfigLoader
from src.config.definitions.game_definitions import GameEnum
from src.ui import bio_new_npc
from src.ui.start_ui import StartUI

LYDIA = {"name": "Lydia", "base_id": "0A2C94", "race": "Nord", "bio": "Housecarl."}


def _table(*rows):
    return pd.DataFrame(list(rows))


# --- Which new NPCs would overwrite an NPC who has a bio ---

def test_the_same_name_without_a_base_id_would_overwrite_them():
    assert bio_new_npc.clashing_npc(_table(LYDIA), "lydia", "", "") == "Lydia"


def test_a_matching_base_id_would_overwrite_them_even_written_differently():
    table = _table(LYDIA)
    assert bio_new_npc.clashing_npc(table, "Lydia", "000A2C94", "") == "Lydia"
    assert bio_new_npc.clashing_npc(table, "Lydia", "FF0A2C94", "Nord") == "Lydia"
    assert bio_new_npc.clashing_npc(table, "Lydia", "02C94", "") == "Lydia" # the game also matches on the last 5, 4 or 3


def test_another_base_id_or_another_race_is_a_different_npc():
    table = _table(LYDIA)
    assert bio_new_npc.clashing_npc(table, "Lydia", "0B1111", "") is None
    assert bio_new_npc.clashing_npc(table, "Lydia", "", "Breton") is None
    assert bio_new_npc.clashing_npc(table, "Whiterun Guard", "", "") is None


def test_a_bio_shared_by_everyone_with_a_name_never_gets_a_second_row():
    # The game finds every Bandit by name alone; with two Bandit rows, all of them would lose their bio
    bandit = {"name": "Bandit", "base_id": float("nan"), "race": float("nan"), "bio": "Robs people."}
    table = _table(LYDIA, bandit)
    for base_id, race in [("", ""), ("0003DF08", ""), ("", "Nord"), ("0003DF08", "Nord")]:
        assert "one bio for everyone" in bio_new_npc.clash_problem(table, "bandit", base_id, race)
    assert bio_new_npc.clash_problem(table, "Lydia", "0B1111", "") is None
    assert "already has a bio" in bio_new_npc.clash_problem(table, "Lydia", "", "")


def test_base_ids_are_cleaned_and_checked():
    assert bio_new_npc.clean_base_id(" 0x1a694 ") == "1A694"
    assert bio_new_npc.clean_base_id("") == ""
    with pytest.raises(ValueError):
        bio_new_npc.clean_base_id("12G4")


def test_race_choices_are_the_races_in_the_list_once_each():
    assert bio_new_npc.race_choices(_table(LYDIA, {**LYDIA, "name": "Ria", "race": "Imperial"}, {**LYDIA, "race": ""})) == ["Imperial", "Nord"]


# --- Writing the file ---

def _guard(**cells):
    return bio_new_npc.new_row("Whiterun Guard", cells.get("base_id", ""), "Nord", cells.get("bio", "Keeps the peace."),
                               cells.get("tags", ""), cells.get("voice_effect", ""))


def test_a_new_file_gets_just_the_new_npc(tmp_path):
    path = tmp_path / "character_overrides.csv"
    assert bio_new_npc.add_row_to_file(str(path), _guard()) is None
    saved = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert saved.to_dict("records") == [{"name": "Whiterun Guard", "base_id": "", "race": "Nord", "bio": "Keeps the peace."}]


def test_every_cell_already_in_the_file_stays_exactly_as_it_was(tmp_path):
    path = tmp_path / "character_overrides.csv"
    original = 'name,base_id,race,bio,ref_id,notes\r\nAdara,013385,Redguard,"Line one\r\nline two",NA,\r\n'
    path.write_bytes(original.encode("utf-8"))
    before = pd.read_csv(path, dtype=str, keep_default_na=False).iloc[0].to_dict()
    backup = bio_new_npc.add_row_to_file(str(path), _guard())
    assert open(backup, "rb").read() == original.encode("utf-8") # the file as it was
    saved = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert len(saved) == 2 and saved.iloc[0].to_dict() == before # "013385", "NA" and the empty cell as they were
    assert saved.iloc[1]["name"] == "Whiterun Guard" and path.read_bytes().count(b"\r\n") >= 3 # keeps its line endings


def test_a_column_the_file_lacks_is_added_after_a_backup(tmp_path):
    path = tmp_path / "character_overrides.csv"
    path.write_text("name,base_id,race,bio\nAdara,013385,Redguard,Old.\n", encoding="utf-8")
    backup = bio_new_npc.add_row_to_file(str(path), _guard(tags="whiterun_hold"))
    assert backup == str(path) + ".bak"
    assert open(backup, encoding="utf-8").read() == "name,base_id,race,bio\nAdara,013385,Redguard,Old.\n"
    saved = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert saved.iloc[0].to_dict() == {"name": "Adara", "base_id": "013385", "race": "Redguard", "bio": "Old.", "tags": ""}
    assert saved.iloc[1]["tags"] == "whiterun_hold"


def test_an_npc_already_in_the_file_is_refused_and_nothing_is_written(tmp_path):
    path = tmp_path / "character_overrides.csv"
    path.write_text("name,base_id,race,bio\nWhiterun Guard,,Nord,Mine.\n", encoding="utf-8")
    with pytest.raises(bio_new_npc.NotSaved):
        bio_new_npc.add_row_to_file(str(path), _guard(bio="Other."))
    assert path.read_text(encoding="utf-8") == "name,base_id,race,bio\nWhiterun Guard,,Nord,Mine.\n"


def test_a_row_with_just_a_name_doesnt_stop_a_different_npc_with_a_base_id(tmp_path):
    # An override for Lydia written with only her name adds to her row; another Lydia with her own base ID is safe
    path = tmp_path / "character_overrides.csv"
    path.write_text("name,base_id,race,bio\nLydia,,,Housecarl.\n", encoding="utf-8")
    bio_new_npc.add_row_to_file(str(path), bio_new_npc.new_row("Lydia", "0B1111", "Nord", "Another Lydia.", "", ""))
    saved = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert saved["bio"].tolist() == ["Housecarl.", "Another Lydia."]
    with pytest.raises(bio_new_npc.NotSaved): # with no base ID, she would overwrite Lydia's bio
        bio_new_npc.add_row_to_file(str(path), bio_new_npc.new_row("Lydia", "", "", "Third.", "", ""))


def test_a_json_file_that_cant_be_read_is_left_as_it_is(tmp_path):
    path = tmp_path / "mine.json"
    path.write_text('[{"name": "Adara", "bio": "Old."}', encoding="utf-8")
    with pytest.raises(bio_new_npc.NotSaved):
        bio_new_npc.add_row_to_file(str(path), _guard())
    assert path.read_text(encoding="utf-8") == '[{"name": "Adara", "bio": "Old."}'


def test_a_json_file_keeps_its_npc_and_gets_the_new_one(tmp_path):
    path = tmp_path / "mine.json"
    path.write_text(json.dumps({"name": "Adara", "base_id": "013385", "bio": "Old.", "author": "me"}), encoding="utf-8")
    bio_new_npc.add_row_to_file(str(path), _guard())
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved[0] == {"name": "Adara", "base_id": "013385", "bio": "Old.", "author": "me"}
    assert saved[1]["name"] == "Whiterun Guard" and len(saved) == 2


# --- The Create button ---

def _handlers(tmp_path):
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    handlers = {}
    for f in blocks.fns:
        name = getattr(f.fn, "__name__", "")
        if name in ("on_create", "on_select", "on_save") and "generate_bio_editor_page" in getattr(f.fn, "__qualname__", ""):
            handlers.setdefault(name, f.fn)
    return handlers


def _on_create(tmp_path):
    return _handlers(tmp_path)["on_create"]


def _messages(result) -> str:
    return " ".join(value for value in result.values() if isinstance(value, str))


def test_create_copies_the_open_npc_and_leaves_them_unchanged(tmp_path):
    on_create = _on_create(tmp_path)
    label, key = "Lydia (0A2C94) — Nord", "Lydia§0A2C94§Nord"
    override = tmp_path / "my_overrides.csv"
    override.write_text("name,base_id,race,bio\nLydia,0A2C94,Nord,Housecarl.\n", encoding="utf-8")
    opened = {"label": label, "bio": "Housecarl.", "tags": "", "voice_effect": ""}
    result = on_create("Lydia's Sister", "", "Nord", label, "Housecarl, edited.", "", "", opened, _table(LYDIA), {label: key}, str(override))
    assert "Added `Lydia's Sister`" in _messages(result) and "`Lydia` wasn't changed" in _messages(result)
    saved = pd.read_csv(override, dtype=str, keep_default_na=False)
    assert saved.iloc[0].to_dict() == {"name": "Lydia", "base_id": "0A2C94", "race": "Nord", "bio": "Housecarl."}
    assert saved.iloc[1]["bio"] == "Housecarl, edited."


def test_create_refuses_a_name_that_already_has_a_bio(tmp_path):
    on_create = _on_create(tmp_path)
    override = tmp_path / "my_overrides.csv"
    result = on_create("Lydia", "", "", None, "", "", "", {}, _table(LYDIA), {}, str(override))
    assert "already has a bio" in _messages(result) and not override.exists()


def test_an_npc_with_no_base_id_opens_with_their_bio_and_saves_it_as_an_empty_base_id(tmp_path):
    # The list shows Bandit's empty base ID and race as nan
    handlers = _handlers(tmp_path)
    label, key = "Bandit (nan) — nan", "Bandit§nan§nan"
    df = _table({"name": "Bandit", "base_id": float("nan"), "race": float("nan"), "bio": "Robs people."})
    assert handlers["on_select"](label, df, {label: key}, "", "")[0] == "Robs people."
    override = tmp_path / "my_overrides.csv"
    info = handlers["on_save"](label, "Robs travellers.", "", {label: key}, {key: label}, str(override))[0]
    assert info.startswith("Saved to")
    saved = pd.read_csv(override, dtype=str, keep_default_na=False)
    assert saved[["name", "base_id", "race", "bio"]].to_dict("records") == [{"name": "Bandit", "base_id": "", "race": "", "bio": "Robs travellers."}]


def test_a_failed_save_says_why(tmp_path):
    handlers = _handlers(tmp_path)
    override = tmp_path / "mine.json"
    override.write_text("[{", encoding="utf-8")
    label, key = "Lydia (0A2C94) — Nord", "Lydia§0A2C94§Nord"
    info = handlers["on_save"](label, "New.", "", {label: key}, {key: label}, str(override))[0]
    assert "Couldn't read `mine.json`" in info and override.read_text(encoding="utf-8") == "[{"


def test_the_tags_box_finds_the_row_save_writes_so_saving_keeps_the_tags(tmp_path):
    # Every base ID in this file is digits only, so a plain read made 013385 the number 13385 and the box came up empty
    handlers = _handlers(tmp_path)
    label, key = "Adara (013385) — Redguard", "Adara§013385§Redguard"
    override = tmp_path / "my_overrides.csv"
    override.write_text("name,base_id,race,bio,tags\nAdara,013385,Redguard,Old.,whiterun_hold\n", encoding="utf-8")
    df = _table({"name": "Adara", "base_id": "013385", "race": "Redguard", "bio": "Old."})
    assert handlers["on_select"](label, df, {label: key}, "", str(override))[5] == "whiterun_hold"


def test_saving_keeps_npcs_added_since_the_page_opened_in_the_list(tmp_path):
    handlers = _handlers(tmp_path)
    label, key = "Whiterun Guard () — Nord", "Whiterun Guard§§Nord"
    df = _table({"name": "Whiterun Guard", "base_id": "", "race": "Nord", "bio": "Keeps the peace."},
                {"name": "Riverwood Guard", "base_id": "", "race": "Nord", "bio": "Also keeps the peace."})
    override = tmp_path / "my_overrides.csv"
    result = handlers["on_save"](label, "Keeps the peace.", "", {label: key}, {key: label}, str(override), None, df)
    assert "Riverwood Guard () — Nord" in result[2]
