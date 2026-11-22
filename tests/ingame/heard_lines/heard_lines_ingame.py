"""In-game test of Vanilla Dialogues: the game plays one of Skyrim's own scenes in the Bannered Mare under different settings, with people
placed near and far, and hands the caught lines over with its usual requests (MantellaHeardLinesTest.psc in Mantella-Spell). This script
then checks what the game sent (Mantella's log) and what Mantella kept (each NPC's heard_lines.json). See FEATURES_README.md,
Vanilla Dialogues, "For developers". In short:

    python tests/ingame/heard_lines/heard_lines_ingame.py setup --game "<Skyrim folder>"
    (restart Mantella, load a save, `bat mhl`, then `bat mhl2`, answer the two messages, press Enter here, `bat mhl3`, press Enter here)

What it covers:
  1. A scene with everyone near: the words, the scene, who it was said to, who heard it (near yes; far, disabled and dead no; never the
     player), that the player was there, and a line reported twice kept once
  2. Nearby NPCs Overhear off with the player far away: only the two speakers, and the player wasn't there
  3. Vanilla Dialogues off: nothing caught, and the hand-over still tells the game's time
  4. The scene during a conversation: it goes into the conversation as narration, the NPC in it keeps it when it ends, and Mantella's own
     lines are never caught
  5. Lines still waiting when the game is saved come back from the save, and past the most waiting lines the oldest are dropped
  6. Dialogue-menu talk the player does: the answer with the question, a line in Lines That Only Open a Menu not kept, Mantella's own menu
     line and a greeting never caught
  Each NPC's heard lines: each line once although the scene played four times (the repeat rule), in the right NPCs' memory only
  7. Loading the save from before the test: every line from after it is forgotten

Setup saves your config.ini and sets Show HTTP Debug Messages (the checks read what the game sent from Mantella's log), the silent voice
(tests/ingame/silent_voice.py, which runs until the end) and the Vanilla Dialogues settings the checks expect. The end puts your config.ini
back and removes the test's batch files and saves. If it stops halfway, "cleanup" does that; "check" checks again.
"""
import argparse
import fnmatch
import glob
import json
import os
import re
import shutil
import sys
from datetime import datetime
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src.config.definitions.other_definitions import OtherDefinitions  # noqa: E402
from src.heard_lines.heard_line import HeardPerson  # noqa: E402
from src.heard_lines.heard_lines_store import JsonHeardLinesStore  # noqa: E402
from src.http.communication_constants import communication_constants as comm_consts  # noqa: E402
from src.remember.npc_folder import NpcFolder  # noqa: E402
from tests.ingame.conversation_events.conversation_events_ingame import (  # noqa: E402
    TIME_FORMAT, Exchange, Report, default_mantella_folder, exchanges, read_json, read_log)
from tests.ingame.silent_voice import start_silent_voice, voice_settings  # noqa: E402
from tests.ingame.talk_settings.talk_settings_ingame import LOG_SETTINGS, write_settings  # noqa: E402

# The same Skyrim.esm forms as MantellaHeardLinesTest.psc
FEUD_SCENE = 0x0E3DE3  # Vignar and Olfrid argue
VIGNAR = 0x01A6A1
OLFRID = 0x01A687
HULDA = 0x01A66E  # Near the speakers
MIKAEL = 0x01A671  # Far away, except during his conversation with the player
BRENUIN = 0x02C90F  # Near the speakers but disabled
PLAYER = 0x14
SPEAKERS = {VIGNAR, OLFRID}
MOST_WAITING = 200  # The most lines the plugin keeps waiting outside part 5 (the add-on's MantellaRepository.heardLinesMostWaiting)

