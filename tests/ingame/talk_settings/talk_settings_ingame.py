"""In-game test of the talk settings (Conversation page): the game runs a group conversation, a Conversation Event, a Shout and a watched NPC Event
by itself (MantellaTalkTest.psc in Mantella-Spell), saying the player's lines itself, then this script checks Mantella's log.
It runs twice: once as it is, and once in Director Mode (setup --director), as only Mantella's settings can switch Director Mode on.

See docs/conversation_wheel.md, "Automated tests", for the steps. In short, for each of the two runs:

    python tests/ingame/talk_settings/talk_settings_ingame.py setup --game "<Skyrim folder>"   (the second time: add --director)
    (start or restart Mantella. Load a save, `bat mts`, watch until "Talk test: done", then press Enter in setup's window)

Setup saves your config.ini and writes the test's talk settings into it. The NPCs' lines are voiced with a moment of silence
(tests/ingame/silent_voice.py), which setup runs until you press Enter, so the lines take no time to voice and say. Then it checks
Mantella's log and puts your config.ini back. If it stops halfway, "cleanup" puts your config.ini back; "check" checks again.
After each NPC reply, Mantella logs a "Turn state:" line (GameMasterTurns): who spoke, who speaks next, whose talk settings apply,
who still has to speak, who is due to answer first, and the extra rounds after the reply limit. Who the models pick changes from run
to run, so it is shown as INFO. What Mantella does with their picks follows from the settings, so it is checked: PASS or FAIL.
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from tests.ingame.silent_voice import start_silent_voice, voice_settings  # noqa: E402
from src.logs.log_detail import LogDetail  # noqa: E402
from src.logs.log_topic import LogTopic  # noqa: E402

BATCH = "mts"
BATCH_LINES = [
    "set gamehour to 20",  # Evening: the Bannered Mare is busy
    "player.moveto 1A66E",  # Next to Hulda in the Bannered Mare
    "setpqv MantellaQuest talkTestRun 1",  # MantellaListenerScript starts MantellaTalkTest on its next tick
]
START_FILE = "talk_test_start.txt"  # In Mantella's folder: when setup ran. The check reads the log from then on
BACKUP_FILE = "config.ini.bak-talk-test"  # In Mantella's folder: your config.ini before setup
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# A line of logging.log: "<date> <time> [<topic>] <WARNING/ERROR/CRITICAL: ><text>"
ENTRY = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (?:\[([^\]\n]+)\] )?(?:(WARNING|ERROR|CRITICAL): )?", re.MULTILINE)
FROM_GAME = "Received from the game: "  # Game connection at Full: this line, then the whole message
TO_GAME = "Sent to the game: "
# The checks read Mantella's log, so every topic shows in full. Not the timings: they would fill the log
LOG_SETTINGS = {topic.setting_id: (LogDetail.OFF if topic == LogTopic.TIMINGS else LogDetail.FULL).value for topic in LogTopic}
TURN_STATE = "Turn state: "
STARTED = "Mantella.exe running in:"  # What Mantella logs when it starts (src/setup.py), also when run from the source

# The settings the test runs with. The group conversation uses the shared set, the Conversation Event, the Shout and the watched NPC Event
# their own. Each kind has a different Max NPC Turns, and between them they use all three choices of "who still speaks"
TEST_SETTINGS = {
    "multi_npc_game_master_mode": "True",
    "multi_conversation_director_mode": "False",
    "group_conversation_max_npc_turns": "3",
    "shout_max_npc_turns": "4",
    "conversation_event_max_npc_turns": "1",  # Below the two people the event is about, so someone is still waiting at the limit
    "who_must_speak": "Anyone you talk to",
    "when_you_talk_to_someone": "Each answers in turn",
    "when_an_npc_talks_to_someone": "They answer next",
    "who_still_speaks_at_reply_limit": "Only people who haven't answered you yet",
    "replies_in_between": "0",
    "extra_rounds": "20",
    "group_conversation_use_own_talk_settings": "False",
    "shout_use_own_talk_settings": "True",
    "shout_who_must_speak": "Anyone you talk to, Anyone an NPC talks to",
    "shout_when_you_talk_to_someone": "They answer next",
    "shout_when_an_npc_talks_to_someone": "They answer next",
    "shout_who_still_speaks_at_reply_limit": "Only people who haven't answered you yet",
    "shout_replies_in_between": "1",
    "shout_extra_rounds": "1",
    "conversation_event_use_own_talk_settings": "True",
    "conversation_event_who_must_speak": "Anyone you talk to",
    "conversation_event_when_you_talk_to_someone": "They answer next",
    "conversation_event_when_an_npc_talks_to_someone": "They answer next",
    "conversation_event_who_still_speaks_at_reply_limit": "Nobody, it's your turn now",
    "conversation_event_replies_in_between": "0",
    "conversation_event_extra_rounds": "1",
    "director_use_own_talk_settings": "False",
    "npc_event_use_own_talk_settings": "True",
    "npc_event_who_must_speak": "Anyone an NPC talks to",
    "npc_event_when_an_npc_talks_to_someone": "The Game Master decides",
    "npc_event_who_still_speaks_at_reply_limit": "Everyone who still has to speak",
    "npc_event_replies_in_between": "1",
    "npc_event_extra_rounds": "20",
    "npc_event_max_lines_without_player": "4",
}
# The second run, in Director Mode: the group conversation and the Shout are both Director Mode, with its own settings. The Conversation Event
# still uses its own Max NPC Turns, not Max NPC Turns per Direction
DIRECTOR_SETTINGS = {
    "multi_conversation_director_mode": "True",
    "multi_npc_game_master_director_max_npc_turns": "5",
    "director_use_own_talk_settings": "True",
    "director_who_must_speak": "Anyone you talk to",
    "director_when_you_talk_to_someone": "Each answers in turn",
    "director_when_an_npc_talks_to_someone": "They answer next",
    "director_who_still_speaks_at_reply_limit": "Everyone who still has to speak",
    "director_replies_in_between": "1",
    "director_extra_rounds": "2",
}
NORMAL, DIRECTOR = "normal", "director"
# The kinds of conversation each run plays, as the Turn state lines name them
PLAYED = {NORMAL: ["group_conversation", "conversation_event", "shout", "npc_event"], DIRECTOR: ["director", "conversation_event", "npc_event"]}
# The setting that is each kind's limit of NPC replies in a row
LIMIT_SETTING = {"group_conversation": "group_conversation_max_npc_turns", "shout": "shout_max_npc_turns",
                 "conversation_event": "conversation_event_max_npc_turns", "director": "multi_npc_game_master_director_max_npc_turns",
                 "npc_event": "npc_event_max_lines_without_player"}
NOBODY_AT_LIMIT = "Nobody, it's your turn now"


def settings_for(test_run: str) -> dict[str, str]:
    return {**TEST_SETTINGS, **(DIRECTOR_SETTINGS if test_run == DIRECTOR else {})}


def expectations(test_run: str) -> dict[str, dict[str, Any]]:
    """For each kind of conversation the run plays: whose talk settings it must use ("shared" or the kind), its limit of NPC replies in a row,
    and whether "Nobody, it's your turn now" ends its turn at the limit"""
    settings = settings_for(test_run)
    result = {}
    for situation in PLAYED[test_run]:
        own = settings.get(f"{situation}_use_own_talk_settings") == "True"
        at_limit = settings[f"{situation}_who_still_speaks_at_reply_limit" if own else "who_still_speaks_at_reply_limit"]
        result[situation] = {"settings": situation if own else "shared", "reply_limit": int(settings[LIMIT_SETTING[situation]]),
                             "ends_at_limit": at_limit == NOBODY_AT_LIMIT}
    return result


def default_mantella_folder() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents", "My Games", "Mantella")


def write_settings(config_path: str, values: dict[str, str]) -> list[str]:
    """Sets these settings in config.ini, keeping everything else and the file's line endings. Returns the ones it didn't find"""
    with open(config_path, "rb") as f:
        data = f.read()
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    lines = data.split(newline)
    found = set()
    for i, line in enumerate(lines):
        if line.startswith(b";") or b" = " not in line:
            continue
        key = line.split(b" = ", 1)[0].decode("utf-8", "replace")
        if key in values:
            lines[i] = f"{key} = {values[key]}".encode("utf-8")
            found.add(key)
    with open(config_path, "wb") as f:
        f.write(newline.join(lines))
    return sorted(set(values) - found)


def setup(game_folder: str, mantella_folder: str, test_run: str = NORMAL):
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
    missing = write_settings(config_path, {**settings_for(test_run), **voice_settings(), **LOG_SETTINGS})
    if missing:
        shutil.copyfile(backup_path, config_path)
        os.remove(backup_path)
        print(f"These settings are not in config.ini, so nothing was changed. Start Mantella once so it writes them, then run setup again: {missing}")
        sys.exit(1)
    with open(os.path.join(game_folder, f"{BATCH}.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(BATCH_LINES) + "\n")
    with open(os.path.join(mantella_folder, START_FILE), "w", encoding="utf-8") as f:
        f.write(datetime.now().strftime(TIME_FORMAT) + "\n" + test_run) # The check reads which run it was
    print(f"Saved your config.ini as {BACKUP_FILE} and wrote the test's talk settings{' (Director Mode on)' if test_run == DIRECTOR else ''} "
          f"and the silent voice, which is now running. Wrote {BATCH}.txt in {game_folder}.")
    print(f"""Now:
  1. Start Mantella, or restart it if it is running, so it reads the test's settings. Use text input (microphone off).
  2. Load a save where you can go to Whiterun (not during a quest scene). Don't save during or after the test.
  3. Console: bat {BATCH} (moves you to the Bannered Mare at 8 pm and starts the test)
  4. Watch the notes "Talk test: ...". It says your lines itself. The NPCs' lines are silent, only their subtitles flash.
     Wait until "Talk test: done".
  5. Press Enter here. It checks the results and puts your config.ini back. Then restart Mantella.""")
    try:
        input()
        check(mantella_folder)
    finally:
        voice.shutdown()
        cleanup(game_folder, mantella_folder)
    if test_run == NORMAL:
        print(f"Then the same again in Director Mode: python {os.path.relpath(__file__)} setup --game \"{game_folder}\" --director")


PAPYRUS_LOG = os.path.join(os.path.expanduser("~"), "Documents", "My Games", "Skyrim Special Edition", "Logs", "Script", "Papyrus.0.log")


def game_npc_event_notes(test_name: str, papyrus_log: str = PAPYRUS_LOG) -> list[str]:
    """What the game noted about NPC Events in its latest run of the test ("Talk test" or "Event test"), e.g. why the scene didn't start.
    Empty without Papyrus logging (bEnableLogging=1 and bEnableTrace=1 in Skyrim.ini)"""
    if not os.path.exists(papyrus_log):
        return []
    with open(papyrus_log, encoding="utf-8", errors="replace") as f:
        lines = [line.split("] ", 1)[-1].strip() for line in f]
    starts = [i for i, line in enumerate(lines) if line.lower() == f"{test_name}: starting".lower()]
    if not starts:
        return []
    wanted = ("mantella npc event:", f"{test_name}: no npc event".lower(), f"{test_name}: npc event".lower())
    return [line for line in lines[starts[-1]:] if line.lower().startswith(wanted)]


def log_entry(match: re.Match, text: str) -> tuple[str, str]:
    """(level, text) of one log entry: WARNING / ERROR / CRITICAL for problems, HTTP-in / HTTP-out with the whole message for the
    messages between the game and Mantella, otherwise the line's topic"""
    if match.group(3):
        return match.group(3), text
    if text.startswith(FROM_GAME):
        return "HTTP-in", text.partition("\n")[2]
    if text.startswith(TO_GAME):
        return "HTTP-out", text.partition("\n")[2]
    return match.group(2) or "", text


