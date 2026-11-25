import os
from types import SimpleNamespace

import pytest

from src.config.definitions.game_definitions import started_inside_game_folder
from src.setup import settings_problem_message


def make_folders(root, *parts, esp: bool = True) -> str:
    """Makes <root>/<parts...>/Plugins/MantellaSoftware, with the mod's Sound/Voice/Mantella.esp in the Data folder when esp, and returns
    the exe folder. Mantella.esp there is a folder, as the mod has it: the NPCs' lines go in it"""
    data = os.path.join(root, *parts)
    exe_folder = os.path.join(data, "SKSE", "Plugins", "MantellaSoftware")
    os.makedirs(exe_folder)
    if esp:
        os.makedirs(os.path.join(data, "Sound", "Voice", "Mantella.esp", "MantellaVoice00"))
    return exe_folder


def test_inside_a_game_folder(tmp_path):
    # where the game starts it, and where Mod Organizer 2 shows it to a program it starts (a lower-case data folder too)
    assert started_inside_game_folder(make_folders(str(tmp_path), "Skyrim Special Edition", "data"))


def test_not_without_the_mod(tmp_path):
    assert not started_inside_game_folder(make_folders(str(tmp_path), "Skyrim Special Edition", "Data", esp=False))


def test_not_in_a_mod_managers_own_folder(tmp_path):
    # Mod Organizer 2's real mod folder: the mod is there, but no game folder is above it, so the game can't be worked out
    assert not started_inside_game_folder(make_folders(str(tmp_path), "mods", "Mantella"))


def test_not_somewhere_else(tmp_path):
    assert not started_inside_game_folder(str(tmp_path))


def fake_config(violations: dict[str, list[str]]):
    names = {"skyrimvr_mod_folder": "Skyrim VR: Path to Mantella Spell Mod", "xvasynth_folder": "xVASynth Folder"}

    def definition(identifier):
        return SimpleNamespace(name=names[identifier])

    definitions = SimpleNamespace(constraint_violations=violations, get_config_value_definition=definition)
    return SimpleNamespace(definitions=definitions, port=4999)


@pytest.mark.parametrize("identifier, says_game_tab", [("skyrimvr_mod_folder", True), ("xvasynth_folder", False)])
def test_problem_message_names_the_setting(identifier, says_game_tab):
    message = settings_problem_message(fake_config({identifier: ["\nThere's no Sound\\Voice\\Mantella.esp in C:\\Mods\n  More help: here"]}))
    assert "Mantella can't start until these settings are fixed:" in message
    name = "Skyrim VR: Path to Mantella Spell Mod" if says_game_tab else "xVASynth Folder"
    assert f"- {name}:There's no Sound\\Voice\\Mantella.esp in C:\\Mods\n    More help: here" in message
    assert ("Game tab: http://localhost:4999/ui" in message) == says_game_tab
