"""The game's half of the actions (Mantella-Spell): every action MantellaSoftware can send has a script that does it, the game's menu
switches still guard the actions they did in 0.13.1, and Mantella.esp holds what those scripts need.
Mistakes here compile and load without an error, and only show in the game as an action that does nothing.
Needs the Mantella-Spell repository next to this one, or its path in MANTELLA_SPELL_PATH. Skipped without it"""
import glob
import json
import os
import re
import struct
import subprocess

import pytest
import yaml

from src.config.config_loader import ConfigLoader
from src.http.communication_constants import communication_constants as comm_consts
import src.utils as utils

SPELL_PATH = os.environ.get("MANTELLA_SPELL_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "Mantella-Spell"))
SCRIPTS = os.path.join(SPELL_PATH, "Scripts", "Source")
PLUGIN = os.path.join(SPELL_PATH, "Mantella.esp")
PLUGIN_TEXT = os.path.join(SPELL_PATH, "Spriggit_data")
UPSTREAM_SPELL = "0c5f851" # The original's add-on the action records were copied from

STRING_CONSTANT = re.compile(r'^\s*string\s+property\s+(\w+)\s*=\s*"([^"]*)"\s+auto', re.MULTILINE | re.IGNORECASE)
REGISTRATION = re.compile(r'RegisterForModEvent\(\s*EventInterface\.(EVENT_ACTIONS_PREFIX|EVENT_ADVANCED_ACTIONS_PREFIX)\s*\+\s*mConsts\.(\w+)\s*,\s*"(\w+)"\s*\)',
                          re.IGNORECASE)
PROPERTY = re.compile(r"^\s*(\w+(?:\[\])?)\s+property\s+(\w+)\s*(=[^;\n]*)?\s*auto\b([^;\n]*)", re.IGNORECASE | re.MULTILINE)
VALUE_TYPES = ("int", "bool", "float", "string") # Set by the scripts themselves, not by the plugin

LEGACY, ADVANCED = "EVENT_ACTIONS_PREFIX", "EVENT_ADVANCED_ACTIONS_PREFIX"
# The menu switches, and which action handlers check them: Offended and Forgiven (Allow Aggro), Follow, Inventory, as in 0.13.1.
# StandDown (Forgiven with arguments) only stops a fight, so it isn't guarded
SWITCH_FACTION = {"ACTION_NPC_OFFENDED": "giafac_AllowAnger", "ACTION_NPC_FORGIVEN": "giafac_AllowAnger",
                  "ACTION_NPC_FOLLOW": "giafac_AllowFollower", "ACTION_NPC_INVENTORY": "fac_AllowInventory"}
GUARDED = [(LEGACY, c) for c in SWITCH_FACTION] + [(ADVANCED, "ACTION_NPC_OFFENDED"), (ADVANCED, "ACTION_NPC_FOLLOW"), (ADVANCED, "ACTION_NPC_INVENTORY")]
# Packages the plugin writer (Mutagen, used by Spriggit) gets wrong. Their exact bytes come from the original's plugin
PACKAGES_FROM_ORIGINAL = [0x0534DF92, 0x0531B58B, 0x05339B8F, 0x05246B53] # CollectFlora, Flee, LeadTo, Loot
KNOWN_UNFILLED = {("mantellamcm", "playeralias")} # Checked for None in the script (updating from an old version)


def require_spell():
    if not os.path.isdir(SCRIPTS):
        pytest.skip(f"Mantella-Spell not found at {os.path.abspath(SPELL_PATH)}. Set MANTELLA_SPELL_PATH to its folder")


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def game_constants() -> dict[str, str]:
    require_spell()
    return dict(STRING_CONSTANT.findall(read(os.path.join(SCRIPTS, "MantellaConstants.psc"))))


def registrations() -> list[tuple[str, str, str, str]]:
    """(prefix, constant, handler, script text) for every action event a script listens for"""
    require_spell()
    found = []
    for path in glob.glob(os.path.join(SCRIPTS, "*.psc")):
        text = read(path)
        found += [(prefix.upper(), constant.upper(), handler, text) for prefix, constant, handler in REGISTRATION.findall(text)]
    return found


def handler_body(text: str, handler: str) -> str:
    match = re.search(r"^\s*event\s+" + handler + r"\s*\((.*?)^\s*endEvent", text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    assert match, f"Event {handler} is registered but not written"
    return match.group(1)


def builtin_action_identifiers() -> list[str]:
    """The actions MantellaSoftware sends without arguments that belong to Mantella itself (not another mod, like Actions For Mantella)"""
    game_values = set(game_constants().values())
    actions = ConfigLoader.load_actions_from_json(os.path.join(utils.resolve_path(), "data", "actions"))
    sent = [a.identifier for a in actions if a.is_basic] + [comm_consts.ACTION_ENDCONVERSATION, comm_consts.ACTION_REMOVECHARACTER, comm_consts.ACTION_RELOADCONVERSATION]
    return [identifier for identifier in sent if identifier in game_values]


def test_every_action_mantella_sends_without_arguments_has_a_script_that_does_it():
    constants = game_constants()
    handled = {constants.get(constant) for prefix, constant, handler, text in registrations() if prefix == LEGACY and handler_body(text, handler)}
    missing = [identifier for identifier in builtin_action_identifiers() if identifier not in handled]
    assert not missing, f"Nothing in the game does these actions: {missing}"


def test_every_action_the_game_knows_has_a_script_that_does_it():
    handled = {constant for prefix, constant, handler, text in registrations() if handler_body(text, handler)}
    missing = [name for name in game_constants() if name.startswith("ACTION_NPC_") and name not in handled]
    assert not missing, f"No script does these actions: {missing}"


@pytest.mark.parametrize("prefix,constant", GUARDED)
def test_the_menu_switches_still_guard_their_actions(prefix, constant):
    bodies = [handler_body(text, handler) for p, c, handler, text in registrations() if p == prefix and c == constant]
    assert bodies, f"No script listens for {constant}"
    faction = SWITCH_FACTION[constant].lower()
    unguarded = [body for body in bodies if f"isinfaction(repository.{faction})" not in body.lower().replace(" ", "")]
    assert not unguarded, f"{constant} happens even with its switch off in the menu"


def test_the_menu_still_has_the_three_switches():
    require_spell()
    text = read(os.path.join(SCRIPTS, "MantellaMCM_GeneralSettings.psc"))
    for label in ("Allow Follow", "Allow Aggro", "Allow Inventory"):
        assert f'AddToggleOption("{label}' in text, f"The menu has no {label} switch"


# The plugin

def plugin_records(data: bytes) -> dict[int, bytes]:
    """Every record of a plugin, by its form id"""
    records: dict[int, bytes] = {}

    def walk(start: int, end: int):
        position = start
        while position < end:
            if data[position:position + 4] == b"GRUP":
                size = struct.unpack_from("<I", data, position + 4)[0]
                walk(position + 24, position + size)
                position += size
            else:
                size = struct.unpack_from("<I", data, position + 4)[0]
                records[struct.unpack_from("<I", data, position + 12)[0]] = data[position:position + 24 + size]
                position += 24 + size

    walk(24 + struct.unpack_from("<I", data, 4)[0], len(data))
    return records


def plugin_header(data: bytes) -> tuple[list[str], int]:
    """The masters and the next free form id"""
    body = data[24:24 + struct.unpack_from("<I", data, 4)[0]]
    masters, next_id, i = [], 0, 0
    while i < len(body):
        kind, length = body[i:i + 4], struct.unpack_from("<H", body, i + 4)[0]
        if kind == b"MAST":
            masters.append(body[i + 6:i + 6 + length - 1].decode())
        elif kind == b"HEDR":
            next_id = struct.unpack_from("<I", body, i + 6 + 8)[0]
        i += 6 + length
    return masters, next_id


def plugin_bytes() -> bytes:
    require_spell()
    with open(PLUGIN, "rb") as f:
        return f.read()


def plugin_text_files() -> list[tuple[str, dict]]:
    require_spell()
    return [(path, yaml.safe_load(read(path))) for path in glob.glob(os.path.join(PLUGIN_TEXT, "**", "*.yaml"), recursive=True)]


def test_the_plugin_and_its_text_files_hold_the_same_records():
    data = plugin_bytes()
    masters, _ = plugin_header(data)
    in_plugin = {f"{form_id & 0xFFFFFF:06X}:{(masters + ['Mantella.esp'])[form_id >> 24]}" for form_id in plugin_records(data)}
    in_text = set()
    for path, _ in plugin_text_files():
        in_text |= set(re.findall(r"^\s*-?\s*FormKey: ([0-9A-F]{6}:[\w.]+)", read(path), re.MULTILINE))
    assert in_plugin == in_text, f"Only in Mantella.esp: {sorted(in_plugin - in_text)}. Only in Spriggit_data: {sorted(in_text - in_plugin)}"


def test_everything_the_plugin_points_to_is_in_it():
    defined, referenced = set(), set()
    for path, _ in plugin_text_files():
        text = read(path)
        defined |= set(re.findall(r"FormKey: ([0-9A-F]{6}):Mantella\.esp", text))
        referenced |= set(re.findall(r"\b([0-9A-F]{6}):Mantella\.esp", text))
    assert not referenced - defined, f"Mantella.esp points to records it doesn't have: {sorted(referenced - defined)}"


def test_the_next_new_record_gets_a_free_id():
    data = plugin_bytes()
    _, next_id = plugin_header(data)
    own = [form_id & 0xFFFFFF for form_id in plugin_records(data) if form_id >> 24 == len(plugin_header(data)[0])]
    assert next_id > max(own)


def scripts_in(node):
    if isinstance(node, dict):
        if isinstance(node.get("Scripts"), list):
            yield from node["Scripts"]
        for value in node.values():
            yield from scripts_in(value)
    elif isinstance(node, list):
        for value in node:
            yield from scripts_in(value)


def test_every_object_a_script_needs_is_filled_in_the_plugin():
    """A property the plugin leaves empty is None in the game: the script compiles and runs, and does nothing"""
    unfilled = []
    for path, record in plugin_text_files():
        for script in scripts_in(record):
            source = os.path.join(SCRIPTS, script["Name"] + ".psc")
            if not os.path.exists(source):
                continue # A script of the game itself
            filled = {p["Name"].lower() for p in (script.get("Properties") or [])}
            for kind, name, default, rest in PROPERTY.findall(read(source)):
                needed = kind.lower().rstrip("[]") not in VALUE_TYPES and not default and "hidden" not in rest.lower()
                if needed and name.lower() not in filled and (script["Name"].lower(), name.lower()) not in KNOWN_UNFILLED:
                    unfilled.append(f"{script['Name']}.{name} ({os.path.basename(path)})")
    assert not unfilled, f"Empty in Mantella.esp: {unfilled}"


def test_the_packages_the_plugin_writer_gets_wrong_are_the_originals_bytes():
    data = plugin_bytes()
    try:
        original = subprocess.run(["git", "-C", SPELL_PATH, "show", f"{UPSTREAM_SPELL}:Mantella.esp"], capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"The original's add-on ({UPSTREAM_SPELL}) isn't in the Mantella-Spell repository")
    ours, theirs = plugin_records(data), plugin_records(original)
    different = [f"{form_id:08X}" for form_id in PACKAGES_FROM_ORIGINAL if ours.get(form_id) != theirs.get(form_id)]
    assert not different, f"Rebuilt with Spriggit without copying these packages back from the original: {different}"


# The actions' details: what the game reads from the arguments Mantella sends with an action the model picked (Advanced Actions)

ARGUMENT_READ = re.compile(r'SKSE_HTTP\.get(\w+)\(\s*argumentsHandle\s*,\s*(?:mConsts\.(\w+)|"([^"]+)")', re.IGNORECASE)
GETTER_KIND = {"stringarray": "array", "string": "string", "int": "integer", "bool": "boolean", "float": "number", "intarray": "array"}
NEVER_SENT = {"mantella_npc_listen", "mantella_npc_vision", "mantella_end_conversation"} # The player's, a screenshot, never offered


def action_definitions() -> list[dict]:
    definitions = []
    for path in sorted(glob.glob(os.path.join(utils.resolve_path(), "data", "actions", "*.json"))):
        with open(path, encoding="utf-8") as f:
            definitions.append(json.load(f))
    return definitions


def arguments_sent(definition: dict) -> dict[str, str]:
    """The arguments Mantella sends with the action, and their kind: its parameters, and what function_manager adds once it has looked
    a name up (an animation's or an NPC's id, and whether it found it)"""
    sent = {}
    for key, parameter in definition.get("parameters", {}).items():
        sent[key] = parameter.get("type", "string")
        resolve = parameter.get("resolve_to_id")
        if resolve:
            sent[f"{key}_id"] = "integer" if resolve == "idle" else "string"
            sent[f"{key}_succeeded"] = "boolean"
    if definition["identifier"] == "mantella_npc_shareconversation":
        sent["debug_message"] = "string" # Whether it will be shared, for the notification
    return sent


def mantellas_actions() -> list[dict]:
    """The actions in data/actions that are Mantella's own (the game knows them) and that NPCs can be given"""
    game_values = set(game_constants().values())
    return [d for d in action_definitions() if d["identifier"] in game_values and d["identifier"] not in NEVER_SENT]


def test_every_action_the_model_can_pick_has_a_script_for_how_mantella_sends_it():
    """With details, the game sends the action to its advanced script. Without any (an action without parameters), to the basic one"""
    constants = game_constants()
    by_prefix = {LEGACY: set(), ADVANCED: set()}
    for prefix, constant, handler, text in registrations():
        if handler_body(text, handler):
            by_prefix[prefix].add(constants.get(constant))
    missing = [f"{d['name']} ({'with' if d.get('parameters') else 'without'} details)" for d in mantellas_actions()
               if d["identifier"] not in by_prefix[ADVANCED if d.get("parameters") else LEGACY]]
    assert not missing, f"Nothing in the game does these actions as Advanced Actions sends them: {missing}"


def test_every_detail_the_game_reads_is_one_mantella_sends():
    """A detail read under another name or as another kind (a list read as one name) is empty in the game, and the action does nothing"""
    constants = game_constants()
    definitions = {d["identifier"]: d for d in mantellas_actions()}
    wrong = []
    for prefix, constant, handler, text in registrations():
        identifier = constants.get(constant)
        if prefix != ADVANCED or identifier not in definitions:
            continue
        sent = arguments_sent(definitions[identifier])
        for getter, key_constant, key_text in ARGUMENT_READ.findall(handler_body(text, handler)):
            key = constants.get(key_constant, key_constant) if key_constant else key_text
            kind = GETTER_KIND.get(getter.lower(), getter)
            if key not in sent:
                wrong.append(f"{definitions[identifier]['name']} reads {key}, which Mantella doesn't send")
            elif sent[key] != kind:
                wrong.append(f"{definitions[identifier]['name']} reads {key} as {kind}, but Mantella sends {sent[key]}")
    assert not wrong, "; ".join(wrong)
