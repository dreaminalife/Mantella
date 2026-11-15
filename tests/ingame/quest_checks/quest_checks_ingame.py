"""In-game test of quest tag checks: the real game, Mantella-Spell and MantellaQuestChecks.dll answer a fixed set of checks.

One list of test cases below makes the test CSV and the two console batch files, and checks the answers afterwards.
See docs/quest_tags_1_user_edits.md, "Automated tests", for the steps. In short:

    python tests/ingame/quest_checks/quest_checks_ingame.py setup --game "<Skyrim folder>"
    (start Mantella, load a save, `bat mqc1`, start a conversation, `bat mqc2`, say one line, end it)
    python tests/ingame/quest_checks/quest_checks_ingame.py check
    python tests/ingame/quest_checks/quest_checks_ingame.py cleanup --game "<Skyrim folder>"

Any save where Hulda has never died: the first batch file moves the player to her and resets dunNorthwatchKeepQST.
Talk to anyone but Hulda: the second batch file kills her. Don't save afterwards.
"""
import argparse
import csv
import glob
import json
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src.quest_tags.conditions import ANSWER_MISSING, ANSWER_NOT_LOADED, ANSWER_UNREADABLE, CheckList  # noqa: E402
from src.quest_tags.tracker import ANSWERS_FILE_NAME, QuestTagTracker  # noqa: E402

TEST_CSV_NAME = "zz_quest_checks_test.csv"
BATCH_BEFORE = "mqc1"  # Run before the conversation: puts everything this test can reset into a known state
BATCH_DURING = "mqc2"  # Run during the conversation, before the player's line: changes it

ANY = "any number"
MISSING = "missing"
NOT_LOADED = "not loaded"
CANT_RUN = "can't be run"
_RESERVED = {ANSWER_MISSING: MISSING, ANSWER_NOT_LOADED: NOT_LOADED, ANSWER_UNREADABLE: CANT_RUN}

# Console lines. Hulda (ref 01A66E, base 013BA3) is in the Bannered Mare; Rorlund (ref 0198A5, base 0132A2) is in Solitude.
# dunNorthwatchKeepQST runs from the start of the game and has no script and no aliases, so setstage always works on it
# (quests the game starts on an event, like entering a place, ignore setstage until then). All IDs checked against Skyrim.esm
BEFORE_LINES = [
    "player.setav alchemy 15",
    "player.setav marksman 20",
    "player.removespell 43324",       # Candlelight (not a starting spell)
    "player.additem 1397E 1",         # Iron Dagger
    "player.unequipitem 1397E",
    "player.removefromfaction 2BF9B",  # Stormcloak faction (as in the community CSV)
    "set timescale to 20",
    "resetquest dunNorthwatchKeepQST",  # Step 100 not done
    "setpqv MQ302 CityToSwitch 0",     # A property of the peace summit's script; its variable is ::CityToSwitch_var
    "prid 1A66E",
    "enable",
    "player.moveto 1A66E",            # Last: Hulda loaded, next to the player
]
DURING_LINES = [
    "player.setav alchemy 100",
    "player.addspell 43324",
    "player.equipitem 1397E",
    "player.addtofaction 2BF9B 1",
    "set timescale to 30",
    "setstage dunNorthwatchKeepQST 100",
    "setpqv MQ302 CityToSwitch 2",
    "prid 1A66E",
    "kill",
    "disable",
]


@dataclass(frozen=True)
class Case:
    condition: str  # One check, written as in a code_N cell, without a comparison
    before: float | str  # The answer at the start of the conversation (after BATCH_BEFORE)
    after: float | str  # The answer with the player's line (after BATCH_DURING)
    covers: str
    death_count_of: str = ""  # For getdead: the base NPC whose death count Mantella asks for instead