def read_log(mantella_folder: str, since: datetime) -> list[tuple[str, str]]:
    """(level, text) of every log entry since then. The older file first, if the log was rotated during the test"""
    entries: list[tuple[str, str]] = []
    for name in ("logging.log.1", "logging.log"):
        path = os.path.join(mantella_folder, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        starts = list(ENTRY.finditer(text))
        for i, match in enumerate(starts):
            if datetime.strptime(match.group(1), TIME_FORMAT) >= since:
                end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
                entries.append(log_entry(match, text[match.end():end].strip()))
    return entries


def turn_states(entries: list[tuple[str, str]]) -> list[dict[str, Any]]:
    states = []
    for _level, text in entries:
        if text.startswith(TURN_STATE):
            try:
                states.append(json.loads(text[len(TURN_STATE):].splitlines()[0]))
            except json.JSONDecodeError:
                pass
    return states


class Report:
    def __init__(self):
        self.passed = True

    def check(self, ok: bool, text: str):
        self.passed &= ok
        print(f"PASS {text}" if ok else f"FAIL {text}")

    def info(self, text: str):
        print(f"INFO {text}")


def turns(states: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The states of each turn of NPC replies: a turn ends when nobody speaks next (the player's turn, or the scene ends). A turn the conversation
    ended in the middle of (e.g. the test stopped waiting for a slow model) has no such end, so another kind of conversation also starts a new turn"""
    result: list[list[dict[str, Any]]] = [[]]
    for state in states:
        if result[-1] and state.get("situation") != result[-1][-1].get("situation"):
            result.append([])
        result[-1].append(state)
        if state.get("next") == "nobody":
            result.append([])
    return [turn for turn in result if turn]


def check_turn(report: Report, turn: list[dict[str, Any]], label: str, expected: dict[str, Any] | None = None):
    """What Mantella must do with the Game Master's picks, whatever they were

    Args:
        expected (dict[str, Any] | None): what the settings say for this kind of conversation (see expectations). None to check only the rules
            that hold whatever the settings are
    """
    report.info(f"{label}: {' > '.join(state.get('spoke', '?') for state in turn)}, then {turn[-1].get('next')}")
    first_past = next((i for i, state in enumerate(turn) if state.get("past_limit")), None)
    if expected:
        report.check(all(state.get("settings") == expected["settings"] for state in turn), f"{label}: uses the {expected['settings']} talk settings")
        limit = expected["reply_limit"]
        report.check(all(state.get("reply_limit") == limit for state in turn), f"{label}: its own limit of NPC replies in a row is used ({limit})")
        if first_past is not None:
            report.check(first_past + 1 == limit, f"{label}: the limit is reached at NPC reply {limit}, not before or after (at {first_past + 1})")
        if expected["ends_at_limit"] and first_past is not None:
            report.check(turn[first_past].get("next") == "nobody", f"{label}: \"{NOBODY_AT_LIMIT}\": nobody else speaks once the limit is reached")
    followed = [state for state in turn if state.get("answer_order") and state.get("next") not in ("nobody", "joining")]
    report.check(all(state["next"] in state["answer_order"][0] for state in followed),
                 f"{label}: whoever is due to answer first speaks next ({len(followed)} times)")
    past = [(before, after) for before, after in zip(turn, turn[1:]) if before.get("past_limit") and after.get("past_limit")]
    report.check(all(set(after.get("still_to_speak", [])) <= set(before.get("still_to_speak", [])) for before, after in past),
                 f"{label}: nobody new has to speak after the reply limit")
    report.check(all(0 <= state.get("free_picks_left", 0) <= state.get("replies_in_between", 0) for state in turn)
                 and all(state.get("round", 0) <= state.get("extra_rounds", 0) for state in turn),
                 f"{label}: at most Replies in between free picks a round, and at most Extra rounds rounds")
    last = turn[-1]
    if last.get("next") == "nobody":
        report.check(not last.get("still_to_speak"), f"{label}: the turn only ends once nobody still has to speak")
    if last.get("next") != "nobody":
        report.info(f"{label}: cut short: the conversation ended before this turn did (for example, the test stopped waiting for a slow model)")
    if first_past is not None:
        report.info(f"{label}: reached the limit, {max(state.get('round', 0) for state in turn)} extra rounds")
    else:
        report.info(f"{label}: ended before the limit, so what happens at the limit wasn't tried in this turn")


def check_entries(entries: list[tuple[str, str]], test_run: str) -> Report:
    """Checks the log entries of one run. Prints each check"""
    print(f"The {test_run} run{' (Director Mode)' if test_run == DIRECTOR else ''}")
    states = turn_states(entries)
    report = Report()
    all_turns = turns(states)
    for situation, expected in expectations(test_run).items():
        found = [turn for turn in all_turns if turn[0].get("situation") == situation]
        report.check(bool(found), f"{situation}: NPC replies in the log since setup")
        for number, turn in enumerate(found, 1):
            check_turn(report, turn, f"{situation} turn {number}", expected)
        if found and not any(state.get("past_limit") for turn in found for state in turn):
            report.info(f"{situation}: never reached its limit this run. Run the test again to try what happens at the limit")
    unexpected = sorted({turn[0].get("situation", "") for turn in all_turns} - set(PLAYED[test_run]))
    report.check(not unexpected, f"only the kinds of conversation this run plays ({', '.join(unexpected) or 'none other'})")
    return report


def check(mantella_folder: str) -> bool:
    start_path = os.path.join(mantella_folder, START_FILE)
    if not os.path.exists(start_path):
        print(f"Run setup first: {start_path} not found")
        return False
    with open(start_path, encoding="utf-8") as f:
        lines = f.read().strip().splitlines()
    since = datetime.strptime(lines[0], TIME_FORMAT)
    test_run = lines[1] if len(lines) > 1 else NORMAL
    entries = read_log(mantella_folder, since)
    if not any(text.startswith(STARTED) for _level, text in entries):
        print(f"Mantella didn't start after setup ({since}), so it ran with the settings it had before, not the test's. "
              f"Mantella only reads config.ini when it starts. Run cleanup, then setup again, restart Mantella, and run the test again.")
        return False
    if not turn_states(entries):
        print(f"No Turn state lines in Mantella's log since {since}. Did Mantella restart after setup, and did the test run (\"Talk test: done\")?")
        return False
    report = check_entries(entries, test_run)
    spoken_to = [text for _level, text in entries if text.startswith("Game Master: the latest line was spoken to ")]
    for text in spoken_to[:20]:
        report.info(text)
    for text in game_npc_event_notes("Talk test"):
        report.info(f"game: {text}")
    scene_ended = any(text.startswith("NPC Event: ") and "The scene ends." in text for _level, text in entries)
    if any(state.get("situation") == "npc_event" for state in turn_states(entries)):
        report.check(scene_ended, "npc_event: the watched scene ends instead of giving the player the turn")
    errors = [text.splitlines()[0] for level, text in entries if level == "ERROR"]
    if errors:
        print(f"\n{len(errors)} errors in Mantella's log during the test (may be unrelated):")
        for error in errors[:10]:
            print(f"  {error}")
    print(f"\n{'All checks passed' if report.passed else 'Some checks FAILED'}")
    return report.passed


def cleanup(game_folder: str | None, mantella_folder: str):
    backup_path = os.path.join(mantella_folder, BACKUP_FILE)
    if os.path.exists(backup_path):
        shutil.copyfile(backup_path, os.path.join(mantella_folder, "config.ini"))
        os.remove(backup_path)
        print("Put your config.ini back. Restart Mantella so it reads it.")
    paths = [os.path.join(mantella_folder, START_FILE)]
    if game_folder:
        paths.append(os.path.join(game_folder, f"{BATCH}.txt"))
    for path in paths:
        if os.path.exists(path):
            os.remove(path)
            print(f"Removed {path}")


def main():
    parser = argparse.ArgumentParser(description="In-game test of the talk settings")
    parser.add_argument("command", choices=["setup", "check", "cleanup"])
    parser.add_argument("--game", help="Skyrim's folder, where the console reads batch files (setup, cleanup)")
    parser.add_argument("--mantella-folder", default=default_mantella_folder(), help="Mantella's user folder (default: Documents/My Games/Mantella)")
    parser.add_argument("--director", action="store_true", help="setup: the second run, in Director Mode")
    args = parser.parse_args()
    if args.command == "setup":
        if not args.game:
            parser.error("setup needs --game")
        setup(args.game, args.mantella_folder, DIRECTOR if args.director else NORMAL)
    elif args.command == "check":
        sys.exit(0 if check(args.mantella_folder) else 1)
    else:
        cleanup(args.game, args.mantella_folder)


if __name__ == "__main__":
    main()
