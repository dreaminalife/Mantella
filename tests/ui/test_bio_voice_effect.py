"""The Voice effect list in the Bio Editor: one NPC's voice effect, saved in the voice_effect column of their character override"""
import pandas as pd

from src.config.config_loader import ConfigLoader
from src.config.definitions.game_definitions import GameEnum
from src.ui import bio_voice_effect
from src.ui.start_ui import StartUI

LABEL = "Lydia (000A2C94) — NordRace"
KEY = "Lydia§000A2C94§NordRace"


def bio_editor(tmp_path):
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    handlers = {}
    for f in blocks.fns:
        name = getattr(f.fn, "__name__", "")
        if name in ("on_select", "on_save") and "generate_bio_editor_page" in getattr(f.fn, "__qualname__", "") and name not in handlers:
            handlers[name] = f.fn
    return handlers


def test_the_list_offers_automatic_none_and_every_effect(tmp_path):
    values = [value for _, value in bio_voice_effect.effect_choices(str(tmp_path))]
    assert values[:2] == ["", "none"] and {"draugr", "ghost", "vampire_lord"} <= set(values)
    assert bio_voice_effect.picked_value(str(tmp_path), "Vampire Lord") == "vampire_lord"
    assert bio_voice_effect.picked_value(str(tmp_path), "NONE") == "none"
    assert bio_voice_effect.picked_value(str(tmp_path), "") == ""
    assert "banshee" in [value for _, value in bio_voice_effect.effect_choices(str(tmp_path), "banshee")] # kept, so it isn't lost on Save


def test_an_effect_that_isnt_ticked_says_so(tmp_path):
    from src.tts import voice_effects
    labels = dict((value, label) for label, value in bio_voice_effect.effect_choices(str(tmp_path)))
    assert labels["ghost"] == "Ghost (turned off)" and "ticked" in labels[""]
    voice_effects.shared_voice_effects(str(tmp_path)).set_on(["ghost"])
    labels = dict((value, label) for label, value in bio_voice_effect.effect_choices(str(tmp_path)))
    assert labels["ghost"] == "Ghost" and labels["draugr"] == "Draugr (turned off)"


def test_picking_an_npc_shows_their_effect_and_save_writes_it_to_their_override(tmp_path):
    handlers = bio_editor(tmp_path)
    df = pd.DataFrame([{"name": "Lydia", "base_id": "000A2C94", "race": "NordRace", "bio": "Housecarl.", "voice_effect": "Ghost"}])
    selected = handlers["on_select"](LABEL, df, {LABEL: KEY}, "", "")
    assert selected[-2]["value"] == "ghost" # then the voice service

    override = tmp_path / "my_overrides.csv"
    handlers["on_save"](LABEL, "Housecarl.", "", {LABEL: KEY}, {KEY: LABEL}, str(override), "none")
    saved = pd.read_csv(override)
    assert saved.loc[saved["name"] == "Lydia", "voice_effect"].iloc[0] == "none"

    handlers["on_save"](LABEL, "Housecarl.", "", {LABEL: KEY}, {KEY: LABEL}, str(override), "dremora")
    saved = pd.read_csv(override)
    assert saved.loc[saved["name"] == "Lydia", "voice_effect"].iloc[0] == "dremora" and len(saved) == 1


def test_a_json_override_gets_the_effect_too(tmp_path):
    import json
    handlers = bio_editor(tmp_path)
    override = tmp_path / "lydia.json"
    override.write_text(json.dumps({"name": "Lydia", "base_id": "000A2C94", "race": "NordRace", "bio": "Old."}), encoding="utf-8")
    handlers["on_save"](LABEL, "New.", "", {LABEL: KEY}, {KEY: LABEL}, str(override), "ghost")
    saved = json.loads(override.read_text(encoding="utf-8"))
    assert saved["voice_effect"] == "ghost" and saved["bio"] == "New."


def test_saving_puts_the_effect_in_the_character_table_in_memory():
    df = pd.DataFrame([{"name": "Lydia", "base_id": "000A2C94", "race": "NordRace", "bio": ""}])
    assert bio_voice_effect.set_in_table(df, "Lydia", "000A2C94", "NordRace", "ghost")
    assert bio_voice_effect.effect_of_row(df, "Lydia", "000A2C94", "NordRace") == "ghost"
    assert not bio_voice_effect.set_in_table(df, "Serana", "02002B74", "DLC1NordRace", "ghost")