GREETING = "hello"  # The text of the topic the game picks itself when the dialogue menu opens
ROOM_PATTERN = "*room*"  # Added to Lines That Only Open a Menu for the test: the player asks Hulda to rent a room
MANTELLA_PICK = "start mantella conversation"  # In Mantella's own dialogue-menu line
SAVE_BEFORE = "mhl_before"
SAVE_WAITING = "mhl_waiting"
BATCHES = {
    "mhl": ["setpqv MantellaQuest heardLinesTestOnLoad 3",  # Loading the save made next goes on with run 3
            f"save {SAVE_BEFORE}",
            f"player.moveto {HULDA:X}",
            "setpqv MantellaQuest heardLinesTestRun 1"],
    "mhl2": [f"save {SAVE_WAITING}", f"load {SAVE_WAITING}"],
    "mhl3": [f"load {SAVE_BEFORE}"],
}
START_FILE = "heard_lines_test_start.txt"  # In Mantella's folder: when setup ran. The checks read the log from then on
SNAPSHOT_FILE = "heard_lines_test_snapshot.json"  # In Mantella's folder: the NPCs' heard lines after run 2, before run 3 forgets them
BACKUP_FILE = "config.ini.bak-heard-lines-test"  # In Mantella's folder: your config.ini before setup
SKYRIM_FOLDER = os.path.join(os.path.expanduser("~"), "Documents", "My Games", "Skyrim Special Edition")
PLUGIN_LOG = os.path.join(SKYRIM_FOLDER, "SKSE", "MantellaHeardLines.log")
SAVES_FOLDER = os.path.join(SKYRIM_FOLDER, "Saves")


