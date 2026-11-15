"""In-game test of the Conversation Wheel events: the game runs each event by itself (MantellaEventTest.psc in Mantella-Spell), then this
script checks Mantella's log for what the game sent and what Mantella did with it.

See docs/conversation_wheel.md, "Automated tests", for the steps. In short:

    python tests/ingame/conversation_events/conversation_events_ingame.py setup --game "<Skyrim folder>"
    (Mantella running with Show HTTP Debug Messages on. Load a save, `bat mwe`, watch until "Event test: done")
    python tests/ingame/conversation_events/conversation_events_ingame.py check
    python tests/ingame/conversation_events/conversation_events_ingame.py cleanup --game "<Skyrim folder>"

What the models decide (whether something happens, who reacts, who joins) changes from run to run, so it is shown as INFO.
What the game sends and what Mantella does with it is checked: PASS or FAIL.
"""
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src.http.communication_constants import communication_constants as comm_consts  # noqa: E402

BATCH = "mwe"
BATCH_LINES = [
    "set gamehour to 20",  # Evening: the Bannered Mare is busy
    "player.moveto 1A66E",  # Next to Hulda in the Bannered Mare
    "setpqv MantellaQuest eventTestRun 1",  # MantellaListenerScript starts MantellaEventTest on its next tick
]
START_FILE = "event_test_start.txt"  # In Mantella's folder: when setup ran. The check reads the log from then on
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
ENTRY = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d{3} ([^:\n]+): ", re.MULTILINE)


def default_mantella_folder() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents", "My Games", "Mantella")


