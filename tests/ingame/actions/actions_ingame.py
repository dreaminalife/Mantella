"""In-game test of the NPC actions: the game plays a one-on-one conversation by itself (MantellaActionTest.psc in Mantella-Spell), saying the
player's lines, and checks what really happens: the NPC follows and stops following, waits, the give and take menu and the shop open, the NPC
appears next to you (Teleport) and walks over to you (MoveTo) when you stand across the room, starts a fistfight (Brawl), runs away (Flee),
attacks and stops. It does this first with the in-game menu's NPC Actions switches (Allow Follow, Allow Aggro, Allow Inventory) off, when
nothing may happen, then on. Loot is switched off for one-on-one conversations on the Conversation page (Actions section), as by default:
the NPC who says it doesn't start looting, and Mantella's log notes it was left out.

With --advanced, it tests the actions with Advanced Actions on instead: the model picks each action with its details (tool calling), and
the game checks what each does: Follow, Unfollow, Wait, the give and take menu, the shop, MoveTo and Flee aimed at another NPC, Teleport,
TravelTo and CancelTravel, LeadTo, Loot, CollectIngredients, CastSpell (the NPC is taught Healing Hands for it and heals you), Brawl,
StandDown, Attack, ReportCrime and AbsolveCrime (a Whiterun guard is placed next to you for it; your bounty is put back) and
AddToConversation. This script then checks that the NPC
went on with the game's answer after CheckDirections, Inventory and ReportCrime, and that Ysolda remembers the conversation she was told
about (ShareConversation), and takes that memory out of your playthrough's files again. Emote isn't checked: a script can't see an
animation play.

So that each action happens on cue, MantellaSoftware talks to a stand-in AI service this script runs on your PC instead of a real model.
The stand-in answers each of the player's lines with the action its cue asks for, e.g. "ACTIONTEST_follow Come with me." gets
"Follow: Right behind you.", and with Advanced Actions on "ACTIONTEST_tmoveto@Saadia Go over to Saadia." gets "Alright." with MoveTo
picked for Saadia. Nothing goes to a real AI service, so the test costs nothing and gives the same lines every time.
The NPCs' lines are voiced with a moment of silence (tests/ingame/silent_voice.py), so they take no time to voice and say.

    python tests/ingame/actions/actions_ingame.py run --game "<Skyrim folder>"
    python tests/ingame/actions/actions_ingame.py run --advanced --game "<Skyrim folder>"

then follow the steps it prints. It saves your config.ini and points every model at the stand-in, then waits. When the game notes
"Action test: done", press Enter: it reads the game's results from Papyrus.0.log, prints PASS or FAIL for each check, and puts your
config.ini back. If it stops halfway, "cleanup" puts your config.ini back. Papyrus logging has to be on (bEnableLogging=1 and
bEnableTrace=1 in the [Papyrus] section of Skyrim.ini, and of SkyrimCustom.ini if it has one, as that wins; with Mod Organizer, the
profile's).
"""
import argparse
import glob
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
ADVANCED_BATCH = "mtaa"
ADVANCED_BATCH_LINES = BATCH_LINES[:2] + ["setpqv MantellaQuest actionTestRun 2"] # 2: the run with Advanced Actions on
REQUEST_LOG = "action_test_requests.jsonl" # In Mantella's folder: each request the stand-in got, for the checks of the advanced run
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
    "unfollow": "Unfollow: I'll stay here, then.",
    "wait": "Wait: I'll be right here.",
    "inventory": "Inventory: Here, have a look.",
    "barter": "Barter: Take a look at my wares.",
    "loot": "Loot: Let's see what's lying around.",
    "teleport": "Teleport: Here I am.",
    "moveto": "MoveTo: Coming over.",
    "brawl": "Brawl: Come on, then!",
    "flee": "Flee: I'm getting out of here!",
    "offended": "Attack: How dare you!",
    "forgiven": "StandDown: Fine. Let's forget it.",
}
# The actions switched on for one-on-one conversations during the test. Loot stays off
ONE_ON_ONE_ACTIONS = "Follow, Unfollow, Wait, Inventory, Barter, Teleport, MoveTo, Brawl, Flee, Attack, StandDown"
LEFT_OUT = "used Loot, which NPCs can't use in this conversation" # Mantella's log, when the NPC says the switched-off Loot
PLAIN_ANSWER = "Alright."

