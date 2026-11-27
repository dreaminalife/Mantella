"""The Bio Editor's Save button: writes the NPC's bio without losing anything else in the override file"""
import json

import pandas as pd
import pytest

from src.ui import bio_override_file


def _read(path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def test_saving_changes_only_that_npcs_cells_and_keeps_every_other_cell_as_it_was(tmp_path):
    path = tmp_path / "character_overrides.csv"
    path.write_text("name,base_id,race,bio,ref_id,notes\nAdara,013385,Redguard,Old.,NA,\nLydia,0A2C94,Nord,Housecarl.,,mine\n", encoding="utf-8")
    backup = bio_override_file.save_npc(str(path), "Lydia", "0A2C94", "Nord", "New.", {"voice_effect": "ghost", "author": None}, "whiterun_hold")
    saved = _read(path)
    assert saved.iloc[0].to_dict() == {"name": "Adara", "base_id": "013385", "race": "Redguard", "bio": "Old.", "ref_id": "NA",
                                       "notes": "", "voice_effect": "", "tags": ""}
    assert saved.iloc[1][["bio", "notes", "voice_effect", "tags"]].tolist() == ["New.", "mine", "ghost", "whiterun_hold"]
    assert len(saved) == 2 and "author" not in saved.columns
    assert open(backup, encoding="utf-8").read().startswith("name,base_id,race,bio,ref_id,notes\nAdara,013385")


def test_a_base_id_of_only_digits_is_still_found_so_no_second_row_is_added(tmp_path):
    path = tmp_path / "character_overrides.csv"
    path.write_text("name,base_id,race,bio\nAdara,013385,Redguard,Old.\n", encoding="utf-8")
    bio_override_file.save_npc(str(path), "Adara", "013385", "Redguard", "New.", {}, None)
    saved = _read(path)
    assert saved.to_dict("records") == [{"name": "Adara", "base_id": "013385", "race": "Redguard", "bio": "New."}]


def test_an_npc_not_in_the_file_is_added_at_the_end_and_a_new_file_is_made(tmp_path):
    path = tmp_path / "sub" / "character_overrides.csv"
    assert bio_override_file.save_npc(str(path), "Ria", "01A6E5", "Imperial", "Companion.", {}, "") is None
    bio_override_file.save_npc(str(path), "Lydia", "0A2C94", "Nord", "Housecarl.", {}, None)
    assert _read(path)["name"].tolist() == ["Ria", "Lydia"]


def test_tags_left_as_none_keep_what_the_file_says(tmp_path):
    path = tmp_path / "character_overrides.csv"
    path.write_text("name,base_id,race,bio,tags\nLydia,0A2C94,Nord,Old.,whiterun_hold\n", encoding="utf-8")
    bio_override_file.save_npc(str(path), "Lydia", "0A2C94", "Nord", "New.", {"tags": "something_else"}, None)
    assert _read(path).iloc[0]["tags"] == "whiterun_hold"


def test_a_json_file_that_cant_be_read_is_left_as_it_is(tmp_path):
    path = tmp_path / "mine.json"
    path.write_text('[{"name": "Lydia", "bio": "Old."}', encoding="utf-8")
    with pytest.raises(bio_override_file.NotSaved):
        bio_override_file.save_npc(str(path), "Lydia", "", "", "New.", {}, None)
    assert path.read_text(encoding="utf-8") == '[{"name": "Lydia", "bio": "Old."}'


def test_a_json_npc_is_updated_and_the_others_are_kept(tmp_path):
    path = tmp_path / "mine.json"
    path.write_text(json.dumps([{"name": "Lydia", "base_id": "0A2C94", "race": "Nord", "bio": "Old.", "author": "me"},
                                {"name": "Ria", "base_id": "01A6E5", "race": "Imperial", "bio": "Companion."}]), encoding="utf-8")
    bio_override_file.save_npc(str(path), "Lydia", "0A2C94", "Nord", "New.", {"voice_effect": "none", "author": "someone"}, "")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved[0] == {"name": "Lydia", "base_id": "0A2C94", "race": "Nord", "bio": "New.", "author": "me", "voice_effect": "none", "tags": ""}
    assert saved[1] == {"name": "Ria", "base_id": "01A6E5", "race": "Imperial", "bio": "Companion."}


def test_the_npc_is_found_however_the_list_writes_their_base_id_or_an_empty_race(tmp_path):
    path = tmp_path / "character_overrides.csv"
    path.write_text("name,base_id,race,bio\nAdara,013385,Redguard,Old.\nBandit,,,Old.\n", encoding="utf-8")
    bio_override_file.save_npc(str(path), "Adara", "13385", "Redguard", "New.", {}, None) # the list read 013385 as a number
    bio_override_file.save_npc(str(path), "Bandit", "nan", "nan", "Robs people.", {}, None) # the list shows an empty cell as nan
    saved = _read(path)
    assert saved["bio"].tolist() == ["New.", "Robs people."] and saved["base_id"].tolist() == ["013385", ""]


def test_json_is_written_as_plain_ascii_so_the_game_reads_it_in_any_encoding(tmp_path):
    path = tmp_path / "mine.json"
    bio_override_file.save_npc(str(path), "Ærin", "", "Nord", "Fisher.", {}, None)
    raw = path.read_bytes()
    assert raw.isascii() and json.loads(raw)[0]["name"] == "Ærin" and json.loads(raw)[0]["base_id"] == ""
