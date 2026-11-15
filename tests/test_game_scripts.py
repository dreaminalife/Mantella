"""Mistakes in the game's scripts (Mantella-Spell) that compile but fail in the game.
Needs the Mantella-Spell repository next to this one, or its path in MANTELLA_SPELL_PATH. Skipped without it"""
import glob
import os
import re

import pytest

SPELL_PATH = os.environ.get("MANTELLA_SPELL_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "Mantella-Spell"))
SCRIPTS = os.path.join(SPELL_PATH, "Scripts", "Source", "Mantella*.psc")
ARRAY_DECLARATION = re.compile(r"\b\w+\[\]\s+(\w+)", re.IGNORECASE)


def test_no_array_is_set_to_none_inside_a_function():
    """Assigning None to an array fails in the game ("Cannot cast from None to Actor[]"), and a local array declared "= None" then can't
    take an array at all, so it stays None. That is how Nearby and Random Events once found nobody nearby. A script variable declared
    "= None" is fine. Declare locals without "= None", and clear arrays with an array variable that is never assigned"""
    paths = glob.glob(SCRIPTS)
    if not paths:
        pytest.skip(f"Mantella-Spell not found at {os.path.abspath(SPELL_PATH)}. Set MANTELLA_SPELL_PATH to its folder")
    found = []
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [line.split(";")[0] for line in f.read().splitlines()]
        arrays = {name.lower() for line in lines for name in ARRAY_DECLARATION.findall(line)}
        for number, line in enumerate(lines, 1):
            match = re.match(r"\s+(?:\w+\[\]\s+)?(\w+)\s*=\s*none\s*$", line, re.IGNORECASE) # Indented: inside a function
            if match and match.group(1).lower() in arrays:
                found.append(f"{os.path.basename(path)}:{number}: {line.strip()}")
    assert not found, "Arrays set to None:\n" + "\n".join(found)
