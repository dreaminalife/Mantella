"""Mantella's list of voice models (data/voice_models.json), the models the player adds and removes, and each online service's
settings kept per model (Step 11)"""
import json
from datetime import date
from types import SimpleNamespace

import pytest

from src.tts.model_catalog import CatalogCopy, ListChanges, ModelCatalog, ServiceModel, ServiceModels
from src.tts.online_service import merged
from src.tts.voice_style import HEYGEN_EXPRESSIVENESS, HEYGEN_SIMILARITY, INWORLD_DELIVERY, ServiceStyle, shared_values


def catalog_text(heygen_models: list[dict], **more) -> str:
    return json.dumps({"services": {"HeyGen": {"default": heygen_models[0]["id"], "models": heygen_models}}, **more})


def use_catalog(monkeypatch, tmp_path, text: str) -> ModelCatalog:
    """Mantella's list, as if it shipped *text*"""
    path = tmp_path / "shipped.json"
    path.write_text(text, encoding="utf-8")
    catalog = ModelCatalog(str(path))
    monkeypatch.setattr(ModelCatalog, "_shared", catalog)
    return catalog


TURBO = {"id": "heygen-voice-1-turbo", "name": "Turbo", "price": None, "sound_symbols": True}
TURBO_2 = {"id": "heygen-voice-2-turbo", "name": "Turbo 2", "price": 12, "skips": ["heygen_similarity"],
           "defaults": {"heygen_expressiveness": 0.3}}


def test_the_shipped_list_has_every_online_service():
    catalog = ModelCatalog()
    for name in ("ElevenLabs", "Inworld", "HeyGen", "Qwen", "Boson AI", "Iodine"):
        assert catalog.models(name, date(2026, 11, 19)), name
        assert ServiceModels.of(name).default().known, name
    assert catalog.find("HeyGen", "Turbo").id == "heygen-voice-1-turbo" # older Mantella saved the name
    assert catalog.find("Inworld", "inworld-tts-2-flash").skips == ("inworld_delivery", "inworld_directions")
    assert catalog.service("Qwen").clones_per_model and not catalog.service("HeyGen").clones_per_model


def test_a_model_past_its_end_date_is_no_longer_offered():
    catalog = ModelCatalog()
    assert "heygen-voice-1-turbo-free" in [m.id for m in catalog.models("HeyGen", date(2026, 11, 30))]
    assert "heygen-voice-1-turbo-free" not in [m.id for m in catalog.models("HeyGen", date(2026, 12, 1))]
    assert catalog.find("HeyGen", "heygen-voice-1-turbo-free").ended(date(2026, 12, 1)) # still known, for a player still on it


def test_fields_mantella_doesnt_know_are_left_out_and_broken_lists_are_turned_down():
    copy = CatalogCopy.parse(catalog_text([{**TURBO, "loudness": "very"}, {"name": "no ID"}], newer_field=1))
    assert [m.id for m in copy.services["HeyGen"].models] == ["heygen-voice-1-turbo"]
    assert CatalogCopy.parse("not json") is None and CatalogCopy.parse(json.dumps({"about": "no services"})) is None


def test_a_model_typed_in_is_used_as_typed_with_every_setting():
    heygen = ServiceModels.of("HeyGen")
    typed = heygen.resolve("heygen-voice-2")
    assert typed == ServiceModel.unknown("heygen-voice-2") and not typed.known and typed.takes("heygen_similarity")
    assert heygen.resolve("") == heygen.default() and heygen.resolve(" Turbo Free ").id == "heygen-voice-1-turbo-free"
    assert heygen.picked(SimpleNamespace(heygen_model="Turbo")).id == "heygen-voice-1-turbo"
    style = ServiceStyle(SimpleNamespace(inworld_model="inworld-tts-3"), "Inworld")
    assert INWORLD_DELIVERY in style.settings() # not on the list: nothing is known to be skipped
    style = ServiceStyle(SimpleNamespace(inworld_model="TTS-2 Flash"), "Inworld")
    assert INWORLD_DELIVERY not in style.settings()


def test_the_model_list_offers_ids_shown_by_name():
    options, labels = ServiceModels.of("Inworld").options()
    assert options == ["inworld-tts-2", "inworld-tts-2-flash"] and labels["inworld-tts-2-flash"] == "TTS-2 Flash"


def test_extra_fields_are_merged_into_a_request():
    body = {"text": "Hi", "voice_settings": {"stability": 0.5, "speed": 1.0}}
    assert merged(body, {"voice_settings": {"speed": 1.1}, "seed": 3}) == {"text": "Hi", "voice_settings": {"stability": 0.5, "speed": 1.1}, "seed": 3}
    assert merged(body, {"voice_settings": "off"})["voice_settings"] == "off" # the player's wins
    assert body["voice_settings"]["speed"] == 1.0 # the request itself isn't changed


