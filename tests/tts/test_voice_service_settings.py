"""The TTS Service and Backup Voice Service settings list the player's voice setups"""
import json
import logging

import pytest

from src.config.config_loader import ConfigLoader
from src.config.definitions.game_definitions import GameEnum
from src.config.definitions.tts_definitions import NO_BACKUP, TTSDefinitions, TTSEnum
from src.tts.registry import voice_services
from src.tts.voice_setups import empty_setup


@pytest.fixture(autouse=True)
def no_setups_left_over():
    yield
    voice_services.set_setups([])


def write_setup(folder, name: str):
    setup = {**empty_setup(), "name": name, "url": "http://127.0.0.1:8080", "clip_field": "speaker_ref_b64"}
    (folder / "voice_setups.json").write_text(json.dumps({"setups": [setup]}), encoding="utf-8")


def load(folder) -> ConfigLoader:
    return ConfigLoader(mygame_folder_path=str(folder), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(folder / "prompt_profiles.json"))


def set_in_config_ini(folder, key: str, value: str):
    path = folder / "config.ini"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(f"{key} = {value}" if line.startswith(f"{key} =") else line for line in lines), encoding="utf-8")


def test_by_default_pocket_tts_speaks_with_no_backup(tmp_path):
    config = load(tmp_path)
    assert config.tts_service is None and config.tts_service_name == "Pocket TTS" and config.tts_backup_service_name == NO_BACKUP
    assert voice_services.find(config.tts_backup_service_name) is None # no backup service is started
    assert config.definitions.get_config_value_definition("tts_backup_service").options[0] == NO_BACKUP
    assert NO_BACKUP not in config.definitions.get_config_value_definition("tts_service").options


def test_no_backup_loads_without_a_warning(tmp_path, caplog):
    load(tmp_path)
    set_in_config_ini(tmp_path, "tts_backup_service", "None")
    with caplog.at_level(logging.DEBUG):
        config = load(tmp_path)
    assert config.tts_backup_service_name == NO_BACKUP
    assert "is not in the list of voice services" not in caplog.text and "no longer exists" not in caplog.text


def test_a_voice_setup_keeps_none_in_the_backup_list(tmp_path):
    load(tmp_path)
    write_setup(tmp_path, "My Pocket")
    config = load(tmp_path)
    backup = config.definitions.get_config_value_definition("tts_backup_service")
    TTSDefinitions.refresh_voice_service_options(backup) # after a setup is saved on the page
    assert backup.options[0] == NO_BACKUP and "My Pocket" in backup.options


def piper_folder(tmp_path):
    """A Piper install with one voice, as the original Mantella mod has it"""
    folder = tmp_path / "piper"
    (folder / "models" / "skyrim" / "low").mkdir(parents=True)
    (folder / "piper.exe").write_bytes(b"")
    (folder / "models" / "skyrim" / "low" / "femalenord.onnx").write_bytes(b"")
    return folder


def test_piper_without_piper_on_this_pc_moves_to_pocket_tts(tmp_path, caplog):
    load(tmp_path)
    set_in_config_ini(tmp_path, "tts_service", "Piper")
    set_in_config_ini(tmp_path, "tts_backup_service", "Piper")
    with caplog.at_level(logging.DEBUG):
        config = load(tmp_path)
    assert config.tts_service is None and config.tts_service_name == "Pocket TTS" and config.tts_backup_service_name == NO_BACKUP
    assert "Piper isn't on this PC" in caplog.text
    saved = (tmp_path / "config.ini").read_text(encoding="utf-8")
    assert "tts_service = Pocket TTS" in saved and "tts_backup_service = None" in saved # kept for the next start


def test_piper_stays_when_it_is_installed(tmp_path):
    load(tmp_path)
    set_in_config_ini(tmp_path, "piper_folder", str(piper_folder(tmp_path)))
    set_in_config_ini(tmp_path, "tts_service", "Piper")
    set_in_config_ini(tmp_path, "tts_backup_service", "Piper")
    config = load(tmp_path)
    assert config.tts_service == TTSEnum.PIPER and config.tts_service_name == "Piper" and config.tts_backup_service_name == "Piper"


def test_piper_without_voices_counts_as_missing(tmp_path):
    load(tmp_path)
    folder = piper_folder(tmp_path)
    for voice in (folder / "models").rglob("*.onnx"):
        voice.unlink()
    set_in_config_ini(tmp_path, "piper_folder", str(folder))
    set_in_config_ini(tmp_path, "tts_service", "Piper")
    config = load(tmp_path)
    assert config.tts_service_name == "Pocket TTS"


def test_a_voice_setup_can_be_the_tts_service_and_the_backup(tmp_path):
    load(tmp_path) # writes config.ini
    write_setup(tmp_path, "My Pocket")
    set_in_config_ini(tmp_path, "tts_service", "My Pocket")
    set_in_config_ini(tmp_path, "tts_backup_service", "My Pocket")
    config = load(tmp_path)
    assert config.tts_service is None and config.tts_service_name == "My Pocket" and config.tts_backup_service_name == "My Pocket"
    selection = config.definitions.get_config_value_definition("tts_service")
    assert "My Pocket" in selection.options


def test_a_removed_setup_falls_back_to_pocket_tts_with_a_warning(tmp_path, caplog):
    load(tmp_path)
    set_in_config_ini(tmp_path, "tts_service", "Gone Setup")
    with caplog.at_level(logging.DEBUG):
        config = load(tmp_path)
    assert config.tts_service is None and config.tts_service_name == "Pocket TTS"
    assert "'Gone Setup' chosen as TTS Service no longer exists" in caplog.text


def test_the_old_values_still_work(tmp_path):
    load(tmp_path)
    set_in_config_ini(tmp_path, "tts_service", "xVASynth")
    config = load(tmp_path)
    assert config.tts_service == TTSEnum.XVASYNTH and config.tts_service_name == "xVASynth"