# With Advanced Actions on: the action the stand-in picks for each cue, and its details. "$1" and "$2" are the values after the cue's @,
# separated by +, with _ for a space: "ACTIONTEST_tcastspell@Prisoner+Healing_Hands". Who does it is always the NPC speaking
TOOL_CUES: dict[str, tuple[str, dict[str, Any]]] = {
    "tfollow": ("Follow", {}),
    "tunfollow": ("Unfollow", {}),
    "twait": ("Wait", {}),
    "tinventory": ("Inventory", {}),
    "tbarter": ("Barter", {}),
    "tdirections": ("CheckDirections", {"location": "Riverwood"}),
    "tmoveto": ("MoveTo", {"target": "$1"}),
    "tflee": ("Flee", {"target": "$1"}),
    "tteleport": ("Teleport", {"target": "$1"}),
    "ttravel": ("TravelTo", {"location": "Riverwood", "leave_now": True}),
    "tcancel": ("CancelTravel", {}),
    "tleadto": ("LeadTo", {"target": "$1", "location": "Riverwood"}),
    "tloot": ("Loot", {}),
    "tcollect": ("CollectIngredients", {}),
    "tcastspell": ("CastSpell", {"target": "$1", "spellName": "$2"}),
    "tbrawl": ("Brawl", {}),
    "tattack": ("Attack", {"target": "$1"}),
    "tstanddown": ("StandDown", {}),
    "tshare": ("ShareConversation", {"recipient": "Ysolda"}),
    "treport": ("ReportCrime", {"crime_gold": 10}),
    "tabsolve": ("AbsolveCrime", {"crime_gold": 10}),
    "tadd": ("AddToConversation", {"source": ["$1"]}),
}
TOOL_CUE = re.compile(r"ACTIONTEST_(t[a-z]+)(?:@(\S+))?", re.IGNORECASE)
PLAYER_LINE = re.compile(r"([^.:()*\n]+): ACTIONTEST_") # Who says the cues: the player, as the conversation so far shows them
# Ticked for one-on-one and group conversations in the advanced run (ReportCrime and AddToConversation make it a group)
ADVANCED_ACTIONS = ", ".join(sorted({name for name, _ in TOOL_CUES.values()}))
SHARED_WITH = "Ysolda" # ShareConversation's recipient: she isn't in the Bannered Mare, and only one NPC has her name
SHARE_MARKER = f"told {SHARED_WITH} about a conversation" # What her memory of it says (Summaries.share_conversation)
# What the game answers, as Mantella's in-game events, after the actions that answer back. The NPC must go on with it
ANSWERS_BACK = {"directions-answered": ("CheckDirections", ["Riverwood is", "Cannot find location: Riverwood", "Cannot determine directions"]),
                "inventory-answered": ("Inventory", ["inventory opened"]),
                "reportcrime-answered": ("ReportCrime", ["Crime reported against the player", "Cannot report crime without a guard"])}

# The checks MantellaActionTest notes, by id. A check that never shows counts as failed: the test stopped before it
CHECKS = ["follow-off", "inventory-off", "offended-off", "follow-on", "unfollow-on", "wait-on", "inventory-on", "inventory-typed", "barter-on",
          "loot-switched-off", "teleport-on", "moveto-on", "brawl-on", "flee-on", "offended-on", "forgiven-on"]
# The advanced run's. SKIP counts as passed: the game noted why it couldn't check (no guard could be placed, the bounty was already paid)
ADVANCED_CHECKS = ["follow-tool", "unfollow-tool", "wait-tool", "inventory-tool", "barter-tool", "moveto-npc", "flee-npc", "teleport-tool",
                   "travelto-tool", "canceltravel-tool", "leadto-tool", "loot-tool", "collectingredients-tool", "castspell-known", "castspell-tool", "brawl-tool",
                   "standdown-tool", "attack-tool", "reportcrime-tool", "absolvecrime-tool", "addtoconversation-tool"]