def setup(game_folder: str, mantella_folder: str):
    config_path = os.path.join(mantella_folder, "config.ini")
    backup_path = os.path.join(mantella_folder, BACKUP_FILE)
    if os.path.exists(backup_path):
        print(f"{backup_path} already exists: run cleanup first, so your own config.ini is put back")
        sys.exit(1)
    try:
        voice = start_silent_voice()
    except OSError as e:
        print(f"Can't start the silent voice ({e}). Is another in-game test still running?")
        sys.exit(1)
    shutil.copyfile(config_path, backup_path)
    days = OtherDefinitions.get_heard_lines_days_kept_config_value()
    most = OtherDefinitions.get_heard_lines_most_per_npc_config_value()
    menu_only = OtherDefinitions.get_heard_lines_menu_only_config_value()
    missing = write_settings(config_path, {**voice_settings(), **LOG_SETTINGS, days.identifier: str(days.default_value),
                                           most.identifier: str(most.default_value),
                                           menu_only.identifier: f"{menu_only.default_value}, {ROOM_PATTERN}"})
    if missing:
        shutil.copyfile(backup_path, config_path)
        os.remove(backup_path)
        voice.shutdown()
        print(f"These settings are not in config.ini, so nothing was changed. Start Mantella once so it writes them, then run setup again: {missing}")
        sys.exit(1)
    for name, lines in BATCHES.items():
        with open(os.path.join(game_folder, f"{name}.txt"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\n".join(lines) + "\n")
    with open(os.path.join(mantella_folder, START_FILE), "w", encoding="utf-8") as f:
        f.write(datetime.now().strftime(TIME_FORMAT))
    snapshot_path = os.path.join(mantella_folder, SNAPSHOT_FILE)
    if os.path.exists(snapshot_path):
        os.remove(snapshot_path)  # From an earlier run that wasn't cleaned up
    print(f"Saved your config.ini as {BACKUP_FILE}, set the test's settings and started the silent voice. Wrote {', '.join(BATCHES)} in {game_folder}.")
    print(f"""Now:
  1. Start Mantella, or restart it if it is running, so it reads the test's settings. Use text input (microphone off).
     Don't change Mantella's settings during the test.
  2. Start the game with the MantellaHeardLines plugin and load a save where Vignar Gray-Mane, Olfrid Battle-Born, Hulda and Mikael are alive,
     not during a quest scene. Don't save during the test: its batch files save and load what it needs.
  3. Console: bat mhl (saves as {SAVE_BEFORE}, moves you to the Bannered Mare and starts run 1). Watch the notes "Heard lines test: ...":
     Vignar and Olfrid argue five times. Wait for "run 1 done".
  4. Console: bat mhl2 (saves as {SAVE_WAITING} and loads it). Run 2 starts by itself. Do what its two messages say: talk to Hulda
     (a question, renting a room, then Mantella's line), then open and close Vignar's dialogue. Wait for "run 2 done".
     To do run 2 again, load {SAVE_WAITING} from the Load menu. Never type bat mhl2 again: it would save over {SAVE_WAITING} while run 2
     is in the middle of a request, and every later load of that save sends that request broken.
  5. Press Enter here. It checks runs 1 and 2.""")
    try:
        input()
        passed = check_runs_1_and_2(mantella_folder)
        print(f"""
  6. Console: bat mhl3 (loads {SAVE_BEFORE}). Run 3 starts by itself. Wait for "done".
  7. Press Enter here. It checks that the lines from after that save were forgotten, puts your config.ini back and removes the test's
     batch files and saves. Then restart Mantella and load your own save.""")
        input()
        passed = check_run_3(mantella_folder) and passed
        print(f"\n{'All checks passed' if passed else 'Some checks FAILED'}")
    finally:
        voice.shutdown()
        cleanup(game_folder, mantella_folder)


class HandOver:
    """One request that carried the plugin's heard lines"""
    def __init__(self, position: int, exchange: Exchange, data: dict[str, Any], since_epoch: float):
        self.position: int = position  # Among all requests since setup
        self.exchange: Exchange = exchange
        self.data: dict[str, Any] = data
        self.since_epoch: float = since_epoch

    @property
    def kind(self) -> str:
        return self.exchange.kind

    @property
    def lines(self) -> list[dict[str, Any]]:
        lines = self.data.get("lines")
        return [line for line in lines if isinstance(line, dict)] if isinstance(lines, list) else []

    @property
    def feud(self) -> list[dict[str, Any]]:
        """The scene's lines the test played. Not one that played by itself before setup and waited in the save"""
        return [line for line in self.lines if line.get("scene") == FEUD_SCENE and int(line.get("time") or 0) >= self.since_epoch]

    @property
    def day(self) -> float | None:
        now = self.data.get("now")
        return float(now["day"]) if isinstance(now, dict) and isinstance(now.get("day"), (int, float)) else None


def hand_overs(all_exchanges: list[Exchange], since: datetime) -> list[HandOver]:
    result: list[HandOver] = []
    for position, exchange in enumerate(all_exchanges):
        text = exchange.request.get(comm_consts.KEY_HEARDLINES)
        if isinstance(text, str) and text.strip():
            result.append(HandOver(position, exchange, read_json(text), since.timestamp()))
    return result


def person_id(person: Any) -> int | None:
    return person.get("id") if isinstance(person, dict) else None


def heard_ids(line: dict[str, Any]) -> set[int]:
    return {person_id(p) for p in line.get("heard_by", []) if person_id(p) is not None}


def heard_names(line: dict[str, Any]) -> list[str]:
    return [str(p.get("name", "")) for p in line.get("heard_by", []) if isinstance(p, dict)]


def words_of(lines: list[dict[str, Any]]) -> list[str]:
    return [str(line.get("words", "")) for line in lines]


def other_speaker(speaker: int | None) -> int | None:
    rest = SPEAKERS - {speaker}
    return next(iter(rest)) if speaker in SPEAKERS else None


def check_scene_lines(report: Report, lines: list[dict[str, Any]], label: str):
    """What each scene line carries, whoever heard it"""
    report.check(len(lines) >= 2 and {person_id(line.get("speaker")) for line in lines} == SPEAKERS,
                 f"{label}: {len(lines)} scene lines caught, said by Vignar and Olfrid")
    report.check(all(str(line.get("words", "")).strip() for line in lines), f"{label}: every line has its words")
    report.check(all(line.get("menu") is False and line.get("asked") == "" for line in lines), f"{label}: none counted as dialogue-menu talk")
    report.check(all(person_id(line.get("to")) == other_speaker(person_id(line.get("speaker"))) for line in lines),
                 f"{label}: each line said to the other one of the two")
    report.check(all(PLAYER not in heard_ids(line) for line in lines), f"{label}: the player is never among who heard it")
    pairs = [(person_id(line.get("speaker")), line.get("words")) for line in lines]
    report.check(all(a != b for a, b in zip(pairs, pairs[1:])), f"{label}: no line caught twice in a row")
    places = sorted({str(line.get("place", "")) for line in lines})
    report.check(all(places), f"{label}: the place is known: {', '.join(places)}")


def check_scenes(report: Report, overs: list[HandOver], all_exchanges: list[Exchange]) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """Parts 1 to 5. Returns the words of one whole scene, the hand-overs' dialogue-menu lines from Hulda, and the words Mikael heard
    during his conversation"""
    print("\nEvery hand-over")
    report.check(bool(overs), f"{len(overs)} requests carried the plugin's hand-over")
    report.check(all(o.day is not None for o in overs), "each one tells the game's time, also when no lines waited")
    report.check(all(str(o.data.get("player", "")).strip() for o in overs), "each one names the player")
    full = [o for o in overs if len(o.lines) >= MOST_WAITING]
    speakers: dict[str, int] = {}
    for o in full:
        for line in o.lines:
            name = str((line.get("speaker") or {}).get("name", ""))
            speakers[name] = speakers.get(name, 0) + 1
    busiest = ", ".join(f"{name} {count}" for name, count in sorted(speakers.items(), key=lambda item: -item[1])[:3])
    report.check(not full, f"no hand-over came full ({MOST_WAITING} lines), so no line was pushed out" + (f". Most lines from: {busiest}" if full else ""))
    feud = [o for o in overs if o.feud]
    told = next((o for o in feud if o.kind == comm_consts.KEY_REQUESTTYPE_PLAYERINPUT), None)
    before = feud[:feud.index(told)] if told else feud[:2]
    after = feud[feud.index(told) + 1:] if told else feud[2:]

    print("\n1. The scene with everyone near")
    if not before:
        report.check(False, "Part 1: no scene lines handed over. Did the scene play (note \"the scene didn't start\")?")
        return [], []
    first = before[0].feud
    one_scene = words_of(first)
    check_scene_lines(report, first, "Part 1")
    report.check(all({VIGNAR, OLFRID, HULDA} <= heard_ids(line) for line in first), "Part 1: the two speakers and Hulda, standing near, heard every line")
    report.check(all(MIKAEL not in heard_ids(line) for line in first), "Part 1: Mikael, far away in the kitchen, heard none")
    report.check(all(BRENUIN not in heard_ids(line) for line in first), "Part 1: Brenuin, near but disabled, heard none")
    report.check(all(not any("chicken" in name.lower() for name in heard_names(line)) for line in first), "Part 1: the dead chicken heard none")
    report.check(all(line.get("player_there") is True for line in first), "Part 1: the player was there")
    report.info(f"Part 1: heard by {', '.join(sorted(set(n for line in first for n in heard_names(line))))}")

    print("\n2. Nearby NPCs Overhear off, the player far away")
    if len(before) < 2:
        report.check(False, "Part 2: no scene lines handed over")
    else:
        second = before[1].feud
        check_scene_lines(report, second, "Part 2")
        report.check(words_of(second) == one_scene, "Part 2: the same lines as part 1, none handed over again (taking the lines empties the plugin)")
        report.check(all(heard_ids(line) == SPEAKERS for line in second), "Part 2: only the two speakers heard it")
        report.check(all(line.get("player_there") is False for line in second), "Part 2: the player wasn't there")
    report.check(len(before) <= 2, "Part 3: Vanilla Dialogues off, so no scene lines caught")
    if len(before) >= 2 and told:
        between = [o for o in overs if before[1].position < o.position < told.position]
        report.check(bool(between) and all(o.day is not None and not o.feud for o in between),
                     "Part 3: the hand-overs while it was off still came, with the game's time and no scene lines")

    print("\n4. The scene during the player's conversation with Mikael")
    mikael_heard: list[str] = []
    if not told:
        report.check(False, "Part 4: no scene lines came with the player's line. Did the conversation with Mikael start?")
    else:
        lines = told.feud
        check_scene_lines(report, lines, "Part 4")
        # During the conversation the speakers sometimes walk a few steps while the scene plays, although placed and told to stay. So
        # what goes into the conversation is checked against who heard each line, and Mikael must have heard most of it
        heard = [line for line in lines if MIKAEL in heard_ids(line)]
        missed = [line for line in lines if MIKAEL not in heard_ids(line)]
        mikael_heard = words_of(heard)
        if missed:
            report.info(f"Part 4: someone walked off while the scene played, so Mikael didn't hear: {[w[:40] for w in words_of(missed)]}")
        report.check(len(heard) >= 2, f"Part 4: Mikael, now standing near, heard {len(heard)} of the {len(lines)} lines")
        # Other lines caught meanwhile, which Mikael didn't hear, are kept outside the conversation
        arrived = re.search(r"(\d+) heard lines arrived\. (\d+) go into the conversation", told.exchange.log_text)
        report.check(arrived is not None and int(arrived.group(2)) >= len(heard),
                     f"Part 4: Mantella put the lines Mikael heard into the conversation ({arrived.group(0) if arrived else 'no log line'})")
        conversation_log = "\n".join(x.log_text for x in all_exchanges[told.position:])
        heading = conversation_log.find("Heard nearby since previous exchange")
        narration = conversation_log[heading:heading + 4000] if heading >= 0 else ""
        report.check(heading >= 0 and bool(heard) and all(f'"{words}"' in narration for words in mikael_heard)
                     and not any(f'"{words}"' in narration for words in words_of(missed)),
                     "Part 4: the lines Mikael heard, and only those, went with the player's line as narration, under "
                     "\"Heard nearby since previous exchange\"")
    mantella_lines = [line for o in overs for line in o.lines
                      if person_id(line.get("speaker")) in (MIKAEL, HULDA) and not line.get("menu") and not line.get("scene")]
    report.check(not mantella_lines, "Mantella's own lines (Mikael and Hulda answering through Mantella) were never caught")

    print("\n5. Lines waiting in the save")
    if not after:
        report.check(False, "Part 5: no scene lines came after loading the save. Did bat mhl2 save and load it?")
    else:
        kept = after[0].feud
        report.check(words_of(kept) == one_scene[-2:], f"Part 5: the last 2 of the scene's {len(one_scene)} lines came back from the save, the oldest dropped "
                     "past the most waiting lines (2)")
        report.check(all(line.get("player_there") is True and HULDA in heard_ids(line) for line in kept),
                     "Part 5: who heard them was kept in the save")
    report.check(len(after) <= 1, "Part 5: the scene's lines came once")
    menu = [line for o in overs if not told or o.position > told.position for line in o.lines if line.get("menu")]
    return one_scene, menu, mikael_heard


def check_menu_talk(report: Report, menu: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Part 6. Returns Hulda's answers"""
    print("\n6. Dialogue-menu talk")
    hulda = [line for line in menu if person_id(line.get("speaker")) == HULDA]
    asked = [str(line.get("asked", "")) for line in hulda]
    report.info(f"Hulda was asked: {asked}")
    report.check(len(hulda) >= 2, f"Hulda's {len(hulda)} answers caught")
    if not hulda:
        return []
    report.check(all(a.strip() for a in asked), "each answer has the player's question (and her greeting isn't kept)")
    # Read too early, the question is the pick before: the greeting for the first answer, the first question for the room's answer
    report.check(not any(a.strip().lower() == GREETING for a in asked), f"no answer is paired with the greeting (\"{GREETING}\"), so each has its own question")
    report.check(any(fnmatch.fnmatch(a.lower(), ROOM_PATTERN) for a in asked) and any(not fnmatch.fnmatch(a.lower(), ROOM_PATTERN) for a in asked),
                 "a question and the room both caught")
    report.check(not any(MANTELLA_PICK in a.lower() for a in asked), "her answer to Mantella's own menu line not caught")
    report.check(all(person_id(line.get("to")) == PLAYER and not line.get("scene") for line in hulda), "said to the player, in no scene")
    report.check(all(HULDA in heard_ids(line) and PLAYER not in heard_ids(line) for line in hulda), "Hulda keeps them, the player is never among who heard")
    report.check(all({VIGNAR, OLFRID} <= heard_ids(line) for line in hulda), "Vignar and Olfrid, standing near, overheard them")
    report.check(not any(person_id(line.get("speaker")) == VIGNAR for line in menu), "Vignar's greeting when his dialogue opened not caught")
    return hulda


def world_folder(mantella_folder: str, since: datetime) -> str | None:
    """The playthrough's folder: the one whose latest game time Mantella wrote during the test"""
    paths = glob.glob(os.path.join(mantella_folder, "data", "*", "conversations", "*", JsonHeardLinesStore.NOW_FILE_NAME))
    recent = [p for p in paths if datetime.fromtimestamp(os.path.getmtime(p)) >= since]
    return os.path.dirname(max(recent, key=os.path.getmtime)) if recent else None


def npc_lines(world: str, names: dict[int, str], form_id: int) -> list[dict[str, Any]]:
    """What the NPC keeps in heard_lines.json, as stored"""
    ref_id = HeardPerson.from_game({"id": form_id, "name": "x"}).ref_id
    conversations, world_id = os.path.dirname(world), os.path.basename(world)
    if form_id in names:
        folder = NpcFolder(conversations).path(world_id, names[form_id], ref_id)
    else:
        found = glob.glob(os.path.join(world, f"* - {ref_id}"))
        folder = found[0] if found else ""
    path = os.path.join(folder, JsonHeardLinesStore.FILE_NAME)
    if not folder or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [line for line in data.get("lines", []) if isinstance(line, dict)]


def names_seen(overs: list[HandOver]) -> dict[int, str]:
    names: dict[int, str] = {}
    for o in overs:
        for line in o.lines:
            for person in [line.get("speaker"), line.get("to"), *line.get("heard_by", [])]:
                if isinstance(person, dict) and isinstance(person.get("id"), int) and person.get("name"):
                    names[person["id"]] = str(person["name"])
    return names


def stored_ref(form_id: int) -> str:
    return HeardPerson.from_game({"id": form_id, "name": "x"}).ref_id


def scene_words(lines: list[dict[str, Any]], since_epoch: float) -> list[str]:
    """The stored scene lines from the test: said by Vignar or Olfrid since setup"""
    speakers = {stored_ref(VIGNAR), stored_ref(OLFRID)}
    return [str(line.get("words")) for line in lines if line.get("speaker", {}).get("ref_id") in speakers and int(line.get("time") or 0) >= since_epoch]


def check_memory(report: Report, kept: dict[int, list[dict[str, Any]]], one_scene: list[str], hulda_menu: list[dict[str, Any]], since_epoch: float,
                 mikael_heard: list[str]):
    print("\nWhat each NPC keeps (heard_lines.json)")
    if not one_scene:
        report.check(False, "no scene lines from part 1 to compare with")
        return
    for form_id, name in ((VIGNAR, "Vignar"), (OLFRID, "Olfrid"), (HULDA, "Hulda")):
        words = scene_words(kept.get(form_id, []), since_epoch)
        report.check(sorted(words) == sorted(set(one_scene)) and len(words) == len(set(words)),
                     f"{name} keeps each of the scene's lines once, although it played four times ({len(words)} kept)")
    report.check(bool(mikael_heard) and set(mikael_heard) <= set(scene_words(kept.get(MIKAEL, []), since_epoch)),
                 "Mikael keeps the lines he heard during his conversation, kept when it ended")
    report.check(not scene_words(kept.get(BRENUIN, []), since_epoch), "Brenuin, disabled while it played, keeps none of it")
    asked = {str(line.get("asked", "")) for line in kept.get(HULDA, []) if line.get("menu")}
    sent = [str(line.get("asked", "")) for line in hulda_menu]
    question = [a for a in sent if not fnmatch.fnmatch(a.lower(), ROOM_PATTERN) and a.strip().lower() != GREETING]
    report.check(bool(question) and question[0] in asked, "Hulda keeps her answer to the player's question")
    room_sent = any(fnmatch.fnmatch(a.lower(), ROOM_PATTERN) for a in sent)  # Else there was no room line to leave out
    report.check(room_sent and not any(fnmatch.fnmatch(a.lower(), ROOM_PATTERN) for a in asked),
                 "Hulda doesn't keep the room line (Lines That Only Open a Menu)")
    overheard = [line for line in kept.get(VIGNAR, []) if line.get("menu")]
    report.info(f"Vignar keeps {len(overheard)} dialogue-menu lines he overheard")


def check_plugin_log(report: Report):
    print("\nThe plugin's log")
    if not os.path.exists(PLUGIN_LOG):
        report.check(False, f"{PLUGIN_LOG} not found. Is the MantellaHeardLines plugin installed?")
        return
    with open(PLUGIN_LOG, encoding="utf-8", errors="replace") as f:
        text = f.read()
    report.check("Listening for spoken lines" in text, "the plugin listens for spoken lines")
    report.check(re.search(r"Settings: catch lines true, nearby NPCs overhear false, hearing distance 355", text) is not None,
                 "the test's settings reached it (Nearby NPCs Overhear off, Hearing Distance 5 m in game units)")
    report.check("most waiting lines 2" in text, "the most waiting lines (2) reached it")
    report.check("Settings refused" not in text, "no settings refused")
    report.check("Loaded 2 waiting lines from the save" in text, "it loaded the 2 waiting lines from the save")


def read_start(mantella_folder: str) -> datetime | None:
    path = os.path.join(mantella_folder, START_FILE)
    if not os.path.exists(path):
        print(f"Run setup first: {path} not found")
        return None
    with open(path, encoding="utf-8") as f:
        return datetime.strptime(f.read().strip(), TIME_FORMAT)


def check_runs_1_and_2(mantella_folder: str) -> bool:
    since = read_start(mantella_folder)
    if since is None:
        return False
    entries = read_log(mantella_folder, since)
    all_exchanges = exchanges(entries)
    if not all_exchanges:
        print(f"No requests from the game in Mantella's log since {since}. Did Mantella restart after setup, so it read the test's settings "
              "(Show HTTP Debug Messages on)? Run the test again")
        return False
    report = Report()
    overs = hand_overs(all_exchanges, since)
    one_scene, menu, mikael_heard = check_scenes(report, overs, all_exchanges)
    hulda_menu = check_menu_talk(report, menu)
    # Run 3 forgets these lines, so the first check keeps a copy, and a later "check" reads that
    snapshot_path = os.path.join(mantella_folder, SNAPSHOT_FILE)
    world = world_folder(mantella_folder, since)
    kept: dict[int, list[dict[str, Any]]] = {}
    if os.path.exists(snapshot_path):
        with open(snapshot_path, encoding="utf-8") as f:
            kept = {int(k): v for k, v in json.load(f).items()}
    elif world:
        names = names_seen(overs)
        kept = {form_id: npc_lines(world, names, form_id) for form_id in (VIGNAR, OLFRID, HULDA, MIKAEL, BRENUIN)}
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in kept.items()}, f)
    report.check(bool(kept), "Mantella wrote the NPCs' heard lines during the test")
    check_memory(report, kept, one_scene, hulda_menu, since.timestamp(), mikael_heard)
    check_plugin_log(report)
    print_errors(entries)
    print(f"\nRuns 1 and 2: {'all checks passed' if report.passed else 'some checks FAILED'}")
    return report.passed


def check_run_3(mantella_folder: str) -> bool:
    print("\n7. Loading the save from before the test")
    since = read_start(mantella_folder)
    if since is None:
        return False
    report = Report()
    entries = read_log(mantella_folder, since)
    overs = hand_overs(exchanges(entries), since)
    forgot = [re.search(r"an older save was loaded, so (\d+) heard lines", text) for _, text in entries]
    forgotten = [int(m.group(1)) for m in forgot if m]
    report.check(bool(forgotten) and forgotten[-1] > 0, f"Mantella saw the older save and forgot {forgotten[-1] if forgotten else 0} lines from after it")
    days = [o.day for o in overs if o.day is not None]
    report.check(any(day < max(days[:i]) for i, day in enumerate(days) if i), "a hand-over came with a game time earlier than the test's")
    world = world_folder(mantella_folder, since)
    if not world:
        report.check(False, "no NPC heard lines found")
    else:
        names = names_seen(overs)
        hulda_asked = {str(line.get("asked")) for o in overs for line in o.lines if line.get("menu") and person_id(line.get("speaker")) == HULDA}
        for form_id, name in ((VIGNAR, "Vignar"), (OLFRID, "Olfrid"), (HULDA, "Hulda"), (MIKAEL, "Mikael")):
            lines = npc_lines(world, names, form_id)
            left = scene_words(lines, since.timestamp()) + [str(line.get("asked")) for line in lines
                                                            if line.get("menu") and str(line.get("asked")) in hulda_asked and int(line.get("time") or 0) >= since.timestamp()]
            report.check(not left, f"{name} kept none of the test's lines" + (f", but still has: {left}" if left else ""))
    print(f"\nRun 3: {'all checks passed' if report.passed else 'some checks FAILED'}")
    return report.passed


def print_errors(entries: list[tuple[str, str]]):
    errors = [text.splitlines()[0] for level, text in entries if level == "ERROR"]
    if errors:
        print(f"\n{len(errors)} errors in Mantella's log during the test (may be unrelated):")
        for error in errors[:10]:
            print(f"  {error}")


def cleanup(game_folder: str | None, mantella_folder: str):
    backup_path = os.path.join(mantella_folder, BACKUP_FILE)
    if os.path.exists(backup_path):
        shutil.copyfile(backup_path, os.path.join(mantella_folder, "config.ini"))
        os.remove(backup_path)
        print("Put your config.ini back. Restart Mantella so it reads it.")
    paths = [os.path.join(mantella_folder, START_FILE), os.path.join(mantella_folder, SNAPSHOT_FILE)]
    if game_folder:
        paths += [os.path.join(game_folder, f"{name}.txt") for name in BATCHES]
    # The game keeps the save it overwrote as .ess.bak
    saves = [os.path.join(SAVES_FOLDER, f"{save}.{ext}") for save in (SAVE_BEFORE, SAVE_WAITING) for ext in ("ess", "skse", "ess.bak")]
    removed_saves = False
    for path in paths + saves:
        if os.path.exists(path):
            os.remove(path)
            removed_saves = removed_saves or path in saves
            print(f"Removed {path}")
    if not removed_saves:
        print(f"The test's saves weren't in {SAVES_FOLDER} (Mod Organizer can keep saves per profile). If the test made them, delete "
              f"{SAVE_BEFORE} and {SAVE_WAITING} yourself")


def main():
    parser = argparse.ArgumentParser(description="In-game test of Vanilla Dialogues")
    parser.add_argument("command", choices=["setup", "check", "cleanup"])
    parser.add_argument("--game", help="Skyrim's folder, where the console reads batch files (setup, cleanup)")
    parser.add_argument("--mantella-folder", default=default_mantella_folder(), help="Mantella's user folder (default: Documents/My Games/Mantella)")
    args = parser.parse_args()
    if args.command == "setup":
        if not args.game:
            parser.error("setup needs --game")
        setup(args.game, args.mantella_folder)
    elif args.command == "check":
        passed = check_runs_1_and_2(args.mantella_folder)
        passed = check_run_3(args.mantella_folder) and passed
        sys.exit(0 if passed else 1)
    else:
        cleanup(args.game, args.mantella_folder)


if __name__ == "__main__":
    main()
