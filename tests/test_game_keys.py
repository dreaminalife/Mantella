"""The JSON keys the game sends and reads (MantellaConstants.psc in Mantella-Spell) are the ones MantellaSoftware uses.
A key spelled differently on one side is silently ignored at runtime, so it is caught here instead.
Needs the Mantella-Spell repository next to this one, or its path in MANTELLA_SPELL_PATH. Skipped without it"""
import os
import re

import pytest

from src.games.skyrim import Skyrim
from src.http.communication_constants import communication_constants as comm_consts

SPELL_PATH = os.environ.get("MANTELLA_SPELL_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "Mantella-Spell"))
GAME_CONSTANTS = os.path.join(SPELL_PATH, "Scripts", "Source", "MantellaConstants.psc")
STRING_PROPERTY = re.compile(r'^\s*string\s+property\s+(\w+)\s*=\s*"([^"]*)"\s+auto', re.MULTILINE | re.IGNORECASE) # Not the commented out ones


def server_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    for owner in (comm_consts, Skyrim): # Skyrim reads the weather keys itself
        keys.update({name: value for name, value in vars(owner).items() if name.isupper() and isinstance(value, str)})
    return keys


def game_keys() -> dict[str, str]:
    """The game's string constants that are JSON keys or values, by name"""
    if not os.path.exists(GAME_CONSTANTS):
        pytest.skip(f"Mantella-Spell not found at {os.path.abspath(SPELL_PATH)}. Set MANTELLA_SPELL_PATH to its folder")
    with open(GAME_CONSTANTS, encoding="utf-8") as f:
        constants = dict(STRING_PROPERTY.findall(f.read()))
    return {name: value for name, value in constants.items() if value.startswith(comm_consts.PREFIX) and value != comm_consts.PREFIX}


def test_every_key_the_game_uses_is_known_to_the_server():
    server_values = set(server_keys().values())
    unknown = {name: value for name, value in game_keys().items() if value not in server_values}
    assert not unknown, f"The game uses keys MantellaSoftware doesn't know: {unknown}"


def test_keys_with_the_same_name_have_the_same_value():
    server = server_keys()
    different = {name: (value, server[name]) for name, value in game_keys().items() if name in server and server[name] != value}
    assert not different, f"Same name, different value (game, MantellaSoftware): {different}"