# ---- each model's own values on the Text-to-Speech page ----

def page(tmp_path):
    from src.config.config_loader import ConfigLoader
    from src.config.definitions.game_definitions import GameEnum
    return ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))


def value(config, name):
    return config.definitions.get_config_value_definition(name).value


def test_each_model_keeps_its_own_settings(tmp_path):
    config = page(tmp_path)
    style = ServiceStyle(config, "HeyGen")
    assert value(config, "heygen_model") == "heygen-voice-1-turbo"
    config.definitions.get_config_value_definition("heygen_expressiveness").value = 0.2 # set for Turbo
    shown = style.switch_model("heygen-voice-1-turbo-free")
    assert shown["heygen_expressiveness"] == 0.5 and value(config, "heygen_expressiveness") == 0.5 # Turbo Free starts at its own
    config.definitions.get_config_value_definition("heygen_extra_request").value = '{"speed": 1.1}'
    assert style.switch_model("heygen-voice-1-turbo")["heygen_expressiveness"] == 0.2 # Turbo's is back
    assert value(config, "heygen_extra_request") == "" and style.extra_request() == {}
    style.switch_model("heygen-voice-1-turbo-free")
    assert value(config, "heygen_expressiveness") == 0.5 and style.extra_request() == {"speed": 1.1}
    assert value(config, "heygen_model") == "heygen-voice-1-turbo-free"
    again = page(tmp_path) # Mantella started again: kept in config.ini
    assert ServiceStyle(again, "HeyGen").switch_model("heygen-voice-1-turbo")["heygen_expressiveness"] == 0.2


def test_a_new_model_starts_where_mantellas_list_says(tmp_path, monkeypatch):
    config = page(tmp_path)
    use_catalog(monkeypatch, tmp_path, catalog_text([TURBO, TURBO_2]))
    style = ServiceStyle(config, "HeyGen")
    shown = style.switch_model("heygen-voice-2-turbo")
    assert shown["heygen_expressiveness"] == 0.3 # the list's default for Turbo 2
    assert HEYGEN_SIMILARITY not in style.settings() and HEYGEN_EXPRESSIVENESS in style.settings() # Turbo 2 skips Similarity


def test_a_model_changed_in_config_ini_by_hand_gets_its_own_settings(tmp_path):
    config = page(tmp_path)
    ServiceStyle(config, "HeyGen").switch_model("heygen-voice-1-turbo-free")
    config.definitions.get_config_value_definition("heygen_expressiveness").value = 0.9 # Turbo Free's
    ini = tmp_path / "config.ini"
    text = ini.read_text(encoding="utf-8").replace("heygen_model = heygen-voice-1-turbo-free", "heygen_model = Turbo")
    ini.write_text(text, encoding="utf-8")
    again = page(tmp_path)
    assert value(again, "heygen_model") == "heygen-voice-1-turbo" # the name is kept as the ID from now on
    assert value(again, "heygen_expressiveness") == 0.5 # Turbo's, not Turbo Free's
    assert '"current":"heygen-voice-1-turbo"' in value(again, "heygen_model_settings")


# ---- a voice's own values, per model ----

def test_a_voices_own_value_is_kept_for_each_model(tmp_path):
    from src.voice_clips.clip_store import NPCS, VOICE_TYPES, ClipStore
    store = ClipStore(str(tmp_path / "voice_clips"))
    store.set_voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness", 0.3) # kept before settings were per model
    store.set_pitch(VOICE_TYPES, "femaleeventoned", 2)
    assert store.voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness", "any-model") == 0.3 # the picked model's
    store.pin_plain_values(["heygen_expressiveness"], "heygen-voice-1-turbo") # another model picked
    assert store.voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness", "heygen-voice-1-turbo-free") is None
    store.set_voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness", 0.8, "heygen-voice-1-turbo-free")
    assert store.voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness") == {"heygen-voice-1-turbo": 0.3,
                                                                                            "heygen-voice-1-turbo-free": 0.8}
    assert store.pitch(VOICE_TYPES, "femaleeventoned") == 2 # Pitch is for every model
    store.set_voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness", None, "heygen-voice-1-turbo")
    store.set_voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness", None, "heygen-voice-1-turbo-free")
    assert store.voice_settings(VOICE_TYPES, "femaleeventoned") == {"pitch": 2}


