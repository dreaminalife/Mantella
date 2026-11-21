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


def _scripts_or_skip():
    paths = glob.glob(SCRIPTS)
    if not paths:
        pytest.skip(f"Mantella-Spell not found at {os.path.abspath(SPELL_PATH)}. Set MANTELLA_SPELL_PATH to its folder")
    return paths


def _code(name):
    """A script's lines without their comments"""
    with open(os.path.join(SPELL_PATH, "Scripts", "Source", name + ".psc"), encoding="utf-8", errors="replace") as f:
        return [line.split(";")[0].rstrip() for line in f.read().splitlines()]


def _function(script, name):
    """The lines inside a script's function, without comments"""
    lines = _code(script)
    start = next(i for i, line in enumerate(lines) if re.match(rf"\s*(?:\w+\s+)?function\s+{name}\s*\(", line, re.IGNORECASE))
    end = next(i for i in range(start + 1, len(lines)) if re.match(r"\s*endfunction\b", lines[i], re.IGNORECASE))
    return [line.strip() for line in lines[start + 1:end] if line.strip()]


SEND = "SKSE_HTTP.sendLocalhostHttpRequest("
ADD_HEARD_LINES = re.compile(r"(?:conversation\.)?AddHeardLines\(handle\)$", re.IGNORECASE)
HEARD_LINES_REQUESTS = [
    ("MantellaConversation", "StartConversation"),
    ("MantellaConversation", "RequestContinueConversation"),
    ("MantellaConversation", "sendRequestForPlayerInput"),
    ("MantellaConversation", "TrySendPendingEvent"),
    ("MantellaListenerScript", "RunNpcInitiativeCheck"),
    ("MantellaListenerScript", "RunNpcEventCheck"),
]


@pytest.mark.parametrize("script,function", HEARD_LINES_REQUESTS)
def test_heard_lines_come_right_before_the_final_send(script, function):
    """The plugin forgets the lines once handed over, so they are taken only when the request is sure to go: after the early returns,
    right before the send"""
    _scripts_or_skip()
    body = _function(script, function)
    sends = [i for i, line in enumerate(body) if line.startswith(SEND)]
    added = [i for i, line in enumerate(body) if ADD_HEARD_LINES.match(line)]
    assert sends and len(added) == 1, f"{script}.{function} needs exactly one AddHeardLines(handle)"
    assert added[0] == sends[-1] - 1, f"{script}.{function}: AddHeardLines must be the line right before the final send"
    assert added[0] > sends[0] or len(sends) == 1, f"{script}.{function}: never on the first (INIT) request"


def test_start_conversation_sends_no_heard_lines_with_the_init_request():
    _scripts_or_skip()
    body = _function("MantellaConversation", "StartConversation")
    first_send = next(i for i, line in enumerate(body) if line.startswith(SEND))
    assert not any(ADD_HEARD_LINES.match(line) for line in body[:first_send + 1])


def test_the_old_vanilla_dialogue_plugin_is_no_longer_called():
    paths = _scripts_or_skip()
    assert not os.path.exists(os.path.join(SPELL_PATH, "Scripts", "Source", "MantellaVanillaDialogue.psc"))
    found = []
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            for number, line in enumerate(f.read().splitlines(), 1):
                if re.search(r"MantellaVanillaDialogue|IsVanillaDialogueInstalled", line.split(";")[0]):
                    found.append(f"{os.path.basename(path)}:{number}: {line.strip()}")
    assert not found, "Still uses the old plugin:\n" + "\n".join(found)


def test_every_heard_lines_plugin_call_checks_the_plugin_is_installed():
    """Calling a native function of a plugin that isn't loaded logs an error in the game on every call"""
    paths = _scripts_or_skip()
    found = []
    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        if name == "MantellaHeardLines":
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [line.split(";")[0] for line in f.read().splitlines()]
        function_start = 0
        for number, line in enumerate(lines):
            if re.match(r"\s*(?:\w+\s+)?function\s+\w+\s*\(", line, re.IGNORECASE):
                function_start = number
            if re.search(r"\bMantellaHeardLines\.\w+\(", line, re.IGNORECASE):
                if not any(re.search(r"IsHeardLinesInstalled\(\)", earlier, re.IGNORECASE) for earlier in lines[function_start:number]):
                    found.append(f"{name}.psc:{number + 1}: {line.strip()}")
    assert not found, "MantellaHeardLines called without IsHeardLinesInstalled():\n" + "\n".join(found)


def _most_options(lines):
    """The most options AddOption can add on any one path through these lines (if / elseIf / else blocks)"""
    total = 0
    i = 0
    while i < len(lines):
        line = lines[i].lower()
        if re.match(r"\w+\s*=\s*addoption\(", line):
            total += 1
        elif line.startswith("if "):
            branches, current, depth, has_else = [], [], 1, False
            i += 1
            while depth:
                inner = lines[i].lower()
                if inner.startswith("if "):
                    depth += 1
                elif inner.startswith("endif"):
                    depth -= 1
                if depth == 1 and (inner.startswith("elseif ") or inner.startswith("else")):
                    branches.append(current)
                    current = []
                    has_else = has_else or inner.startswith("else") and not inner.startswith("elseif")
                elif depth:
                    current.append(lines[i])
                i += 1
            branches.append(current)
            if not has_else:
                branches.append([])
            total += max(_most_options(branch) for branch in branches)
            continue
        i += 1
    return total


def test_the_wheel_never_gets_more_than_8_options():
    """The wheel has 8 places. A 9th option would be written outside the actions array"""
    _scripts_or_skip()
    body = _function("MantellaWheel", "Open")
    most = _most_options(body)
    assert 0 < most <= 8, f"The wheel can get {most} options"