def settings_for_test(port: int, advanced: bool = False) -> dict[str, str]:
    url = f"http://127.0.0.1:{port}/v1"
    services = ["llm_api", "multi_npc_llm_api", "game_master_llm_api", "summary_llm_api", "thoughts_llm_api", "reflection_llm_api",
                "npc_event_llm_api", "fallback_llm_api", "bio_llm_api", "vision_llm_api", "advanced_actions_llm_api"]
    models = ["model", "multi_npc_model", "game_master_model", "summary_model", "thoughts_model", "reflection_model", "npc_event_model",
              "fallback_model", "bio_llm_model", "vision_model", "advanced_actions_model"]
    off = ["multi_conversation_director_mode", # Would make the conversation with one NPC a directed one, with the Director Mode actions
           "game_master_use_decision_model", "conversation_summary_enabled", "inner_monologue_enabled", "personal_reflection_enabled",
           "random_llm_one_on_one_enabled", "random_llm_multi_npc_enabled", "random_llm_one_on_one_per_request_enabled",
           "random_llm_multi_npc_per_request_enabled", "sequential_llm_one_on_one_per_request_enabled",
           "sequential_llm_multi_npc_per_request_enabled", "sonnet_prompt_caching_enabled", "allow_per_character_llm_overrides",
           "vision_enabled", "fallback_llm_enabled", "automatic_greeting", "bio_llm_apply_profile", "apply_profile_one_on_one",
           "apply_profile_multi_npc", "apply_profile_game_master", "apply_profile_summaries", "apply_profile_thoughts",
           "apply_profile_reflection", "apply_profile_npc_event", "apply_profile_fallback", "apply_profile_advanced_actions",
           "advanced_actions_own_model"]
    advanced_values = {"advanced_actions_enabled": str(advanced)}
    if advanced:
        advanced_values.update({"actions_one_on_one": ADVANCED_ACTIONS, "actions_group": ADVANCED_ACTIONS})
    return {**{key: url for key in services}, **{key: MODEL for key in models}, **{key: "False" for key in off},
            "custom_token_count": "64000", "multi_npc_custom_token_count": "64000", # A local model counts as small, so long prompts would be cut
            "actions_one_on_one": ONE_ON_ONE_ACTIONS,
            "follow_npc_response": "Follow", "unfollow_npc_response": "Unfollow", "wait_npc_response": "Wait",
            "inventory_npc_response": "Inventory", "barter_npc_response": "Barter", "brawl_npc_response": "Brawl",
            "loot_npc_response": "Loot", "teleport_npc_response": "Teleport", "moveto_npc_response": "MoveTo", "flee_npc_response": "Flee",
            "offended_npc_response": "Attack", "forgiven_npc_response": "StandDown", **voice_settings(), **advanced_values}


# The stand-in AI service

def last_user_text(request: dict[str, Any]) -> str:
    user_lines = [m for m in request.get("messages", []) if m.get("role") == "user"]
    if not user_lines:
        return ""
    content = user_lines[-1].get("content")
    return content if isinstance(content, str) else json.dumps(content)


def answer_for(request: dict[str, Any]) -> str:
    """The cue in the latest player line picks the answer. Anything else (a summary, a line without a cue) gets a plain answer.
    The Game Master gets the player when it may pick them, so the NPCs don't talk on, and otherwise the first NPC it may pick"""
    text = last_user_text(request)
    choices = re.search(r"Who speaks next\? Choose one of: (.*?)\.(?:\s|$)", text)
    if choices:
        listed = choices.group(1)
        players = [name.strip() for name in PLAYER_LINE.findall(text)]
        player = players[-1] if players else ""
        # A name may have a comma in it ("Volmann, Whiterun Guard"), so the player is looked for in the whole list
        if player and re.search(rf"(?:^|, ){re.escape(player)}(?:,|$)", listed):
            chosen = player
        else:
            chosen = listed.split(", ")[0].strip()
        return json.dumps({"next": chosen, "reason": "the test"})
    found = CUE.findall(text)
    if found:
        return ANSWERS.get(found[-1].lower(), PLAIN_ANSWER)
    return PLAIN_ANSWER


def tool_call_for(request: dict[str, Any]) -> dict[str, Any] | None:
    """With Advanced Actions on: the action the cue in the latest player line asks for, as the model picks it ({"name", "arguments"}).
    {"not_offered": name} if the request didn't offer that action, None if there is no such cue"""
    offered = {tool.get("function", {}).get("name") for tool in request.get("tools") or []}
    found = TOOL_CUE.findall(last_user_text(request))
    if not offered or not found:
        return None
    cue, values_text = found[-1]
    if cue.lower() not in TOOL_CUES:
        return None
    name, template = TOOL_CUES[cue.lower()]
    if name not in offered:
        return {"not_offered": name}
    values = [value.replace("_", " ") for value in values_text.split("+")] if values_text else []

    def fill(value: Any) -> Any:
        if isinstance(value, list):
            return [fill(v) for v in value]
        if isinstance(value, str) and value.startswith("$"):
            index = int(value[1:]) - 1
            return values[index] if index < len(values) else ""
        return value

    return {"name": name, "arguments": {key: fill(value) for key, value in template.items()}}


