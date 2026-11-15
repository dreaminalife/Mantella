"""In-game test of the NPC actions: the game plays a one-on-one conversation by itself (MantellaActionTest.psc in Mantella-Spell), saying the
player's lines, and checks what really happens: the NPC follows, the give and take menu opens, the NPC attacks and stops. It does this first
with the in-game menu's NPC Actions switches (Allow Follow, Allow Aggro, Allow Inventory) off, when nothing may happen, then on.

So that each action happens on cue, MantellaSoftware talks to a stand-in AI service this script runs on your PC instead of a real model.
The stand-in answers each of the player's lines with the action its cue asks for, e.g. "ACTIONTEST_follow Come with me." gets
"Follow: Right behind you." Nothing goes to a real AI service, so the test costs nothing and gives the same lines every time.
The NPCs' lines are voiced with a moment of silence (tests/ingame/silent_voice.py), so they take no time to voice and say.

    python tests/ingame/actions/actions_ingame.py run --game "<Skyrim folder>"

then follow the steps it prints. It saves your config.ini and points every model at the stand-in, then waits. When the game notes
"Action test: done", press Enter: it reads the game's results from Papyrus.0.log, prints PASS or FAIL for each check, and puts your
config.ini back. If it stops halfway, "cleanup" puts your config.ini back. Papyrus logging has to be on (bEnableLogging=1 and
bEnableTrace=1 in the [Papyrus] section of Skyrim.ini, and of SkyrimCustom.ini if it has one, as that wins; with Mod Organizer, the
profile's).
"""
import argparse
import json
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from tests.ingame.silent_voice import start_silent_voice, voice_settings # noqa: E402
from tests.ingame.talk_settings.talk_settings_ingame import STARTED, TIME_FORMAT, default_mantella_folder, read_log, write_settings # noqa: E402

BATCH = "mta"
BATCH_LINES = [
    "set gamehour to 20",  # Evening: the Bannered Mare is busy
    "player.moveto 1A66E",  # Next to Hulda in the Bannered Mare
    "setpqv MantellaQuest actionTestRun 1",  # MantellaListenerScript starts MantellaActionTest on its next tick
]
START_FILE = "action_test_start.txt"  # In Mantella's folder: when the run started
BACKUP_FILE = "config.ini.bak-action-test"  # In Mantella's folder: your config.ini before the run
STAND_IN_LOG = "action_test_stand_in.log"  # In Mantella's folder: what the stand-in was asked and answered
PORT = 5123
MODEL = "stand-in"

# The stand-in's answer to a line with each cue. The action names are the keywords the test settings give the actions
# A plain word: brackets or stars could be read as narration. Any case: the game's scripts share one copy of each word whatever its case,
# so "follow" can arrive as "Follow"
CUE = re.compile(r"ACTIONTEST_([a-z]+)", re.IGNORECASE)
ANSWERS = {
    "follow": "Follow: Right behind you.",
    "inventory": "Inventory: Here, have a look.",
    "offended": "Offended: How dare you!",
    "forgiven": "Forgiven: Fine. Let's forget it.",
}
PLAIN_ANSWER = "Alright."

# The checks MantellaActionTest notes, by id. A check that never shows counts as failed: the test stopped before it
CHECKS = ["follow-off", "inventory-off", "offended-off", "follow-on", "inventory-on", "inventory-typed", "offended-on", "forgiven-on"]


def settings_for_test(port: int) -> dict[str, str]:
    url = f"http://127.0.0.1:{port}/v1"
    services = ["llm_api", "multi_npc_llm_api", "game_master_llm_api", "summary_llm_api", "thoughts_llm_api", "reflection_llm_api",
                "npc_event_llm_api", "fallback_llm_api", "bio_llm_api", "vision_llm_api"]
    models = ["model", "multi_npc_model", "game_master_model", "summary_model", "thoughts_model", "reflection_model", "npc_event_model",
              "fallback_model", "bio_llm_model", "vision_model"]
    off = ["game_master_use_decision_model", "conversation_summary_enabled", "inner_monologue_enabled", "personal_reflection_enabled",
           "random_llm_one_on_one_enabled", "random_llm_multi_npc_enabled", "random_llm_one_on_one_per_request_enabled",
           "random_llm_multi_npc_per_request_enabled", "sequential_llm_one_on_one_per_request_enabled",
           "sequential_llm_multi_npc_per_request_enabled", "sonnet_prompt_caching_enabled", "allow_per_character_llm_overrides",
           "vision_enabled", "fallback_llm_enabled", "automatic_greeting", "bio_llm_apply_profile", "apply_profile_one_on_one",
           "apply_profile_multi_npc", "apply_profile_game_master", "apply_profile_summaries", "apply_profile_thoughts",
           "apply_profile_reflection", "apply_profile_npc_event", "apply_profile_fallback"]
    return {**{key: url for key in services}, **{key: MODEL for key in models}, **{key: "False" for key in off},
            "custom_token_count": "64000", "multi_npc_custom_token_count": "64000", # A local model counts as small, so long prompts would be cut
            "active_actions": "Follow, Inventory, Offended, Forgiven",
            "follow_npc_response": "Follow", "inventory_npc_response": "Inventory",
            "offended_npc_response": "Offended", "forgiven_npc_response": "Forgiven", **voice_settings()}