def test_switching_models_keeps_each_voices_older_values_with_the_model_they_were_set_for(tmp_path):
    from src.tts.voice_picker import VOICE_TYPE_CLIP, VoicePick
    from src.voice_clips.clip_store import VOICE_TYPES, ClipStore
    config = page(tmp_path)
    store = ClipStore(str(tmp_path / "voice_clips"))
    store.set_voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness", 0.3) # from before settings were per model
    style = ServiceStyle(config, "HeyGen")
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", "a.wav")
    assert style.voice_value(store, pick, HEYGEN_EXPRESSIVENESS) == 0.3
    style.switch_model("heygen-voice-1-turbo-free", store)
    assert style.voice_value(store, pick, HEYGEN_EXPRESSIVENESS) == 0.5 # Turbo Free: the page's
    style.switch_model("heygen-voice-1-turbo", store)
    assert style.voice_value(store, pick, HEYGEN_EXPRESSIVENESS) == 0.3


def test_a_friends_values_are_kept_for_each_model():
    assert shared_values({"heygen_expressiveness": {"heygen-voice-1-turbo": 0.2, "heygen-voice-2": 7}, "heygen_similarity": "Medium",
                          "heygen_extra_request": '{"speed": 2}', "unknown": 1}) == {
        "heygen_expressiveness": {"heygen-voice-1-turbo": 0.2}, # 7 isn't a value it takes
        "heygen_similarity": "Medium"} # an older file: for the importer's picked model. Settings Mantella doesn't have yet aren't shared


# ---- fixes from the review ----

def test_heygens_similarity_is_kept_for_each_model(tmp_path):
    from src.tts.voice_picker import VOICE_TYPE_CLIP, VoicePick
    from src.voice_clips.clip_store import VOICE_TYPES, ClipStore
    config = page(tmp_path)
    store = ClipStore(str(tmp_path / "voice_clips"))
    style = ServiceStyle(config, "HeyGen")
    assert HEYGEN_SIMILARITY in style.per_model_settings() and HEYGEN_EXPRESSIVENESS in style.per_model_settings()
    style.switch_model("heygen-voice-1-turbo", store)
    config.definitions.get_config_value_definition("heygen_similarity").value = "High" # Turbo's
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", "a.wav")
    style.set_voice_own(store, VOICE_TYPES, "femaleeventoned", HEYGEN_SIMILARITY, "Medium")
    assert store.voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_similarity") == {"heygen-voice-1-turbo": "Medium"}
    shown = style.switch_model("heygen-voice-1-turbo-free", store)
    assert shown["heygen_similarity"] == "Highest" and value(config, "heygen_similarity") == "Highest" # Turbo Free starts at the most
    assert style.voice_value(store, pick, HEYGEN_SIMILARITY) == "Highest" # the voice's own was for Turbo
    assert style.switch_model("heygen-voice-1-turbo", store)["heygen_similarity"] == "High"
    assert style.voice_value(store, pick, HEYGEN_SIMILARITY) == "Medium"
    assert shared_values({"heygen_similarity": {"heygen-voice-1-turbo": "Medium"}}) == {"heygen_similarity": {"heygen-voice-1-turbo": "Medium"}}