CASES = [
    Case('"14".getav alchemy', 15, 100, "getav: base skill, set with setav"),
    Case('"14".getav archery', 20, 20, "a console skill name the game calls Marksman"),
    Case('"14".hasspell 43324', 0, 1, "a spell"),
    Case('"14".getequipped 1397E', 0, 1, "an item (any form)"),
    Case("getinfaction 2BF9B", 0, 1, "a faction, on the player when no reference is given"),
    Case("getfactionrank 2BF9B", ANY, 1, "a faction rank"),
    Case("show 3A", 20, 30, "show: a global (TimeScale)"),
    Case("getstagedone dunNorthwatchKeepQST 100", 0, 1, "a quest by editor ID, and a stage number"),
    Case("getstage dunNorthwatchKeepQST", ANY, 100, "a quest's current stage"),
    Case('"01A66E".getdead', 0, 1, "getdead -> the base NPC's death count (Hulda is in the characters CSV)", "013BA3"),
    Case('"01A66E".getdisabled', 0, 1, "a reference before the dot, no values"),
    Case('"01A66E".haskeyword 13794', 1, 1, "a keyword (ActorTypeNPC)"),
    Case('"01A66E".getrelationshiprank 14', ANY, ANY, "a reference and an NPC value (14: the player)"),
    Case('"01A66E".ishostiletoactor player', 0, ANY, "`player` as a value"),
    Case('"14".getdead', 0, 0, "getdead on the reference itself (the player)"),
    Case('"0198A5".getrelationshiprank 14', ANY, ANY, "a named NPC far away (Rorlund, in Solitude): always in memory"),
    Case('"0DD256".getdisabled', NOT_LOADED, NOT_LOADED, "a plain NPC far away (a skeleton in Solitude's catacombs)"),
    Case('"0198A5".getdead', 0, 0, "a death count works far away", "0132A2"),
    Case("GetKeywordDataForLocation 16772 2A456", ANY, ANY, "a place and a keyword"),
    Case("GetVMQuestVariable MQ302 ::CityToSwitch_var", 0, 2, "a script variable, by the variable's name"),
    Case("GetVMQuestVariable MQ302 CityToSwitch", 0, 2, "a script variable, by the property's name"),
    Case("GetVMQuestVariable MQ302 ::ImperialMajorHoldID_var", ANY, ANY, "a script variable the test never sets"),
    Case("GetVMQuestVariable MQ302 ::NoSuchVariableForMantellaTests_var", CANT_RUN, CANT_RUN, "a variable the quest's script doesn't have"),
    Case("GetVMQuestVariable dunNorthwatchKeepQST ::CityToSwitch_var", CANT_RUN, CANT_RUN, "a script variable of a quest with no script"),
    Case("GetVMQuestVariable NoSuchQuestForMantellaTests ::CityToSwitch_var", MISSING, MISSING, "a script variable of a quest no loaded file has"),
    Case("getquestcompleted NoSuchQuestForMantellaTests", MISSING, MISSING, "a quest no loaded file has"),
    Case("getinfaction NoSuchMod.esp|000800", MISSING, MISSING, "a form from a mod that isn't installed"),
    Case("getstagedone DA08", CANT_RUN, CANT_RUN, "too few values"),
    Case("getnosuchfunctionformantellatests", CANT_RUN, CANT_RUN, "not a condition function"),
    Case("getinfaction PlayerIsWerewolf", CANT_RUN, CANT_RUN, "a name where a FormID is needed"),
    Case("show 13BA3", CANT_RUN, CANT_RUN, "a FormID of the wrong kind (an NPC where a global is needed)"),
]

# Tags whose outcome changes in the test, and how the change must show up in Mantella's log
PIPELINE_TAGS = [
    ("zz_qc_alchemy", '"14".getav alchemy>=100', "(announced)"),
    ("zz_qc_stage", "getstagedone dunNorthwatchKeepQST 100=1", "(announced)"),
    ("zz_qc_hulda", '"01A66E".getdead=1', "(death, not announced)"),
]


def default_mantella_folder() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents", "My Games", "Mantella")