# The stand-in AI service

def answer_for(request: dict[str, Any]) -> str:
    """The cue in the latest player line picks the answer. Anything else (a summary, a line without a cue) gets a plain answer"""
    user_lines = [m for m in request.get("messages", []) if m.get("role") == "user"]
    if user_lines:
        content = user_lines[-1].get("content")
        text = content if isinstance(content, str) else json.dumps(content)
        found = CUE.findall(text)
        if found:
            return ANSWERS.get(found[-1].lower(), PLAIN_ANSWER)
    return PLAIN_ANSWER


class StandIn(BaseHTTPRequestHandler):
    """Speaks enough of the OpenAI chat API for MantellaSoftware: a model list, and chat completions, streamed or not"""
    log_path = ""

    def log_message(self, format: str, *args: Any):
        pass # Quiet: the window shows the steps, the stand-in's log file what it answered

    def __send_json(self, value: dict[str, Any]):
        body = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.__send_json({"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "test"}]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            request = {}
        answer = answer_for(request)
        with open(self.log_path, "a", encoding="utf-8") as f:
            last = next((m.get("content") for m in reversed(request.get("messages", [])) if m.get("role") == "user"), "")
            f.write(f"{datetime.now().strftime(TIME_FORMAT)} asked: {str(last)[-200:]!r} answered: {answer!r}\n")
        made = int(time.time())
        if not request.get("stream"):
            self.__send_json({"id": "stand-in", "object": "chat.completion", "created": made, "model": MODEL,
                              "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for delta, finish in (({"role": "assistant", "content": answer}, None), ({}, "stop")):
            chunk = {"id": "stand-in", "object": "chat.completion.chunk", "created": made, "model": MODEL,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def start_stand_in(port: int, log_path: str) -> ThreadingHTTPServer:
    StandIn.log_path = log_path
    server = ThreadingHTTPServer(("127.0.0.1", port), StandIn)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# The game's results

def default_papyrus_log() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents", "My Games", "Skyrim Special Edition", "Logs", "Script", "Papyrus.0.log")


# Any case: the game's scripts share one copy of each word whatever its case, so "starting" can be written "Starting"
NOTE = re.compile(r"Action test: (.*)", re.IGNORECASE)


def game_notes(papyrus_log: str) -> list[str] | None:
    """The "Action test:" notes of the latest run in the game's script log, or None if it never started"""
    if not os.path.exists(papyrus_log):
        return None
    with open(papyrus_log, encoding="utf-8", errors="replace") as f:
        notes = [found.group(1).strip() for found in map(NOTE.search, f) if found]
    starts = [i for i, note in enumerate(notes) if note.lower() == "starting"]
    return notes[starts[-1]:] if starts else None


def check(mantella_folder: str, papyrus_log: str) -> bool:
    start_path = os.path.join(mantella_folder, START_FILE)
    if not os.path.exists(start_path):
        print(f"Nothing to check: the test wasn't run ({start_path} not found)")
        return False
    with open(start_path, encoding="utf-8") as f:
        since = datetime.strptime(f.read().strip(), TIME_FORMAT)
    entries = read_log(mantella_folder, since)
    if not any(text.startswith(STARTED) for _level, text in entries):
        print(f"Mantella didn't start after {since}, so it ran with your own settings, not the test's. Restart Mantella and run the test again.")
        return False
    notes = game_notes(papyrus_log)
    if notes is None:
        print(f"No \"Action test: starting\" in {papyrus_log}. Did the test run (bat {BATCH}), and is Papyrus logging on (bEnableLogging=1 and bEnableTrace=1 in Skyrim.ini and SkyrimCustom.ini)?")
        return False
    results: dict[str, str] = {}
    for note in notes:
        match = re.match(r"(PASS|FAIL) (\S+) ?(.*)", note, re.IGNORECASE)
        if match:
            check_id = match.group(2).lower()
            results[check_id] = f"{match.group(1).upper()} {check_id}: {match.group(3)}"
        elif note.lower() != "starting":
            print(f"INFO {note}")
    passed = True
    for check_id in CHECKS:
        result = results.pop(check_id, f"FAIL {check_id}: never checked (the test stopped before it)")
        passed &= result.startswith("PASS")
        print(result)
    for result in results.values(): # A setup failure
        passed &= result.startswith("PASS")
        print(result)
    log_path = os.path.join(mantella_folder, STAND_IN_LOG)
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            answers = [line.split(" answered: ", 1)[-1].strip() for line in f]
        print(f"INFO the stand-in AI service answered {len(answers)} times: {', '.join(answers[:20])}")
    errors = [text.splitlines()[0] for level, text in entries if level == "ERROR"]
    if errors:
        print(f"\n{len(errors)} errors in Mantella's log during the test (may be unrelated):")
        for error in errors[:10]:
            print(f"  {error}")
    print(f"\n{'All checks passed' if passed else 'Some checks FAILED'}")
    return passed


# Setting up and putting back

def setup(game_folder: str, mantella_folder: str, port: int):
    config_path = os.path.join(mantella_folder, "config.ini")
    backup_path = os.path.join(mantella_folder, BACKUP_FILE)
    if os.path.exists(backup_path):
        print(f"{backup_path} already exists: run cleanup first, so your own config.ini is put back")
        sys.exit(1)
    shutil.copyfile(config_path, backup_path)
    missing = write_settings(config_path, settings_for_test(port))
    if missing:
        shutil.copyfile(backup_path, config_path)
        os.remove(backup_path)
        print(f"These settings are not in config.ini, so nothing was changed. Start Mantella once so it writes them, then run again: {missing}")
        sys.exit(1)
    with open(os.path.join(game_folder, f"{BATCH}.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(BATCH_LINES) + "\n")
    with open(os.path.join(mantella_folder, START_FILE), "w", encoding="utf-8") as f:
        f.write(datetime.now().strftime(TIME_FORMAT))
    log_path = os.path.join(mantella_folder, STAND_IN_LOG)
    if os.path.exists(log_path):
        os.remove(log_path)


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


def run(game_folder: str, mantella_folder: str, papyrus_log: str, port: int) -> bool:
    try:
        server = start_stand_in(port, os.path.join(mantella_folder, STAND_IN_LOG))
    except OSError as e:
        print(f"Can't start the stand-in AI service on port {port} ({e}). Is something else using it? Try --port")
        return False
    try:
        voice = start_silent_voice()
    except OSError as e:
        server.shutdown()
        print(f"Can't start the silent voice ({e}). Is another in-game test still running?")
        return False
    setup(game_folder, mantella_folder, port)
    print(f"""Saved your config.ini as {BACKUP_FILE} and pointed every model at the stand-in AI service (port {port}) and the voice at the silent voice,
which are now running.
Now:
  1. Start Mantella, or restart it if it is running, so it reads the test's settings.
  2. Load a save where you can go to Whiterun (not during a quest scene). Don't save during or after the test.
  3. Console: bat {BATCH} (moves you next to Hulda in the Bannered Mare at 8 pm and starts the test)
  4. Watch the notes "Action test: ...". It says your lines itself. It takes a minute or two, until "Action test: done".
     One NPC attacks you briefly, then stops. The NPCs' lines are silent, only their subtitles flash.
  5. Press Enter here. It checks the results and puts your config.ini back. Then restart Mantella.""")
    try:
        input()
        time.sleep(2) # The game writes its script log a moment later
        return check(mantella_folder, papyrus_log)
    finally:
        server.shutdown()
        voice.shutdown()
        cleanup(game_folder, mantella_folder)


def main():
    parser = argparse.ArgumentParser(description="In-game test of the NPC actions")
    parser.add_argument("command", choices=["run", "check", "cleanup"])
    parser.add_argument("--game", help="Skyrim's folder, where the console reads batch files (run, cleanup)")
    parser.add_argument("--mantella-folder", default=default_mantella_folder(), help="Mantella's user folder (default: Documents/My Games/Mantella)")
    parser.add_argument("--papyrus-log", default=default_papyrus_log(), help="The game's script log (default: Documents/My Games/Skyrim Special Edition/Logs/Script/Papyrus.0.log)")
    parser.add_argument("--port", type=int, default=PORT, help=f"Where the stand-in AI service listens (default: {PORT})")
    args = parser.parse_args()
    if args.command == "run":
        if not args.game:
            parser.error("run needs --game")
        sys.exit(0 if run(args.game, args.mantella_folder, args.papyrus_log, args.port) else 1)
    elif args.command == "check":
        sys.exit(0 if check(args.mantella_folder, args.papyrus_log) else 1)
    else:
        cleanup(args.game, args.mantella_folder)


if __name__ == "__main__":
    main()
