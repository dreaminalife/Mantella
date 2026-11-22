"""Asking the game for voice clips, and taking in what it sends.

Mantella can't call the game by itself, so a request rides on Mantella's next reply to the game (mantella_voice_clip_requests).
The add-on hands each one to MantellaVoiceClips.dll, which copies the lines into voice_clips/incoming/<request id>/ and writes
result.json last. The add-on then says so with a voice_clip_delivered request, and Mantella takes the files in.
Mantella also looks in that folder before choosing a voice, so a clip is used even if that message is lost.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from src.voice_clips import fuz
from src.voice_clips.clip_store import (AUTOMATIC, MOST_CHOSEN, NPCS, OWN_LINES, VOICE_TYPES, ClipInfo, ClipStore, line_id,
                                        npc_key, voice_key)

INCOMING = "incoming"
REQUEST_FILE = "request.json"
RESULT_FILE = "result.json"
ASK_AGAIN_SECONDS = 300 # while nothing comes back, the same clip is asked for at most this often
LINES_TO_HEAR = 4 # the line picked, plus others to choose from in the list
MOST_MORE = 16 # Find more clips brings at most this many at a time (about 2 minutes of game lines, as many as a voice is cloned from)
SKIP_WORDS = ["bardsongs"] # voice file names with these words are never picked (every bard song starts with bardsongs_)
MIN_SECONDS = 5.0
MAX_SECONDS = 10.0

# Why a request was made, which decides what the delivered lines become
FIRST = "first" # no clip yet: the first line becomes the clip, all of them are chosen
OWN = "own" # the player gave an NPC a clip of their own
MORE = "more" # Find more clips: the new lines take the place of the ones not chosen
TOP_UP = "top up" # the voice is too short for the service speaking: the new lines are chosen, at the end
REFRESH = "refresh" # the same lines again, to notice a voice replacer
IMPORT = "import" # lines from someone's voices file, in their order
REPLACE = "replace" # a clip's game line is gone: the first new line becomes the clip
LIST = "list" # no lines: the voice types the game's NPCs use, for Prepare all voices
STALE_SECONDS = 3600 # a request folder with no result after this long is given up (the add-on or its DLL is missing)
IN_TURN_AT_ONCE = 8 # voice types from Prepare all voices handed to the game at a time: each delivery brings the next ones
IN_TURN_LOST_SECONDS = 120 # one of those with no answer after this long no longer holds up the next


@dataclass
class ClipRequest:
    id: str
    why: str
    voice_type: str = ""
    npc_plugin: str = ""
    npc_local_id: str = ""
    npc_name: str = ""
    voice_type_lines: bool = True
    npc_lines: str = "none" # none, if_different or always
    exclude: list[str] = field(default_factory=list)
    lines: list[dict[str, str]] = field(default_factory=list) # exact lines, for a refresh or an import
    keys: list[str] = field(default_factory=list) # what this request is waiting for, for asking again
    count: int = LINES_TO_HEAR # lines to copy of each kind
    stand_ins: list[str] = field(default_factory=list) # voice types to ask for instead, in turn, when the game has no lines for this one
    again: bool = False # Find more clips asked a second time, with the lines it skipped before

    def to_game(self, dest: str) -> dict[str, Any]:
        if self.why == LIST:
            return {"id": self.id, "dest": dest, "list_voice_types": True}
        request: dict[str, Any] = {"id": self.id, "dest": dest, "voice_type": self.voice_type, "voice_type_lines": self.voice_type_lines,
                                   "npc_lines": self.npc_lines, "count": self.count, "min_seconds": MIN_SECONDS, "max_seconds": MAX_SECONDS,
                                   "skip_words": SKIP_WORDS, "exclude": self.exclude, "lines": self.lines}
        if self.npc_plugin and self.npc_local_id:
            request["npc_plugin"] = self.npc_plugin
            request["npc_local_id"] = int(self.npc_local_id, 16)
        return request


class ClipRequests:
    def __init__(self, store: ClipStore) -> None:
        self.__store = store
        self.__lock = threading.RLock()
        self.__waiting: list[ClipRequest] = [] # not yet handed to the game
        self.__asked: dict[str, float] = {} # what was asked for -> when, while nothing came back
        self.__refreshed: set[str] = set() # clips whose line was checked this run
        self.__nothing_found: set[str] = set() # voice types the game has no lines for (an unvoiced follower's): not asked again this run
        self.__more_asked: set[str] = set() # clips the player asked more lines for, until the game answers
        self.__no_more: set[str] = set() # clips the game had no more lines for, the last time the player asked
        self.__topped_up: set[str] = set() # voices topped up this run: at most once each
        self.__counter = itertools.count(1)
        self.__in_turn: list[str] = [] # voice types waiting their turn (Prepare all voices)
        self.__in_turn_sent: dict[str, float] = {} # request id -> when it was handed to the game
        self.on_voice_types: Any = None # called with the voice types the game listed
        self.on_arrival: Any = None # called when delivered clips were taken in
        self.__on_the_way: dict[str, str] = {} # a new voice's clips handed to the game and not delivered yet: what for -> request id
        self.__waited: set[str] = set() # requests a line waited for already: no other line waits for them
        self.__arrival = threading.Condition()

    @property
    def incoming_folder(self) -> str:
        return os.path.join(self.__store.folder, INCOMING)

    # ---- a line waiting for its voice ----

    def on_the_way(self, keys: list[str]) -> list[str]:
        """The requests for these voices (voice_types/<key>, npcs/<key>) handed to the game and not delivered yet, that no line waited for before"""
        with self.__lock:
            found = [self.__on_the_way[key] for key in keys if key in self.__on_the_way]
            return [request_id for request_id in dict.fromkeys(found) if request_id not in self.__waited]

    def waited_for(self, request_ids: list[str]) -> None:
        """A line waited for these requests: one that doesn't come in time holds up no other line"""
        with self.__lock:
            self.__waited.update(request_ids)

    def wait_for_delivery(self, seconds: float) -> None:
        """Until clips are taken in, or *seconds* pass, then looks in the folder: the game's message saying they came may be held up"""
        with self.__arrival:
            self.__arrival.wait(max(0.0, seconds))
        self.collect()

    # ---- asking ----

    def ask_for(self, voice_type: str | None, npc_plugin: str | None = None, npc_local_id: str | None = None, npc_name: str = "",
                stand_ins: list[str] | None = None, count: int = LINES_TO_HEAR) -> None:
        """Called when an NPC (or the narrator, with only a voice type) speaks with a service that copies voices.
        stand_ins: the voice types the NPC uses when theirs has no lines (an unvoiced follower): the one in their character file,
        then their race and gender's. Each is asked for once the game says the one before has no lines.
        count: the lines a new voice gets, as many as the service speaking takes (10 for ElevenLabs)"""
        stand_ins = [v for v in dict.fromkeys(v for v in (stand_ins or []) if voice_key(v)) if voice_key(v) != voice_key(voice_type)]
        if not voice_type:
            if stand_ins:
                self.ask_for(stand_ins[0], stand_ins=stand_ins[1:], count=count)
            return
        type_key = voice_key(voice_type)
        own_key = npc_key(npc_plugin, npc_local_id)
        needs_voice_type = not self.__store.voice_type_clip(voice_type)
        unchecked_npc = bool(own_key) and self.__store.remember_npc(str(npc_plugin), str(npc_local_id), npc_name, voice_type)
        with self.__lock:
            keys = []
            no_lines = f"{VOICE_TYPES}/{type_key}" in self.__nothing_found
            if needs_voice_type and not no_lines and self.__can_ask(f"{VOICE_TYPES}/{type_key}"):
                keys.append(f"{VOICE_TYPES}/{type_key}")
            if unchecked_npc and self.__can_ask(f"{NPCS}/{own_key}"):
                keys.append(f"{NPCS}/{own_key}") # their own lines may come from a voice mod for just them
            if keys:
                self.__queue(ClipRequest(self.__new_id(), FIRST, voice_type, npc_plugin or "", npc_local_id or "", npc_name,
                                         voice_type_lines=True, npc_lines="if_different" if own_key else "none", keys=keys, stand_ins=stand_ins,
                                         count=max(1, count)))
        if needs_voice_type and no_lines and stand_ins:
            # the game has no lines for their voice type: the next voice in line instead
            self.ask_for(stand_ins[0], stand_ins=stand_ins[1:], count=count)
        self.__refresh(VOICE_TYPES, type_key)
        if own_key:
            self.__refresh(NPCS, own_key)

    def ask_for_own_clip(self, npc_plugin: str, npc_local_id: str, npc_name: str, voice_type: str, count: int = LINES_TO_HEAR) -> None:
        """Give this NPC their own clip: from their own lines if they have any, else from their voice type's lines (the voice type's clip stays as it is).
        The line their voice type already uses is left out, or the new clip would sound the same. count: as for ask_for"""
        shared_line = self.__store.info(VOICE_TYPES, voice_key(voice_type)).line if self.__store.voice_type_clip(voice_type) else ""
        self.__queue(ClipRequest(self.__new_id(), OWN, voice_type, npc_plugin, npc_local_id, npc_name, voice_type_lines=True, npc_lines="always",
                                 exclude=[shared_line] if shared_line else [], keys=[f"{NPCS}/{npc_key(npc_plugin, npc_local_id)}"],
                                 count=max(1, count)))

    def ask_for_more(self, kind: str, key: str, count: int = LINES_TO_HEAR, again: bool = False) -> None:
        """Find more clips: *count* new lines (1 to MOST_MORE), which take the place of the ones not chosen. Lines the voice has,
        or had before, are left out; when the game has none left, it is asked once more with only the lines the voice has left out (again)"""
        if again:
            self.__store.bring_back_turned_down(kind, key)
        request = self.__lines_request(kind, key, MORE, max(1, min(int(count), MOST_MORE)))
        request.again = again
        with self.__lock:
            self.__more_asked.add(f"{kind}/{key}")
            self.__no_more.discard(f"{kind}/{key}")
        self.__queue(request)

    def ask_to_top_up(self, kind: str, key: str, count: int) -> bool:
        """The voice is shorter than the service speaking wants: *count* more lines, added to its chosen clips. At most once per
        voice per run. True when asked"""
        name = f"{kind}/{key}"
        with self.__lock:
            if name in self.__topped_up or count <= 0:
                return False
            self.__topped_up.add(name)
        self.__queue(self.__lines_request(kind, key, TOP_UP, count))
        logging.info(f"Voice clips: asking the game for {count} more lines for {self.__store.info(kind, key).name or key}, so the voice is long enough")
        return True

    def __lines_request(self, kind: str, key: str, why: str, count: int) -> ClipRequest:
        """New lines for a voice: not the ones it has, nor the ones it left out"""
        info = self.__store.info(kind, key)
        exclude = list(dict.fromkeys(self.__store.turn_down_all(kind, key) + [line_id(c.path) for _, c in self.__store.candidates(kind, key)]))
        if kind == NPCS:
            # Their voice type's lines too: an NPC whose clip was made from those has no lines of their own to pick from
            return ClipRequest(self.__new_id(), why, info.voice_type, info.npc_plugin, info.npc_local_id, info.name,
                               voice_type_lines=True, npc_lines="always", exclude=exclude, keys=[f"{kind}/{key}"], count=count)
        return ClipRequest(self.__new_id(), why, info.name or key, voice_type_lines=True, npc_lines="none", exclude=exclude, keys=[f"{kind}/{key}"],
                           count=count)

    def waiting_for_more(self, kind: str, key: str) -> bool:
        """The player asked for more lines and the game hasn't answered yet"""
        return f"{kind}/{key}" in self.__more_asked

    def no_more_lines(self, kind: str, key: str) -> bool:
        """The game had no lines left to send, the last time the player asked for more"""
        return f"{kind}/{key}" in self.__no_more

    def ask_for_lines(self, why: str, lines: list[dict[str, str]], voice_type: str = "", npc_plugin: str = "", npc_local_id: str = "", npc_name: str = "",
                      turned_down: list[str] | None = None) -> None:
        """Copies exact game lines: to notice a voice replacer (refresh), or someone's exported picks (import, with their turned-down lines)"""
        self.__queue(ClipRequest(self.__new_id(), why, voice_type, npc_plugin, npc_local_id, npc_name, exclude=list(turned_down or []), lines=lines))

    def ask_for_voice_type_list(self) -> None:
        """Prepare all voices: asks the game which voice types its NPCs use. The answer goes to on_voice_types"""
        self.__queue(ClipRequest(self.__new_id(), LIST))

    def ask_for_voice_types_in_turn(self, voice_types: list[str]) -> None:
        """Clips for many voice types, a few at a time, so the game isn't asked for hundreds at once"""
        with self.__lock:
            known = set(self.__in_turn)
            self.__in_turn += [v for v in voice_types if v not in known]

    def voice_type_settled(self, voice_type: str | None) -> bool:
        """The voice type has a clip, or the game has no lines for it"""
        return bool(self.__store.voice_type_clip(voice_type)) or f"{VOICE_TYPES}/{voice_key(voice_type)}" in self.__nothing_found

    def __take_turns(self) -> None:
        now = time.time()
        for request_id, sent in list(self.__in_turn_sent.items()):
            if now - sent > IN_TURN_LOST_SECONDS:
                del self.__in_turn_sent[request_id]
        while self.__in_turn and len(self.__in_turn_sent) < IN_TURN_AT_ONCE:
            voice_type = self.__in_turn.pop(0)
            key = f"{VOICE_TYPES}/{voice_key(voice_type)}"
            if self.voice_type_settled(voice_type) or not self.__can_ask(key):
                continue
            # One line each: lines to hear are asked for when a player wants another, so the game reads and copies a quarter as much
            request = ClipRequest(self.__new_id(), FIRST, voice_type, voice_type_lines=True, npc_lines="none", keys=[key], count=1)
            self.__waiting.append(request)
            self.__in_turn_sent[request.id] = now

    def requests_for_reply(self) -> list[str]:
        """The requests to put on Mantella's next reply to the game, one JSON text each, for the add-on to hand to the DLL"""
        with self.__lock:
            self.__take_turns()
            waiting, self.__waiting = self.__waiting, []
        sent: list[str] = []
        for request in waiting:
            dest = os.path.join(self.incoming_folder, request.id)
            try: # one bad request (say, a damaged NPC ID in an imported picks file) must not fail the reply it rides on
                for_game = json.dumps(request.to_game(dest))
                os.makedirs(dest, exist_ok=True)
                with open(os.path.join(dest, REQUEST_FILE), "w", encoding="utf-8") as file:
                    json.dump(request.__dict__, file, indent=2)
            except Exception as e:
                logging.warning(f"Voice clips: left out the request {request.id} for {request.npc_name or request.voice_type}: {e}")
                continue
            sent.append(for_game)
            if request.why == FIRST:
                with self.__lock:
                    for key in request.keys:
                        self.__on_the_way[key] = request.id
            logging.debug(f"Voice clips: asking the game for {request.why} lines ({request.id}: {', '.join(request.keys) or len(request.lines)})")
        return sent

    # ---- taking in ----

    def collect(self) -> int:
        """Takes in every delivered request. Returns how many clips changed"""
        folder = self.incoming_folder
        if not os.path.isdir(folder):
            return 0
        changed = 0
        taken = False
        for request_id in sorted(os.listdir(folder)):
            request_folder = os.path.join(folder, request_id)
            if not os.path.exists(os.path.join(request_folder, RESULT_FILE)):
                self.__drop_if_stale(request_folder)
                continue
            with self.__lock:
                if not os.path.isdir(request_folder):
                    continue # another thread took it in
                try:
                    changed += self.__take_in(request_folder)
                except Exception as e:
                    logging.warning(f"Voice clips: could not take in {request_id}: {e}")
                shutil.rmtree(request_folder, ignore_errors=True)
                self.__in_turn_sent.pop(request_id, None)
                for key in [k for k, v in self.__on_the_way.items() if v == request_id]:
                    del self.__on_the_way[key]
                taken = True
        if taken:
            with self.__arrival:
                self.__arrival.notify_all()
            if self.on_arrival is not None:
                self.on_arrival()
        return changed

    @staticmethod
    def __drop_if_stale(request_folder: str) -> None:
        try:
            if time.time() - os.path.getmtime(request_folder) > STALE_SECONDS:
                shutil.rmtree(request_folder, ignore_errors=True)
        except OSError:
            pass

    def __take_in(self, request_folder: str) -> int:
        with open(os.path.join(request_folder, RESULT_FILE), encoding="utf-8") as file:
            result = json.load(file)
        try:
            with open(os.path.join(request_folder, REQUEST_FILE), encoding="utf-8") as file:
                request = ClipRequest(**json.load(file))
        except (OSError, ValueError, TypeError):
            logging.warning(f"Voice clips: {os.path.basename(request_folder)} came back, but Mantella has no record of asking for it")
            return 0
        if request.why == LIST:
            voice_types = [str(v.get("name", "")) for v in result.get("voice_types", []) if v.get("name")]
            logging.info(f"Voice clips: the game's NPCs use {len(voice_types)} voice types with lines")
            if self.on_voice_types is not None:
                self.on_voice_types(voice_types)
            return 0
        delivered = result.get("clips", [])
        for key in request.keys:
            kind = key.split("/", 1)[0]
            if any(c.get("for") == ("npc" if kind == NPCS else "voice_type") for c in delivered):
                self.__asked.pop(key, None) # otherwise it is asked for again only after ASK_AGAIN_SECONDS
        if result.get("status") != "error" and request.npc_lines == "if_different":
            self.__store.mark_npc_checked(npc_key(request.npc_plugin, request.npc_local_id))
        if result.get("status") == "error":
            logging.warning(f"Voice clips: the game could not copy lines for {request.voice_type or request.npc_name}: {result.get('message', '')}")
        if request.why == MORE:
            for key in request.keys:
                kind, _, clip_key = key.partition("/")
                if not delivered and result.get("status") != "error" and not request.again and self.__store.info(kind, clip_key).turned_down:
                    self.ask_for_more(kind, clip_key, request.count, again=True) # no new lines left: the ones skipped before come back
                    continue
                self.__more_asked.discard(key)
                if not delivered and result.get("status") != "error":
                    self.__no_more.add(key) # an NPC with no lines of their own gets voice type lines, so any line delivered counts
        changed = 0
        for kind, for_value in ((VOICE_TYPES, "voice_type"), (NPCS, "npc")):
            clips = [c for c in delivered if c.get("for") == for_value]
            if self.__for_npc_only(request):
                if kind == VOICE_TYPES:
                    continue # the voice type's clip is not what was asked for
                # An NPC with no lines of their own gets lines of their voice type, as a clip of their own
                clips = clips or [dict(c, **{"for": "npc"}) for c in delivered if c.get("for") == "voice_type"]
            if kind == NPCS:
                key = npc_key(request.npc_plugin, request.npc_local_id)
            else:
                key = voice_key(request.voice_type)
            if not key or not clips:
                continue
            converted = self.__convert(request_folder, clips)
            if converted:
                changed += self.__file(kind, key, request, converted)
        for missing in result.get("missing", []):
            self.__line_gone(request, missing)
        type_key = f"{VOICE_TYPES}/{voice_key(request.voice_type)}"
        if (request.why == FIRST and type_key in request.keys and result.get("status") != "error"
                and not any(c.get("for") == "voice_type" for c in delivered)):
            self.__nothing_found.add(type_key)
            logging.info(f"Voice clips: the game has no lines for {request.voice_type}" + (f", so {request.stand_ins[0]} stands in" if request.stand_ins else ""))
            if request.stand_ins:
                # Goes with the reply to this delivery, so the stand-in arrives before the NPC's next line
                self.ask_for(request.stand_ins[0], stand_ins=request.stand_ins[1:], count=request.count)
        return changed

    @staticmethod
    def __convert(request_folder: str, clips: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
        converted: list[tuple[str, dict[str, Any]]] = []
        for clip in clips:
            source = os.path.join(request_folder, os.path.basename(str(clip.get("file", ""))))
            wav = source + ".wav"
            try:
                fuz.to_wav(source, wav)
                converted.append((wav, clip))
            except Exception as e:
                logging.warning(f"Voice clips: could not read the game line {clip.get('path')}: {e}")
        return converted

    def __file(self, kind: str, key: str, request: ClipRequest, converted: list[tuple[str, dict[str, Any]]]) -> int:
        """Puts the delivered lines where they belong: the clip, the chosen clips below it, or the voice's other lines"""
        current = self.__store.info(kind, key)
        has_clip = self.__store.clip(kind, key) is not None
        def info_for(clip: dict[str, Any], how: str) -> ClipInfo:
            return ClipInfo(name=request.npc_name if kind == NPCS else request.voice_type, how=how, path=str(clip.get("path", "")),
                            plugin=str(clip.get("plugin", "")), info=str(clip.get("info", "")), size=int(clip.get("size", 0) or 0),
                            source=str(clip.get("source", "")), seconds=float(clip.get("seconds", 0) or 0),
                            turned_down=list(dict.fromkeys(list(current.turned_down) + (request.exclude if request.why == IMPORT else []))),
                            voice_type=request.voice_type,
                            npc_plugin=request.npc_plugin if kind == NPCS else "", npc_local_id=request.npc_local_id if kind == NPCS else "")
        if request.why == REFRESH:
            return self.__refresh_files(kind, key, converted, info_for)
        if request.why == MORE:
            self.__store.drop_other_lines(kind, key)
            self.__add_lines(kind, key, converted, lambda clip: info_for(clip, AUTOMATIC), chosen=False)
            return 0
        if request.why == TOP_UP:
            if not has_clip:
                return 0
            self.__add_lines(kind, key, converted, lambda clip: info_for(clip, AUTOMATIC), chosen=True)
            return 1
        first, rest = converted[0], converted[1:]
        changed = 0
        chosen = True # a new voice: every line is chosen, and each service takes what it can
        if not has_clip or request.why in (OWN, IMPORT, REPLACE):
            how = OWN_LINES if kind == NPCS and request.why == FIRST else AUTOMATIC
            new = info_for(first[1], how)
            if request.why == REPLACE:
                new.hand_picked = current.hand_picked
                chosen = False # the voice keeps the clips chosen below its gone line
            if request.why in (OWN, IMPORT):
                for path, info in self.__store.candidates(kind, key): # the lines chosen before make way for the new ones
                    if info.order:
                        info.order = 0
                        self.__store.set_candidate_info(path, info)
            self.__store.set_clip(kind, key, first[0], new)
            self.__refreshed.add(f"{kind}/{key}")
            logging.info(f"Voice clips: {request.npc_name if kind == NPCS else request.voice_type} now speaks with the game line {first[1].get('path')}")
            changed = 1
        elif self.__store.candidates(kind, key):
            return 0 # a check for another NPC of this voice type: the voice already has its lines
        else:
            rest = converted # a voice made with one line (Prepare all voices): the others join it
            chosen = not current.hand_picked
            changed = 1 if chosen and rest else 0
        self.__add_lines(kind, key, rest, lambda clip: info_for(clip, AUTOMATIC), chosen=chosen)
        return changed

    def __add_lines(self, kind: str, key: str, lines: list[tuple[str, dict[str, Any]]], info_for: Any, chosen: bool) -> None:
        """Each game line once, chosen at the end of the voice's clips or not. The game splits a long line into numbered files
        (_1, _2, ...) that are one line to Mantella, so another file of the clip's own line, or of a line the voice has, is left out"""
        clip_line = self.__store.info(kind, key).line
        held = {info.line: (path, info) for path, info in self.__store.candidates(kind, key)}
        order = max([info.order for _, info in held.values()] + [0])
        added: set[str] = set()
        for wav, clip in lines:
            line = line_id(str(clip.get("path", "")))
            if line == clip_line or line in added:
                continue
            added.add(line)
            if line in held:
                if chosen and held[line][1].order <= 0 and order < MOST_CHOSEN - 1: # someone's voices file chose a line this voice has
                    order += 1
                    held[line][1].order = order
                    self.__store.set_candidate_info(*held[line])
                continue
            info = info_for(clip)
            if chosen and order < MOST_CHOSEN - 1:
                order += 1
                info.order = order
            self.__store.add_candidate(kind, key, wav, info)

    def __refresh_files(self, kind: str, key: str, converted: list[tuple[str, dict[str, Any]]], info_for: Any) -> int:
        """The voice's lines copied again: a file that changed (a voice replacer installed since) takes the place of the old one"""
        current = self.__store.info(kind, key)
        lines = {info.path.lower(): (path, info) for path, info in self.__store.candidates(kind, key)}
        changed = 0
        for wav, clip in converted:
            game_path = str(clip.get("path", "")).lower()
            new_file = (int(clip.get("size", 0) or 0), str(clip.get("source", "")))
            if game_path == current.path.lower():
                if self.__store.clip(kind, key) is not None and new_file == (current.size, current.source):
                    continue
                new = info_for(clip, current.how)
                new.hand_picked = current.hand_picked
                self.__store.set_clip(kind, key, wav, new)
                changed = 1
            elif game_path in lines:
                path, old = lines[game_path]
                if new_file == (old.size, old.source):
                    continue
                new = info_for(clip, old.how)
                new.order, new.turned_down = old.order, []
                self.__store.add_candidate(kind, key, wav, new)
                changed = 1 if old.order else changed
            else:
                continue
            logging.info(f"Voice clips: the game line {clip.get('path')} for {current.name or key} changed (a voice mod?), so it was copied again")
        return changed

    @staticmethod
    def __for_npc_only(request: ClipRequest) -> bool:
        """A request for one NPC's own clip, where voice type lines stand in when the NPC has no lines of their own"""
        return request.why == OWN or (request.why in (REPLACE, MORE, TOP_UP) and bool(request.npc_plugin and request.npc_local_id))

    def __line_gone(self, request: ClipRequest, path: str) -> None:
        """A voice's game line no longer exists: one of its other lines goes; for the clip itself, another line is picked"""
        if request.why != REFRESH:
            return
        for_npc = bool(request.npc_plugin and request.npc_local_id)
        kind, key = (NPCS, npc_key(request.npc_plugin, request.npc_local_id)) if for_npc else (VOICE_TYPES, voice_key(request.voice_type))
        if line_id(path) != self.__store.info(kind, key).line:
            gone = [p for p, info in self.__store.candidates(kind, key) if info.line == line_id(path)]
            if gone:
                self.__store.drop_line(kind, key, gone[0])
                logging.info(f"Voice clips: the game line {path} is gone, so the voice no longer uses it")
            return
        logging.info(f"Voice clips: the game line {path} is gone, so another one is picked")
        exclude = list(dict.fromkeys(self.__store.turn_down_all(kind, key) + [line_id(path)]))
        self.__queue(ClipRequest(self.__new_id(), REPLACE, request.voice_type, request.npc_plugin, request.npc_local_id, request.npc_name,
                                 voice_type_lines=True, npc_lines="always" if for_npc else "none", exclude=exclude, keys=[]))

    # ---- inside ----

    def __refresh(self, kind: str, key: str) -> None:
        """Once per run, the game is asked whether a clip's line changed (a voice replacer installed since)"""
        name = f"{kind}/{key}"
        with self.__lock:
            if name in self.__refreshed:
                return
            self.__refreshed.add(name)
        info = self.__store.info(kind, key)
        if not info.path or not self.__store.clip(kind, key):
            return
        for_value = "npc" if kind == NPCS else "voice_type"
        lines = [{"path": line.path, "for": for_value} for _, line in self.__store.voice_lines(kind, key) if line.path]
        self.ask_for_lines(REFRESH, lines, info.voice_type or info.name, info.npc_plugin, info.npc_local_id, info.name)

    def __can_ask(self, key: str) -> bool:
        asked = self.__asked.get(key)
        if asked is not None and time.time() - asked < ASK_AGAIN_SECONDS:
            return False
        self.__asked[key] = time.time()
        return True

    def __queue(self, request: ClipRequest) -> None:
        with self.__lock:
            self.__waiting.append(request)

    def __new_id(self) -> str:
        return f"vc{int(time.time())}_{next(self.__counter)}"


_shared: dict[str, ClipRequests] = {}
_shared_lock = threading.Lock()


def clip_requests_for(store: ClipStore) -> ClipRequests:
    """One set of requests per voice clips folder, shared by every service and the reply to the game"""
    with _shared_lock:
        found = _shared.get(store.folder)
        if found is None:
            found = ClipRequests(store)
            _shared[store.folder] = found
        return found