def setup(game_folder: str, mantella_folder: str):
    templates = os.path.join(mantella_folder, "data", "Skyrim", "bio_templates")
    os.makedirs(templates, exist_ok=True)
    with open(os.path.join(templates, TEST_CSV_NAME), "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["tag", "description", "potential_paste_1", "code_1", "announce"])
        for i, case in enumerate(CASES, start=1):
            writer.writerow([f"zz_qc_case_{i:02d}", "", f"Quest check test case {i}.", f"{case.condition}=1", "no"])
        for tag, condition, _ in PIPELINE_TAGS:
            writer.writerow([tag, "", f"Quest check test: {tag} changed.", condition, ""])
    for name, lines in ((BATCH_BEFORE, BEFORE_LINES), (BATCH_DURING, DURING_LINES)):
        with open(os.path.join(game_folder, f"{name}.txt"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\n".join(lines) + "\n")
    print(f"Wrote {os.path.join(templates, TEST_CSV_NAME)}, and {BATCH_BEFORE}.txt / {BATCH_DURING}.txt in {game_folder}.")
    print(f"""Now:
  1. Start Mantella (or click Reload Character Data if it's running).
  2. Load any save where Hulda has never died.
  3. Console: bat {BATCH_BEFORE} (moves you to Hulda; wait for the loading screen)
  4. Start a conversation with anyone but Hulda. Wait for the first NPC line.
  5. Console: bat {BATCH_DURING}
  6. Say (type) one line, wait for the answer, end the conversation.
  7. python {os.path.relpath(__file__)} check""")


def _expected_text(expected: float | str) -> str:
    return expected if isinstance(expected, str) else f"{expected:g}"


def _matches(answer: float, expected: float | str) -> bool:
    if expected == ANY:
        return answer not in _RESERVED
    if isinstance(expected, str):
        return _RESERVED.get(answer) == expected
    return answer == float(expected)


def _answer_text(answer: float | None) -> str:
    if answer is None:
        return "(not sent)"
    return _RESERVED.get(answer, f"{answer:g}")


def _latest_file(mantella_folder: str, pattern: str) -> str | None:
    files = glob.glob(os.path.join(mantella_folder, "data", "Skyrim", "conversations", "*", pattern))
    return max(files, key=os.path.getmtime) if files else None


def check(mantella_folder: str) -> bool:
    answers_path = _latest_file(mantella_folder, ANSWERS_FILE_NAME)
    if not answers_path:
        print(f"No {ANSWERS_FILE_NAME} found under {mantella_folder}. Did the conversation run with this Mantella?")
        return False
    with open(answers_path, encoding="utf-8") as f:
        moments: dict[str, list] = json.load(f).get("moments", {})
    by_moment = {moment: {tuple(row[:4]): row[4] for row in rows} for moment, rows in moments.items()}
    before = by_moment.get(QuestTagTracker.MOMENT_CATCHUP) or by_moment.get(QuestTagTracker.MOMENT_START) or {}
    after = by_moment.get(QuestTagTracker.MOMENT_PLAYER_INPUT, {})
    print(f"Answers from {answers_path}")
    if not before or not after:
        print(f"Missing answers: start/catch-up {'found' if before else 'not found'}, player line {'found' if after else 'not found'}")

    passed = True
    print(f"\n{'':4} {'condition':66} {'before':>14} {'':14} {'after':>14} {'':14}")
    for case in CASES:
        if case.death_count_of:
            key = ("getdeadcount", "", f"Skyrim.esm|{case.death_count_of}", "")
        else:
            check_key = CheckList({"case": {1: case.condition}}).checks[0]
            key = (check_key.function, check_key.reference, check_key.param1, check_key.param2)
        got_before, got_after = before.get(key), after.get(key)
        ok = got_before is not None and got_after is not None and _matches(got_before, case.before) and _matches(got_after, case.after)
        passed &= ok
        print(f"{'PASS' if ok else 'FAIL':4} {case.condition:66} {_answer_text(got_before):>14} ({_expected_text(case.before):>11}) "
              f"{_answer_text(got_after):>14} ({_expected_text(case.after):>11})   {case.covers}")

    record_path = os.path.join(os.path.dirname(answers_path), "tag_outcomes.json")
    with open(record_path, encoding="utf-8") as f:
        record: dict = json.load(f).get("tags", {})
    log_path = os.path.join(mantella_folder, "logging.log")
    log = open(log_path, encoding="utf-8", errors="replace").read() if os.path.exists(log_path) else ""
    print()
    for tag, _, log_mark in PIPELINE_TAGS:
        current = record.get(tag, {}).get("current")
        logged = f"Quest tag {tag}: outcome 0 -> 1 {log_mark}" in log
        ok = current == 1 and logged
        passed &= ok
        print(f"{'PASS' if ok else 'FAIL':4} {tag}: record says outcome {current} (expected 1); log {'has' if logged else 'lacks'} 'outcome 0 -> 1 {log_mark}'")

    print(f"\n{'All passed' if passed else 'Some checks FAILED'}")
    return passed


def cleanup(game_folder: str | None, mantella_folder: str):
    paths = [os.path.join(mantella_folder, "data", "Skyrim", "bio_templates", TEST_CSV_NAME)]
    if game_folder:
        paths += [os.path.join(game_folder, f"{name}.txt") for name in (BATCH_BEFORE, BATCH_DURING)]
    for path in paths:
        if os.path.exists(path):
            os.remove(path)
            print(f"Removed {path}")
    print("Click Reload Character Data (or restart Mantella) so the test checks leave the list.")


def main():
    parser = argparse.ArgumentParser(description="In-game test of quest tag checks")
    parser.add_argument("command", choices=["setup", "check", "cleanup"])
    parser.add_argument("--game", help="Skyrim's folder, where the console reads batch files (setup, cleanup)")
    parser.add_argument("--mantella-folder", default=default_mantella_folder(), help="Mantella's user folder (default: Documents/My Games/Mantella)")
    args = parser.parse_args()
    if args.command == "setup":
        if not args.game:
            parser.error("setup needs --game")
        setup(args.game, args.mantella_folder)
    elif args.command == "check":
        sys.exit(0 if check(args.mantella_folder) else 1)
    else:
        cleanup(args.game, args.mantella_folder)


if __name__ == "__main__":
    main()