class StandIn(BaseHTTPRequestHandler):
    """Speaks enough of the OpenAI chat API for MantellaSoftware: a model list, and chat completions, streamed or not, with tool calls"""
    log_path = ""
    requests_path = ""

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
        tool_call = tool_call_for(request)
        picked = tool_call if tool_call and "name" in tool_call else None
        if picked:
            answer = PLAIN_ANSWER # The line that goes with the action
        last = last_user_text(request)
        with open(self.log_path, "a", encoding="utf-8") as f:
            picked_text = f" picked: {picked['name']} {json.dumps(picked['arguments'])}" if picked else ""
            f.write(f"{datetime.now().strftime(TIME_FORMAT)} asked: {last[-200:]!r}{picked_text} answered: {answer!r}\n")
        with open(self.requests_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"tools": sorted(t.get("function", {}).get("name", "") for t in request.get("tools") or []) or None,
                                "stream": bool(request.get("stream")), "last_user": last[-2000:], "picked": picked, "not_offered": (tool_call or {}).get("not_offered"),
                                "answer": answer}) + "\n")
        made = int(time.time())
        call = {"id": "call_test", "type": "function", "function": {"name": picked["name"], "arguments": json.dumps(picked["arguments"])}} if picked else None
        if not request.get("stream"):
            message: dict[str, Any] = {"role": "assistant", "content": answer}
            if call:
                message["tool_calls"] = [call]
            self.__send_json({"id": "stand-in", "object": "chat.completion", "created": made, "model": MODEL,
                              "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if call else "stop"}],
                              "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        # As a real model streams it: the line first, then the action it picked, its name and its details in separate pieces
        deltas: list[tuple[dict[str, Any], str | None]] = [({"role": "assistant", "content": answer}, None)]
        if call:
            deltas += [({"tool_calls": [{"index": 0, "id": call["id"], "type": "function", "function": {"name": call["function"]["name"], "arguments": ""}}]}, None),
                       ({"tool_calls": [{"index": 0, "function": {"arguments": call["function"]["arguments"]}}]}, None)]
        deltas.append(({}, "tool_calls" if call else "stop"))
        for delta, finish in deltas:
            chunk = {"id": "stand-in", "object": "chat.completion.chunk", "created": made, "model": MODEL,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def start_stand_in(port: int, log_path: str, requests_path: str = "") -> ThreadingHTTPServer:
    """Args:
        requests_path (str): where each request is logged for the checks (default: REQUEST_LOG next to log_path)"""
    StandIn.log_path = log_path
    StandIn.requests_path = requests_path or os.path.join(os.path.dirname(log_path), REQUEST_LOG)
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


def read_requests(mantella_folder: str) -> list[dict[str, Any]]:
    path = os.path.join(mantella_folder, REQUEST_LOG)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def answered_back_results(requests: list[dict[str, Any]]) -> dict[str, str]:
    """After each action that answers back, the NPC was asked for a line again, without actions to pick, with the game's answer"""
    results = {}
    for check_id, (action, answers) in ANSWERS_BACK.items():
        picked_at = [i for i, request in enumerate(requests) if (request.get("picked") or {}).get("name") == action]
        if not picked_at:
            results[check_id] = f"FAIL {check_id}: {action} was never picked, so its answer couldn't be checked"
            continue
        later = requests[picked_at[0] + 1:]
        # An NPC's line: streamed (summaries and the Game Master's picks aren't), its latest message the game's answer
        resumed = next((r for r in later if r.get("stream") and any(answer.lower() in r.get("last_user", "").lower() for answer in answers)), None)
        if resumed is None:
            results[check_id] = f"FAIL {check_id}: after {action}, the NPC never went on with the game's answer"
        elif resumed.get("tools"):
            results[check_id] = f"FAIL {check_id}: the NPC went on with {action}'s answer, but could pick actions again"
        else:
            results[check_id] = f"PASS {check_id}: after {action}, the NPC went on with the game's answer"
    return results


def share_result(mantella_folder: str, since: datetime) -> str:
    """ShareConversation: Ysolda remembers the conversation she was told about. Takes that memory out of her files again, as it is only
    the test's"""
    folders = glob.glob(os.path.join(mantella_folder, "data", "Skyrim", "conversations", "*", f"{SHARED_WITH}*"))
    found = []
    for folder in folders:
        for path in glob.glob(os.path.join(folder, "*.txt")):
            if datetime.fromtimestamp(os.path.getmtime(path)) < since:
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if SHARE_MARKER not in text:
                continue
            found.append(path)
            kept = re.sub(r"ts=\d+\n-[^\n]*" + re.escape(SHARE_MARKER) + r"[^\n]*\n*", "", text)
            if kept.strip():
                with open(path, "w", encoding="utf-8") as f:
                    f.write(kept)
            else:
                os.remove(path)
                if not os.listdir(folder):
                    os.rmdir(folder)
    if found:
        return f"PASS share-remembered: {SHARED_WITH} remembers the conversation she was told about (taken out of her files again)"
    return f"FAIL share-remembered: no memory of the shared conversation in {SHARED_WITH}'s files"


def check(mantella_folder: str, papyrus_log: str, advanced: bool = False) -> bool:
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
        batch = ADVANCED_BATCH if advanced else BATCH
        print(f"No \"Action test: starting\" in {papyrus_log}. Did the test run (bat {batch}), and is Papyrus logging on (bEnableLogging=1 and bEnableTrace=1 in Skyrim.ini and SkyrimCustom.ini)?")
        return False
    results: dict[str, str] = {}
    for note in notes:
        match = re.match(r"(PASS|FAIL|SKIP) (\S+) ?(.*)", note, re.IGNORECASE)
        if match:
            check_id = match.group(2).lower()
            results[check_id] = f"{match.group(1).upper()} {check_id}: {match.group(3)}"
        elif note.lower() != "starting":
            print(f"INFO {note}")
    checks = list(ADVANCED_CHECKS if advanced else CHECKS)
    if advanced:
        requests = read_requests(mantella_folder)
        not_offered = sorted({r["not_offered"] for r in requests if r.get("not_offered")})
        if not_offered:
            results["tools-offered"] = f"FAIL tools-offered: ticked, but not offered to the model when its cue came: {', '.join(not_offered)}"
            checks.append("tools-offered")
        results.update(answered_back_results(requests))
        results["share-remembered"] = share_result(mantella_folder, since)
        checks += list(ANSWERS_BACK) + ["share-remembered"]
    else:
        left_out = [text for _level, text in entries if LEFT_OUT in text]
        if "loot-switched-off" in results and not left_out:
            results["loot-switched-off"] = "FAIL loot-switched-off: the NPC didn't start looting, but Mantella's log doesn't say it left Loot out"
    passed = True
    for check_id in checks:
        result = results.pop(check_id, f"FAIL {check_id}: never checked (the test stopped before it)")
        passed &= not result.startswith("FAIL")
        print(result)
    for result in results.values(): # A setup failure
        passed &= not result.startswith("FAIL")
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

def setup(game_folder: str, mantella_folder: str, port: int, advanced: bool = False):
    config_path = os.path.join(mantella_folder, "config.ini")
    backup_path = os.path.join(mantella_folder, BACKUP_FILE)
    if os.path.exists(backup_path):
        print(f"{backup_path} already exists: run cleanup first, so your own config.ini is put back")
        sys.exit(1)
    shutil.copyfile(config_path, backup_path)
    missing = write_settings(config_path, settings_for_test(port, advanced))
    if missing:
        shutil.copyfile(backup_path, config_path)
        os.remove(backup_path)
        print(f"These settings are not in config.ini, so nothing was changed. Start the new Mantella once so it writes them, close it, then run again: {missing}")
        sys.exit(1)
    batch, lines = (ADVANCED_BATCH, ADVANCED_BATCH_LINES) if advanced else (BATCH, BATCH_LINES)
    with open(os.path.join(game_folder, f"{batch}.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(mantella_folder, START_FILE), "w", encoding="utf-8") as f:
        f.write(datetime.now().strftime(TIME_FORMAT))
    for name in (STAND_IN_LOG, REQUEST_LOG):
        path = os.path.join(mantella_folder, name)
        if os.path.exists(path):
            os.remove(path)


def cleanup(game_folder: str | None, mantella_folder: str):
    backup_path = os.path.join(mantella_folder, BACKUP_FILE)
    if os.path.exists(backup_path):
        shutil.copyfile(backup_path, os.path.join(mantella_folder, "config.ini"))
        os.remove(backup_path)
        print("Put your config.ini back. Restart Mantella so it reads it.")
    paths = [os.path.join(mantella_folder, START_FILE)]
    if game_folder:
        paths += [os.path.join(game_folder, f"{batch}.txt") for batch in (BATCH, ADVANCED_BATCH)]
    for path in paths:
        if os.path.exists(path):
            os.remove(path)


def run(game_folder: str, mantella_folder: str, papyrus_log: str, port: int, advanced: bool = False) -> bool:
    try:
        server = start_stand_in(port, os.path.join(mantella_folder, STAND_IN_LOG), os.path.join(mantella_folder, REQUEST_LOG))
    except OSError as e:
        print(f"Can't start the stand-in AI service on port {port} ({e}). Is something else using it? Try --port")
        return False
    try:
        voice = start_silent_voice()
    except OSError as e:
        server.shutdown()
        print(f"Can't start the silent voice ({e}). Is another in-game test still running?")
        return False
    setup(game_folder, mantella_folder, port, advanced)
    if advanced:
        what_happens = """Hulda (or the nearest NPC) follows you and stops, waits, opens her inventory and her shop, looks up the way to Riverwood,
     walks over to someone across the room and runs from them, appears next to you, sets off for Riverwood and is called back, leads you,
     loots, collects ingredients, heals you with Healing Hands, starts a fistfight, stops, attacks you and stops, tells Ysolda about the
     conversation, reports you to a Whiterun guard placed next to you and takes it back (your bounty is put back), and calls someone over."""
        steps_batch = f"bat {ADVANCED_BATCH} (moves you next to Hulda in the Bannered Mare at 8 pm and starts the test with Advanced Actions on)"
    else:
        what_happens = """Hulda (or the nearest NPC) follows you and stops, opens her shop, then you jump across the room twice: she appears next to you,
     then walks over. She starts a fistfight, runs off, attacks you briefly and stops."""
        steps_batch = f"bat {BATCH} (moves you next to Hulda in the Bannered Mare at 8 pm and starts the test)"
    print(f"""Saved your config.ini as {BACKUP_FILE} and pointed every model at the stand-in AI service (port {port}) and the voice at the silent voice,
which are now running.
Now:
  1. Start Mantella, or restart it if it is running, so it reads the test's settings.
  2. Load a save where you can go to Whiterun (not during a quest scene). Don't save during or after the test.
  3. Console: {steps_batch}
  4. Watch the notes "Action test: ...". It says your lines itself. It takes a few minutes, until "Action test: done".
     {what_happens}
     The NPCs' lines are silent, only their subtitles flash.
  5. Press Enter here. It checks the results and puts your config.ini back. Then restart Mantella.""")
    try:
        input()
        time.sleep(2) # The game writes its script log a moment later
        return check(mantella_folder, papyrus_log, advanced)
    finally:
        server.shutdown()
        voice.shutdown()
        cleanup(game_folder, mantella_folder)


def main():
    parser = argparse.ArgumentParser(description="In-game test of the NPC actions")
    parser.add_argument("command", choices=["run", "check", "cleanup"])
    parser.add_argument("--game", help="Skyrim's folder, where the console reads batch files (run, cleanup)")
    parser.add_argument("--advanced", action="store_true", help="Test the actions with Advanced Actions on (run, check)")
    parser.add_argument("--mantella-folder", default=default_mantella_folder(), help="Mantella's user folder (default: Documents/My Games/Mantella)")
    parser.add_argument("--papyrus-log", default=default_papyrus_log(), help="The game's script log (default: Documents/My Games/Skyrim Special Edition/Logs/Script/Papyrus.0.log)")
    parser.add_argument("--port", type=int, default=PORT, help=f"Where the stand-in AI service listens (default: {PORT})")
    args = parser.parse_args()
    if args.command == "run":
        if not args.game:
            parser.error("run needs --game")
        sys.exit(0 if run(args.game, args.mantella_folder, args.papyrus_log, args.port, args.advanced) else 1)
    elif args.command == "check":
        sys.exit(0 if check(args.mantella_folder, args.papyrus_log, args.advanced) else 1)
    else:
        cleanup(args.game, args.mantella_folder)


if __name__ == "__main__":
    main()
