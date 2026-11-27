"""The Voice service list in the Bio Editor: one NPC's tts_service column, saved in their character override"""
import json

import pandas as pd

from src.ui import bio_new_npc, bio_voice_service
from tests.ui.test_bio_voice_effect import KEY, LABEL, bio_editor


def lydia(**cells):
    return pd.DataFrame([{"name": "Lydia", "base_id": "000A2C94", "race": "NordRace", "bio": "Housecarl.", **cells}])


def test_the_service_list_offers_the_default_and_every_voice_service():
    values = [value for _, value in bio_voice_service.service_choices()]
    assert values[0] == "" and {"Pocket TTS", "ElevenLabs", "Inworld"} <= set(values)
    assert bio_voice_service.picked_service("xva") == bio_voice_service.picked_service("xVASynth") != "xva"
    assert bio_voice_service.picked_service("11labs") == "ElevenLabs"
    assert bio_voice_service.picked_service("") == ""


def test_a_service_name_no_service_has_is_kept_so_save_doesnt_lose_it():
    choices = dict((value, label) for label, value in bio_voice_service.service_choices("My Old Setup"))
    assert "no voice service by that name" in choices["My Old Setup"]
    assert bio_voice_service.picked_service("My Old Setup") == "My Old Setup"


def test_picking_an_npc_shows_their_service(tmp_path):
    handlers = bio_editor(tmp_path)
    selected = handlers["on_select"](LABEL, lydia(tts_service="xva"), {LABEL: KEY}, "", "")
    assert selected[-1]["value"] == bio_voice_service.picked_service("xvasynth")


def test_save_writes_the_service_to_their_override(tmp_path):
    handlers = bio_editor(tmp_path)
    override = tmp_path / "my_overrides.csv"
    _, new_df, *_ = handlers["on_save"](LABEL, "Housecarl.", "", {LABEL: KEY}, {KEY: LABEL}, str(override), "", lydia(), "Inworld")
    saved = pd.read_csv(override, dtype=str, keep_default_na=False)
    assert saved.loc[saved["name"] == "Lydia", "tts_service"].iloc[0] == "Inworld"
    assert new_df.loc[0, "tts_service"] == "Inworld" # used without a reload

    # Back to the default service
    _, new_df, *_ = handlers["on_save"](LABEL, "Housecarl.", "", {LABEL: KEY}, {KEY: LABEL}, str(override), "", new_df, "")
    saved = pd.read_csv(override, dtype=str, keep_default_na=False)
    assert saved.loc[saved["name"] == "Lydia", "tts_service"].iloc[0] == "" and len(saved) == 1
    assert new_df.loc[0, "tts_service"] == ""


def test_a_save_from_the_llm_part_keeps_the_service(tmp_path):
    handlers = bio_editor(tmp_path)
    override = tmp_path / "my_overrides.csv"
    handlers["on_save"](LABEL, "Old.", "", {LABEL: KEY}, {KEY: LABEL}, str(override), None, lydia(tts_service="Inworld"))
    saved = pd.read_csv(override, dtype=str, keep_default_na=False)
    assert saved.loc[0, "tts_service"] == "Inworld" and saved.loc[0, "bio"] == "Old."


def test_a_json_override_gets_the_service_too(tmp_path):
    handlers = bio_editor(tmp_path)
    override = tmp_path / "lydia.json"
    override.write_text(json.dumps({"name": "Lydia", "base_id": "000A2C94", "race": "NordRace", "bio": "Old."}), encoding="utf-8")
    handlers["on_save"](LABEL, "New.", "", {LABEL: KEY}, {KEY: LABEL}, str(override), "", lydia(), "ElevenLabs")
    saved = json.loads(override.read_text(encoding="utf-8"))
    assert saved["tts_service"] == "ElevenLabs" and saved["bio"] == "New."


def test_a_new_npc_gets_the_service():
    assert bio_new_npc.new_row("Whiterun Guard", "", "Nord", "Keeps the peace.", "", "", " Inworld ")["tts_service"] == "Inworld"
    assert "tts_service" not in bio_new_npc.new_row("Whiterun Guard", "", "Nord", "", "", "")