def setup(game_folder: str, mantella_folder: str):
    with open(os.path.join(game_folder, f"{BATCH}.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(BATCH_LINES) + "\n")
    with open(os.path.join(mantella_folder, START_FILE), "w", encoding="utf-8") as f:
        f.write(datetime.now().strftime(TIME_FORMAT))
    print(f"Wrote {BATCH}.txt in {game_folder}.")
    print(f"""Now:
  1. In Mantella's settings, turn on Show HTTP Debug Messages (Other tab, under Advanced) and restart Mantella, if it isn't on yet.
     Use text input (microphone off): with the microphone, each step waits 90 seconds.
  2. Load a save where you can go to Whiterun (not during a quest scene). Don't save during or after the test.
  3. Console: bat {BATCH} (moves you to the Bannered Mare at 8 pm and starts the test)
  4. Watch the notes "Event test: ...". If a menu asks about Private, pick Yes. If a text box asks for a hint, leave it empty.
     It takes a few minutes, until "Event test: done".
  5. python {os.path.relpath(__file__)} check""")


@dataclass
class Exchange:
    """A request the game sent, Mantella's reply, and Mantella's log lines from then until the next request"""
    request: dict[str, Any]
    reply: dict[str, Any] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return str(self.request.get(comm_consts.KEY_REQUESTTYPE, ""))

    @property
    def log_text(self) -> str:
        return "\n".join(self.log)


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
                entries.append((match.group(2), text[match.end():end].strip()))
    return entries


def read_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def exchanges(entries: list[tuple[str, str]]) -> list[Exchange]:
    result: list[Exchange] = []
    for level, text in entries:
        if level == "HTTP-in":
            result.append(Exchange(read_json(text)))
        elif level == "HTTP-out" and result and not result[-1].reply:
            result[-1].reply = read_json(text)
        elif result:
            result[-1].log.append(f"{level}: {text}")
    return result


def conversations(all_exchanges: list[Exchange]) -> list[list[Exchange]]:
    """Each conversation's requests, from its start request on"""
    result: list[list[Exchange]] = []
    for exchange in all_exchanges:
        if exchange.kind == comm_consts.KEY_REQUESTTYPE_STARTCONVERSATION:
            result.append([exchange])
        elif result:
            result[-1].append(exchange)
    return result


class Report:
    def __init__(self):
        self.passed = True

    def check(self, ok: bool, text: str):
        self.passed &= ok
        print(f"PASS {text}" if ok else f"FAIL {text}")

    def info(self, text: str):
        print(f"INFO {text}")


def refids(actors: list[dict[str, Any]], players: bool = False) -> set[int]:
    return {a[comm_consts.KEY_ACTOR_REFID] for a in actors if bool(a.get(comm_consts.KEY_ACTOR_ISPLAYER)) == players}


def event_kind(exchange: Exchange) -> str:
    """Papyrus can change a string's case: the game sends "Random" for "random\""""
    return str(exchange.request.get(comm_consts.KEY_EVENT_KIND, "")).lower()


def check_event(report: Report, conversation: list[Exchange], exchange: Exchange, label: str) -> bool:
    """The event request the game sent, and what Mantella did with it. Returns whether something happened"""
    request, reply, log = exchange.request, exchange.reply, exchange.log_text
    kind = event_kind(exchange)
    actors = request.get(comm_consts.KEY_ACTORS, [])
    player = next((a[comm_consts.KEY_ACTOR_NAME] for a in actors if a.get(comm_consts.KEY_ACTOR_ISPLAYER)), "")
    members = refids(actors)
    listed_refids = request.get(comm_consts.KEY_EVENT_NEARBY_REFIDS, [])
    listed_names = request.get(comm_consts.KEY_EVENT_NEARBY_NAMES, [])
    if kind == "conversation":
        report.check(not listed_refids, f"{label}: no list of people nearby, only the conversation")
    else:
        listed = listed_refids[1:]
        report.check(listed_refids[:1] == [0] and listed_names[:1] == [""] and len(listed_refids) == len(listed_names),
                     f"{label}: people nearby sent by refid and name, nearest first: {', '.join(listed_names[1:]) or 'nobody'}")
        if kind == "nearby":
            report.check(bool(listed) and not members & set(listed), f"{label}: only people outside the conversation listed")
        else:
            report.check(members <= set(listed), f"{label}: the people in the conversation listed too")
        described = request.get(comm_consts.KEY_EVENT_MAXDESCRIBED, 0)
        sent = refids(request.get(comm_consts.KEY_EVENT_NEARBY_ACTORS, []))
        report.check(sent <= set(listed[:described]) and not members & sent,
                     f"{label}: full details sent of {len(sent)} of the nearest {described} (Most People Described), none in the conversation")
        if not request.get(comm_consts.KEY_SHOUT_ENABLED):
            report.check(comm_consts.KEY_SHOUT_MAXDESCRIBED in request, f"{label}: Shout was off, so the Shout Mode settings the game keeps were sent with it")
    about = re.search(rf"Event \({kind}\) about: (.*)", log)
    report.check(about is not None, f"{label}: Mantella asked the event generator about: {about.group(1) if about else '(no log line)'}")
    if about and player:
        # Include Me is Mantella's own setting (Conversation page), so only shown
        named = about.group(1).split(". With bios")[0].split(", ")
        report.info(f"{label}: the player {'named' if player in named else 'not named'} (Include Me on the Conversation page)")
    failure = re.search(r"Event failed: .*", log)
    report.check(failure is None, f"{label}: {failure.group(0) if failure else 'no error'}")
    happened = bool(reply.get(comm_consts.KEY_EVENT_RESULT))
    report.info(f"{label}: {'something happened' if happened else 'nothing happened: the event generator wrote no event'}")
    later = [x for x in conversation[conversation.index(exchange) + 1:] if x.kind != comm_consts.KEY_REQUESTTYPE_NPCEVENT]
    if happened:
        report.check(bool(later) and later[0].kind == comm_consts.KEY_REQUESTTYPE_CONTINUECONVERSATION,
                     f"{label}: the game went on with the conversation, so someone can react")
    if happened and kind != "conversation":
        later = [x for x in conversation[conversation.index(exchange) + 1:] if comm_consts.KEY_SHOUT_ENABLED in x.request]
        report.check(bool(later) and later[0].request[comm_consts.KEY_SHOUT_ENABLED] is True, f"{label}: the game switched Shout on")
    return happened


def joins(conversation: list[Exchange]) -> list[str]:
    """Who joined by shouting, as Mantella logged the Game Master's picks"""
    return [m.group(1) for x in conversation for m in re.finditer(r"Game Master: (.+?) joins the conversation", x.log_text)]


def check_one_on_one(report: Report, conversation: list[Exchange]):
    print("\nOne-on-one conversation: Conversation, Nearby and Random Events, then Private")
    events = [x for x in conversation if x.kind == comm_consts.KEY_REQUESTTYPE_CONVERSATIONEVENT]
    for kind in ("conversation", "nearby", "random"):
        sent = next((x for x in events if event_kind(x) == kind), None)
        if sent:
            check_event(report, conversation, sent, f"{kind.capitalize()} Event")
            events = events[events.index(sent) + 1:]
        else:
            report.check(False, f"{kind.capitalize()} Event: not sent" + (" (did the note say Nobody is nearby?)" if kind == "nearby" else ""))
    joined = joins(conversation)
    report.info(f"Joined by shouting: {', '.join(joined) or 'nobody'}")
    if not events:
        report.check(False, "Private: no request after it (the test sends one more Conversation Event)")
        return
    after = events[0]
    was_shouting = any(x.request.get(comm_consts.KEY_SHOUT_ENABLED) for x in conversation[:conversation.index(after)])
    if was_shouting:
        report.check(after.request.get(comm_consts.KEY_SHOUT_ENABLED) is False and "Shout: private again" in after.log_text,
                     "Private: the game switched Shout off and Mantella stopped Shout")
    else:
        report.info("Private: Shout was never on (nothing happened in the Nearby and Random Events)")
    if joined:
        staying = {a[comm_consts.KEY_ACTOR_NAME] for a in after.request.get(comm_consts.KEY_ACTORS, [])}
        report.check(not staying & set(joined), f"Private: who joined by shouting left. Still there: {', '.join(sorted(staying))}")
    check_event(report, conversation, after, "Conversation Event after Private")


def check_group(report: Report, conversation: list[Exchange]):
    print("\nGroup conversation: a Conversation Event, one picked while an NPC reacts, then ending while one is generated")
    events = [x for x in conversation if x.kind == comm_consts.KEY_REQUESTTYPE_CONVERSATIONEVENT]
    if not events:
        report.check(False, "Conversation Event: not sent")
        return
    check_event(report, conversation, events[0], "Conversation Event")
    if len(events) < 2:
        report.check(False, "Ending while an event is generated: no event sent before the end")
        return
    if len(events) >= 3:
        between = conversation[conversation.index(events[0]) + 1:conversation.index(events[1])]
        talked = any(x.reply.get(comm_consts.KEY_REPLYTYPE_NPCTALK) for x in between)
        report.check(talked, "Conversation Event picked while an NPC talked: an NPC was talking when it was picked")
        check_event(report, conversation, events[1], "Conversation Event picked while an NPC talked")
    else:
        report.info("Conversation Event picked while an NPC talked: skipped, nobody reacted to the first one")
    last = events[-1]
    after = conversation[conversation.index(last) + 1:]
    report.check(bool(after) and after[0].kind == comm_consts.KEY_REQUESTTYPE_ENDCONVERSATION,
                 "Ending while an event is generated: the game ended the conversation right after sending it")
    logs = "\n".join(x.log_text for x in [last] + after)
    report.check("Event cancelled: the conversation ended" in logs, "Ending while an event is generated: Mantella threw the event away")


def check_npc_event(report: Report, all_exchanges: list[Exchange], all_conversations: list[list[Exchange]]):
    print("\nNPC Event: the player watches, steps in and picks a Conversation Event")
    checks = [x for x in all_exchanges if x.kind == comm_consts.KEY_REQUESTTYPE_NPCEVENT]
    if not checks:
        report.check(False, "NPC Event check: not sent")
        return
    request, reply = checks[-1].request, checks[-1].reply
    names = request.get(comm_consts.KEY_NPCEVENT_NEARBY_NAMES, [])
    nearby = request.get(comm_consts.KEY_NPCEVENT_NEARBY_REFIDS, [])
    actors = request.get(comm_consts.KEY_ACTORS, [])
    described = [a[comm_consts.KEY_ACTOR_REFID] for a in actors if not a.get(comm_consts.KEY_ACTOR_ISPLAYER)]
    report.check(len(names) == len(nearby) >= 2 and described == nearby[:len(described)],
                 f"NPC Event check: {len(nearby)} people nearby by name, the nearest {len(described)} in full: {', '.join(names)}")
    event_id = reply.get(comm_consts.KEY_NPCEVENT_ID, 0)
    if not event_id:
        report.info("NPC Event: the event generator wrote no scene this time. Run the test again for this part")
        return
    scene = next((c for c in all_conversations if c[0].request.get(comm_consts.KEY_NPCEVENT_ID) == event_id), None)
    if not scene:
        # The game drops it when someone in it is no longer free to talk, a menu is open or the player is in combat. With Show Debug
        # Messages on the Events page, the note "NPC Event: can no longer start" says so
        report.info(f"NPC Event {event_id}: the game didn't start it, as the situation changed while it was written. Run the test again for this part")
        return
    start = scene[0].request
    participants = sorted(refids(start.get(comm_consts.KEY_ACTORS, [])))
    report.check(participants == sorted(reply.get(comm_consts.KEY_NPCEVENT_PARTICIPANT_REFIDS, [])) and not refids(start.get(comm_consts.KEY_ACTORS, []), players=True),
                 "NPC Event: the game started it with the participants, without the player")
    report.check(start.get(comm_consts.KEY_SHOUT_ENABLED) is True and len(start.get(comm_consts.KEY_SHOUT_NEARBY_REFIDS, [])) >= 1,
                 "NPC Event: it starts in Shout, with who is nearby")
    report.info(f"Joined the scene by shouting: {', '.join(joins(scene)) or 'nobody'}")
    stepped_in = next((x for x in scene if refids(x.request.get(comm_consts.KEY_ACTORS, []), players=True)), None)
    if not stepped_in:
        report.info("NPC Event: the scene ended before the player stepped in")
        return
    report.check(True, "NPC Event: the player stepped in")
    events = [x for x in scene if x.kind == comm_consts.KEY_REQUESTTYPE_CONVERSATIONEVENT]
    if events:
        check_event(report, scene, events[0], "Conversation Event after stepping in")
    else:
        report.check(False, "Conversation Event after stepping in: not sent")


def check(mantella_folder: str) -> bool:
    start_path = os.path.join(mantella_folder, START_FILE)
    if not os.path.exists(start_path):
        print(f"Run setup first: {start_path} not found")
        return False
    with open(start_path, encoding="utf-8") as f:
        since = datetime.strptime(f.read().strip(), TIME_FORMAT)
    entries = read_log(mantella_folder, since)
    all_exchanges = exchanges(entries)
    if not all_exchanges:
        print(f"No requests from the game in Mantella's log since {since}. Turn on Show HTTP Debug Messages in Mantella's settings, "
              "restart Mantella and run the test again")
        return False
    all_conversations = conversations(all_exchanges)
    report = Report()
    def with_player(npc_count: int) -> list[list[Exchange]]:
        """The player's conversations with that many NPCs, so a radiant conversation that started in between is left out"""
        return [c for c in all_conversations if refids(c[0].request.get(comm_consts.KEY_ACTORS, []), players=True)
                and len(refids(c[0].request.get(comm_consts.KEY_ACTORS, []))) == npc_count]
    for found, check_conversation, name in ((with_player(1), check_one_on_one, "one-on-one"), (with_player(2), check_group, "group")):
        if found:
            check_conversation(report, found[-1])  # The latest run
        else:
            report.check(False, f"No {name} conversation in the log since setup")
    check_npc_event(report, all_exchanges, all_conversations)

    errors = [text.splitlines()[0] for level, text in entries if level == "ERROR"]
    if errors:
        print(f"\n{len(errors)} errors in Mantella's log during the test (may be unrelated):")
        for error in errors[:10]:
            print(f"  {error}")
    print(f"\n{'All checks passed' if report.passed else 'Some checks FAILED'}")
    return report.passed


def cleanup(game_folder: str | None, mantella_folder: str):
    paths = [os.path.join(mantella_folder, START_FILE)]
    if game_folder:
        paths.append(os.path.join(game_folder, f"{BATCH}.txt"))
    for path in paths:
        if os.path.exists(path):
            os.remove(path)
            print(f"Removed {path}")
    print("Turn Show HTTP Debug Messages off again if you don't need it.")


def main():
    parser = argparse.ArgumentParser(description="In-game test of the Conversation Wheel events")
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