def test_models_are_carried_over_on_a_start_that_also_writes_a_backup(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.play_error_sound", lambda: None)
    page(tmp_path)
    ini = tmp_path / "config.ini"
    lines = [line for line in ini.read_text(encoding="utf-8").splitlines() if not line.startswith("heygen_model_settings")]
    text = "\n".join(lines).replace("heygen_model = heygen-voice-1-turbo", "heygen_model = Turbo")
    ini.write_text(text + "\nno_longer_a_setting = 1\n", encoding="utf-8") # an old setting: config.ini is backed up
    again = page(tmp_path)
    assert value(again, "heygen_model") == "heygen-voice-1-turbo"
    assert '"current":"heygen-voice-1-turbo"' in value(again, "heygen_model_settings")
    assert "heygen_model = heygen-voice-1-turbo" in ini.read_text(encoding="utf-8")


def test_a_model_that_skips_audio_tags_or_directions_gets_none(monkeypatch, tmp_path):
    from src.tts import boson, heygen, inworld, iodine, qwen
    from src.tts.voice_tags import NO_TAGS
    services = {name: {"default": "plain", "models": [{"id": "plain", "name": "Plain"},
                                                        {"id": "quiet", "name": "Quiet", "skips": [f"{key}_audio_tags", f"{key}_directions"]}]}
                for name, key in (("HeyGen", "heygen"), ("Inworld", "inworld"), ("Qwen", "qwen"), ("Boson AI", "boson"), ("Iodine", "iodine"))}
    use_catalog(monkeypatch, tmp_path, json.dumps({"services": services}))
    for module, key in ((heygen, "heygen"), (inworld, "inworld"), (qwen, "qwen"), (boson, "boson"), (iodine, "iodine")):
        on = {f"{key}_audio_tags": True, f"{key}_directions": True, "language": "en"}
        assert module.voice_tags(SimpleNamespace(**on, **{f"{key}_model": "plain"})) != NO_TAGS
        assert module.voice_tags(SimpleNamespace(**on, **{f"{key}_model": "quiet"})) == NO_TAGS, key


def test_a_model_still_picked_after_it_ended_shows_its_name(monkeypatch, tmp_path):
    free = {"id": "heygen-voice-1-turbo-free", "name": "Turbo Free", "ends": "2020-01-01"}
    use_catalog(monkeypatch, tmp_path, catalog_text([TURBO, free]))
    heygen = ServiceModels.of("HeyGen")
    assert heygen.choices("heygen-voice-1-turbo") == [("Turbo", "heygen-voice-1-turbo")]
    assert heygen.choices("heygen-voice-1-turbo-free") == [("Turbo", "heygen-voice-1-turbo"), ("Turbo Free", "heygen-voice-1-turbo-free")]
    assert heygen.choices("heygen-voice-2") == [("Turbo", "heygen-voice-1-turbo")] # typed in: Gradio shows it as typed


# ---- the player's own list ----

def test_the_player_adds_and_removes_models(tmp_path, monkeypatch):
    use_catalog(monkeypatch, tmp_path, catalog_text([TURBO, TURBO_2]))
    config = page(tmp_path)
    heygen = ServiceModels.of("HeyGen")
    ids = lambda: [m.id for m in heygen.offered(config)]
    assert ids() == ["heygen-voice-1-turbo", "heygen-voice-2-turbo"]
    assert heygen.add(config, " heygen-voice-3 ").id == "heygen-voice-3"
    assert heygen.add(config, "heygen-voice-3") is None and heygen.add(config, "Turbo 2") is None and heygen.add(config, " ") is None
    assert ids() == ["heygen-voice-1-turbo", "heygen-voice-2-turbo", "heygen-voice-3"]
    assert value(config, "heygen_model") == "heygen-voice-1-turbo"
    selection = config.definitions.get_config_value_definition("heygen_model")
    assert selection.options == ids() # the Model setting offers them too
    assert heygen.remove(config, "heygen-voice-1-turbo") and heygen.remove(config, "heygen-voice-3")
    assert ids() == ["heygen-voice-2-turbo"]
    assert not heygen.remove(config, "heygen-voice-2-turbo") # the last one stays
    assert not heygen.remove(config, "heygen-voice-9") # not in the list
    assert heygen.first_offered(config).id == "heygen-voice-2-turbo" # the default was removed
    again = page(tmp_path) # kept in config.ini
    assert [m.id for m in heygen.offered(again)] == ["heygen-voice-2-turbo"]
    assert again.definitions.get_config_value_definition("heygen_model").options == ["heygen-voice-2-turbo"]
    assert heygen.add(again, "Turbo").id == "heygen-voice-1-turbo" # one of Mantella's, by its name: listed again
    heygen.remove(again, "heygen-voice-1-turbo")
    heygen.put_back(again)
    assert [m.id for m in heygen.offered(again)] == ["heygen-voice-1-turbo", "heygen-voice-2-turbo"]
    assert ListChanges.parse("not json") == ListChanges() and ListChanges().text() == ""


def test_a_removed_model_keeps_its_settings(tmp_path):
    config = page(tmp_path)
    heygen = ServiceModels.of("HeyGen")
    style = ServiceStyle(config, "HeyGen")
    heygen.add(config, "heygen-voice-3")
    style.show_model("heygen-voice-3")
    config.definitions.get_config_value_definition("heygen_expressiveness").value = 0.8
    style.show_model("heygen-voice-1-turbo")
    assert heygen.remove(config, "heygen-voice-3")
    heygen.add(config, "heygen-voice-3")
    assert style.show_model("heygen-voice-3")["heygen_expressiveness"] == 0.8


def test_every_online_service_has_a_talking_speed_for_each_model_and_pocket_tts_one():
    from src.tts.voice_style import SPEEDS, SETUP_SPEED, SPEED_HIGH, SPEED_LOW
    for models in ServiceModels.all():
        assert SPEEDS[models.service].name == f"{models.key}_speed" and SPEEDS[models.service].per_model, models.service
    assert not SPEEDS["Pocket TTS"].per_model and SPEEDS["Pocket TTS"].name == "pocket_tts_speed"
    assert SETUP_SPEED.per_model # kept by setup name
    assert (SPEED_LOW, SPEED_HIGH) == (0.7, 1.5) and SPEEDS["Iodine"].clean(2.0) is None and SPEEDS["Iodine"].clean(1.25) == 1.25
