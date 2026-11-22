"""End-to-end tests of the Conversation Wheel events on the Mantella side: whole conversations, with the requests the game sends and the
replies it reads, and made-up models. See docs/conversation_wheel.md, "Automated tests".

FakeGame plays Mantella-Spell's part as MantellaConversation.psc and MantellaListenerScript.psc do: it starts conversations, picks events,
adds the people the Game Master brings in, switches Shout and steps into NPC Events. FakeModel stands in for every model, with the Game
Master's picks and the events set by each test. No AI, TTS or speech recognition is called.
What only the game can show (the wheel, menus, notes on screen, who is really nearby) still needs a short check in the game.
"""
import json
import logging
import threading
import time
import wave
from dataclasses import dataclass
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from src.config.config_loader import ConfigLoader
from src.conversation.bystanders import ShoutState
from src.conversation.conversation_type import shout
from src.conversation.npc_event import EVENT_REPLY_FORMAT
from src.game_manager import GameStateManager
from src.games.skyrim import Skyrim
from src.http.communication_constants import communication_constants as comm_consts
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage
from src.output_manager import ChatManager
from src.config.definitions.conversation_definitions import MAX_NPC_TURNS_KINDS, max_npc_turns_identifier

GAME_MASTER_QUESTION = "Who speaks next? Choose one of: "
NPC_LINE = "Aye, I saw that too."
EVENT_TEXT = "A cart loses a wheel right outside, spilling cabbages everywhere."


@dataclass(frozen=True)
class Person:
    name: str
    base_id: int
    ref_id: int
    distance: float = 0 # Meters from the player. The game lists people nearest first
    gender: int = 0
    is_player: bool = False

    def actor(self) -> dict[str, Any]:
        """The full actor settings, as buildActorSetting sends them"""
        values: dict[str, Any] = {
            comm_consts.KEY_ACTOR_BASEID: self.base_id, comm_consts.KEY_ACTOR_REFID: self.ref_id, comm_consts.KEY_ACTOR_NAME: self.name,
            comm_consts.KEY_ACTOR_GENDER: self.gender, comm_consts.KEY_ACTOR_RACE: "[Race <NordRace (00013746)>]",
            comm_consts.KEY_ACTOR_VOICETYPE: "[VoiceType <MaleEvenToned (00013AD2)>]", comm_consts.KEY_ACTOR_ISPLAYER: self.is_player,
            comm_consts.KEY_ACTOR_ISENEMY: False, comm_consts.KEY_ACTOR_ISINCOMBAT: False, comm_consts.KEY_ACTOR_RELATIONSHIPRANK: 0}
        if self.is_player:
            values[comm_consts.KEY_ACTOR_CUSTOMVALUES] = {comm_consts.KEY_ACTOR_PC_DESCRIPTION: "", comm_consts.KEY_ACTOR_PC_VOICEPLAYERINPUT: False}
        return values


# People in the Bannered Mare, as in the characters CSV
PLAYER = Person("Prisoner", 0x000007, 0x000014, is_player=True)
HULDA = Person("Hulda", 0x013BA3, 0x01A66E, 1, gender=1)
MIKAEL = Person("Mikael", 0x01A670, 0x01A671, 2)
YSOLDA = Person("Ysolda", 0x013BAB, 0x01A69A, 3, gender=1)
SAADIA = Person("Saadia", 0x013BA2, 0x0D7505, 4, gender=1)
OLFINA = Person("Olfina Gray-Mane", 0x013B9B, 0x01A685, 5, gender=1)


class FakeModel:
    """Stands in for every model: the NPCs, the Game Master, the event generator and the summaries"""
    model_name = "fake-model"
    service_name = "fake"
    token_limit = 100_000

    def __init__(self):
        self.picks: list[str] = [] # The Game Master's next picks, in order. Without one: the player when offered, otherwise the first choice
        self.also: list[list[str]] = [] # Shout: who else the Game Master says the latest line was spoken to, each time it is asked that. Without one: nobody
        self.after_event: str = "" # The Game Master's first pick once the event is in the conversation, ahead of picks
        self.event = EVENT_TEXT # What the event generator writes. "" for no event
        self.participants: list[str] = [] # NPC Events: who the scene is between
        self.game_master_choices: list[list[str]] = [] # Who the Game Master could pick, each time it was asked
        self.event_requests: list[str] = [] # Everything sent to the event generator, as text
        self.npc_requests: list[str] = [] # Everything sent for each NPC reply, as text
        self.game_master_requests: list[str] = [] # Everything sent to the Game Master, as text
        self.mistakes: list[str] = [] # Picks a test set up that were not offered. Errors in here are caught by the code under test, so they are checked afterwards
        self.hold_event = False # The event generator waits for release, so a test can act while it writes
        self.generating = threading.Event()
        self.release = threading.Event()

    def get_count_tokens(self, text) -> int:
        return len(str(text)) // 4

    def is_too_long(self, messages, token_limit_percent) -> bool:
        return False

    def request_call(self, messages: message_thread, topic_log=None) -> str:
        request = messages.get_messages_of_type(UserMessage)[-1].text
        if request.endswith(EVENT_REPLY_FORMAT):
            return self.__write_event(messages)
        question = next((line for line in request.splitlines() if line.startswith(GAME_MASTER_QUESTION)), None)
        if question is None:
            return "They talked about the day." # A summary
        choices = question[len(GAME_MASTER_QUESTION):].rstrip(".").split(", ")
        self.game_master_choices.append(choices)
        self.game_master_requests.append(str(messages.get_openai_messages()))
        if self.after_event and self.event and self.event in request:
            pick, self.after_event = self.after_event, ""
        elif self.picks:
            pick = self.picks.pop(0)
        else:
            pick = PLAYER.name if PLAYER.name in choices else choices[0]
        if pick not in choices:
            self.mistakes.append(f"{pick} was not offered: {choices}")
        also = self.also.pop(0) if self.also and '"also"' in request else []
        return json.dumps({"next": pick, "also": also, "reason": "test"})

    def __write_event(self, messages: message_thread) -> str:
        self.event_requests.append(str(messages.get_openai_messages()))
        if self.hold_event:
            self.generating.set()
            self.release.wait(10)
        if not self.event:
            return '{"participants": []}'
        return f'{{"participants": {self.participants!r}, "event": "{self.event}"}}'.replace("'", '"')

    async def streaming_call(self, messages: message_thread, is_multi_npc: bool, response_started_at: float | None = None):
        self.npc_requests.append(str(messages.get_openai_messages()))
        yield NPC_LINE


class FakeMicrophone:
    """Stands in for speech recognition. A wait for the player to speak ends with what speak() gives it, or with nothing once cancelled"""

    def __init__(self):
        self.is_listening = False
        self.has_player_spoken = False
        self.waiting = threading.Event() # Set while a request waits for the player to speak
        self.__ready = threading.Event()
        self.__is_cancelled = False
        self.__text = ""

    def start_listening(self, prompt: str = "", names: list[str] | None = None):
        self.is_listening = True

    def stop_listening(self):
        self.is_listening = False

    def cancel_wait(self):
        self.__is_cancelled = True
        self.__ready.set()

    def reset_cancel_wait(self):
        self.__is_cancelled = False

    def speak(self, text: str):
        self.__text = text
        self.__ready.set()

    def get_latest_transcription(self) -> str:
        self.waiting.set()
        self.__ready.wait(10)
        self.__ready.clear()
        self.waiting.clear()
        if self.__is_cancelled:
            self.__is_cancelled = False
            return ""
        text, self.__text = self.__text, ""
        return text


class FakeGame:
    """Plays Mantella-Spell's part, as MantellaConversation.psc and MantellaListenerScript.psc do.
    Each action returns once the game waits for the player, or the conversation has ended"""
    EVENT_MAX_DESCRIBED = {"conversation": 0, "nearby": 6, "random": 6} # The Events page's Most People Described. The other event options are Mantella's settings

    def __init__(self, manager: GameStateManager, around: list[Person] | None = None):
        self.manager = manager
        self.around: list[Person] = around or [] # Everyone near the player who is free to talk
        self.input_type = comm_consts.KEY_INPUTTYPE_TEXT # A test with the microphone sends its requests with listen()
        self.config: ConfigLoader = manager._GameStateManager__config # Mantella's settings, e.g. the Conversation page
        self.event_max_described = dict(self.EVENT_MAX_DESCRIBED)
        self.shout_settings: dict[str, int] = {comm_consts.KEY_SHOUT_MAXDESCRIBED: 6} # The Shout Mode page's settings the game still sends
        self.lines: list[tuple[str, str]] = [] # (speaker, line), as spoken in game
        self.notes: list[str] = [] # What Debug.Notification shows
        self.at_line: dict[int, Callable[[], None]] = {} # Done once that many lines have been spoken, before the next request
        self.joined: list[Person] = [] # Everyone who joined by shouting, also after the conversation ended
        self.cant_join: list[str] = [] # The names of people nearby who can't be added, e.g. as they are in a scene
        self.event_line = 0 # How many lines had been spoken when the last event happened
        self.__queue = threading.Lock() # The route runs the queued requests one at a time
        self.__clear()

    def __clear(self):
        """CleanupConversation"""
        self.participants: list[Person] = []
        self.is_running = False
        self.game_master_mode = False
        self.is_player_turn = False
        self.actors_updated = False
        self.npc_event_id = 0
        self.player_noticeable: bool | None = None
        self.shout_joiners: list[Person] = []
        self.is_room_conversation = False
        self.__clear_shout()
        self.__clear_pending_event()

    def __clear_shout(self):
        self.is_shouting = False
        self.shout_sent: list[Person] = []
        self.add_failed: list[tuple[int, str]] = []

    def __clear_pending_event(self):
        self.pending_kind = ""
        self.event_hint = ""
        self.is_event_turn = False
        self.is_event_sent = False
        self.event_nearby: list[Person] = []
        self.event_sent: list[Person] = []

    # Requests

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            comm_consts.KEY_REQUESTTYPE_STARTCONVERSATION: self.manager.start_conversation,
            comm_consts.KEY_REQUESTTYPE_CONTINUECONVERSATION: self.manager.continue_conversation,
            comm_consts.KEY_REQUESTTYPE_PLAYERINPUT: self.manager.player_input,
            comm_consts.KEY_REQUESTTYPE_CONVERSATIONEVENT: self.manager.conversation_event,
            comm_consts.KEY_REQUESTTYPE_ENDCONVERSATION: self.manager.end_conversation,
        }
        with self.__queue:
            return handlers[request[comm_consts.KEY_REQUESTTYPE]](request)

    def interrupt(self, **values: Any):
        """Not queued: the route handles it right away, while another request may still be running"""
        self.manager.interrupt_conversation({comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_INTERRUPT, **values})

    @staticmethod
    def context() -> dict[str, Any]:
        return {comm_consts.KEY_CONTEXT_LOCATION: "Whiterun", comm_consts.KEY_CONTEXT_TIME: 12, comm_consts.KEY_CONTEXT_INGAMEEVENTS: []}

    def actors(self) -> list[dict[str, Any]]:
        return [person.actor() for person in self.participants]

    def continue_request(self) -> dict[str, Any]:
        """RequestContinueConversation"""
        request: dict[str, Any] = {comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_CONTINUECONVERSATION,
                                   comm_consts.KEY_CONTINUECONVERSATION_TOPICINFOFILE: 1}
        if self.actors_updated:
            request[comm_consts.KEY_ACTORS] = self.actors()
            self.actors_updated = False
        if self.npc_event_id and self.player_noticeable is not None:
            request[comm_consts.KEY_NPCEVENT_PLAYERNOTICEABLE] = self.player_noticeable
        self.add_shout_values(request)
        return request

    def shout_nearby(self) -> list[Person]:
        """MantellaShout.FindNearby: who is around and not in the conversation, nearest first"""
        return sorted((p for p in self.around if p not in self.participants), key=lambda p: p.distance)

    def add_shout_values(self, request: dict[str, Any]):
        """AddShoutValues"""
        request[comm_consts.KEY_SHOUT_ENABLED] = self.is_shouting
        if not self.is_shouting:
            return
        nearby = self.shout_nearby()
        request[comm_consts.KEY_SHOUT_NEARBY_REFIDS] = [0] + [p.ref_id for p in nearby]
        request[comm_consts.KEY_SHOUT_NEARBY_NAMES] = [""] + [p.name for p in nearby]
        new = [p for p in nearby[:self.shout_settings[comm_consts.KEY_SHOUT_MAXDESCRIBED]] if p not in self.shout_sent]
        if new:
            request[comm_consts.KEY_SHOUT_NEARBY_ACTORS] = [p.actor() for p in new]
            self.shout_sent += new
        request.update(self.shout_settings)
        if self.add_failed:
            request[comm_consts.KEY_SHOUT_ADDFAILED_REFIDS] = [refid for refid, _reason in self.add_failed]
            request[comm_consts.KEY_SHOUT_ADDFAILED_REASONS] = [reason for _refid, reason in self.add_failed]
            self.add_failed = []

    # Replies

    def handle(self, reply: dict[str, Any]):
        """ContinueConversation: follows the replies until the game waits for the player, or the conversation ends"""
        next_reply: dict[str, Any] | None = reply
        while next_reply:
            next_reply = self.__next(next_reply)

    def __next(self, reply: dict[str, Any]) -> dict[str, Any] | None:
        reply_type = reply[comm_consts.KEY_REPLYTYPE]
        if comm_consts.KEY_GAMEMASTERMODE in reply:
            self.game_master_mode = reply[comm_consts.KEY_GAMEMASTERMODE]
        if reply_type == comm_consts.KEY_REPLYTTYPE_STARTCONVERSATIONCOMPLETED:
            return self.send(self.continue_request())
        if reply_type == comm_consts.KEY_REPLYTYPE_NPCTALK:
            talk = reply.get(comm_consts.KEY_REPLYTYPE_NPCTALK)
            if talk and talk.get(comm_consts.KEY_ACTOR_LINETOSPEAK):
                self.lines.append((talk[comm_consts.KEY_ACTOR_SPEAKER], talk[comm_consts.KEY_ACTOR_LINETOSPEAK].strip()))
                if len(self.lines) in self.at_line:
                    self.at_line.pop(len(self.lines))()
            return self.send(self.continue_request())
        if reply_type == comm_consts.KEY_REPLYTYPE_PLAYERTALK:
            if self.pending_kind:
                self.is_event_turn = True # The event is sent now, instead of listening to the player
                return self.__send_pending_event()
            self.is_player_turn = True
            return None
        if reply_type == comm_consts.KEY_REPLYTYPE_CONVERSATIONEVENT:
            return self.__on_event_result(reply)
        if reply_type == comm_consts.KEY_REPLYTYPE_SHOUT_ADDACTOR:
            return self.__add_shout_actors(reply[comm_consts.KEY_SHOUT_ADDACTOR_REFIDS])
        if reply_type == comm_consts.KEY_REPLYTYPE_NPCEVENT_ADDACTOR:
            if reply[comm_consts.KEY_NPCEVENT_ADDACTOR_REFID] == PLAYER.ref_id: # MantellaListenerScript.AddNpcEventActor
                self.__add(PLAYER)
            return self.send(self.continue_request())
        if reply_type == comm_consts.KEY_REPLYTYPE_ENDCONVERSATION:
            self.__clear()
            return None
        raise AssertionError(f"Unexpected reply: {reply}")

    def __add(self, person: Person):
        self.participants.append(person)
        self.actors_updated = True

    def refresh_stale_actors(self):
        """RefreshStaleActors, on a tick after a join or leave: everyone's actor settings are built again and sent with the next request"""
        self.actors_updated = True

    def __add_shout_actors(self, refids: list[int]) -> dict[str, Any]:
        """AddShoutActors: everyone who can join is added in one step. The others are reported with the next request"""
        nearby = self.shout_nearby()
        for refid in refids:
            person = next((p for p in nearby if p.ref_id == refid), None)
            if self.is_shouting and person and person.name not in self.cant_join:
                self.__add(person)
                self.shout_joiners.append(person)
                self.joined.append(person)
            else:
                self.add_failed.append((refid, "Shout is off" if not self.is_shouting else "no longer in the nearby list"))
        return self.send(self.continue_request())

    # The player

    def start(self, *npcs: Person):
        """The spell, or the wheel's Talk to my companions for more than one NPC. NPC Events start from npc_event_check"""
        self.participants = ([] if self.npc_event_id else [PLAYER]) + list(npcs)
        self.is_running = True
        request: dict[str, Any] = {comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_STARTCONVERSATION,
                                   comm_consts.KEY_STARTCONVERSATION_WORLDID: PLAYER.name + "Test1", comm_consts.KEY_ACTORS: self.actors(),
                                   comm_consts.KEY_CONTEXT: self.context(), comm_consts.KEY_INPUTTYPE: self.input_type}
        if self.npc_event_id:
            request[comm_consts.KEY_NPCEVENT_ID] = self.npc_event_id
            self.is_shouting = True # An NPC Event runs in Shout from the start
            self.add_shout_values(request)
        self.handle(self.send(request))

    def say_to_room(self, text: str):
        """MantellaShout.SayToRoom: a conversation with only the player, already shouting. The line is sent once it is ready"""
        self.participants = [PLAYER]
        self.is_running = True
        self.is_shouting = True
        self.is_room_conversation = True
        request: dict[str, Any] = {comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_STARTCONVERSATION,
                                   comm_consts.KEY_STARTCONVERSATION_WORLDID: PLAYER.name + "Test1", comm_consts.KEY_ACTORS: self.actors(),
                                   comm_consts.KEY_CONTEXT: self.context(), comm_consts.KEY_INPUTTYPE: self.input_type}
        self.add_shout_values(request)
        self.handle(self.send(request))
        self.say(text)

    def say(self, text: str):
        """sendRequestForPlayerInput"""
        self.is_player_turn = False
        request: dict[str, Any] = {comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_PLAYERINPUT, comm_consts.KEY_REQUESTTYPE_PLAYERINPUT: text,
                                   comm_consts.KEY_ACTORS: self.actors(), comm_consts.KEY_CONTEXT: self.context()}
        self.add_shout_values(request)
        reply = self.send(request)
        if reply[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REQUESTTYPE_TTS: # What the player said into the microphone
            self.say(reply[comm_consts.KEY_TRANSCRIBE])
        else:
            self.handle(reply)

    def listen(self):
        """With the microphone, the player's turn starts with a request that waits for the player to speak (sendRequestForVoiceTranscribe)"""
        self.say("")

    def pick_event(self, kind: str, hint: str = ""):
        """RequestConversationEvent: Conversation Event, Nearby Event or Random Event on the wheel"""
        if not self.is_running or self.pending_kind or PLAYER not in self.participants:
            return
        if not self.game_master_mode:
            self.notes.append("Events need Game Master Mode")
            return
        nearby = [] if kind == "conversation" else self.__event_people(kind)
        if kind == "nearby" and not nearby:
            self.notes.append("Nobody is nearby")
            return
        self.pending_kind, self.event_hint, self.event_nearby = kind, hint, nearby
        self.is_event_sent = False
        self.is_event_turn = self.is_player_turn
        self.is_player_turn = False
        if not self.is_event_turn:
            self.interrupt(**{comm_consts.KEY_EVENT_KIND: kind}) # The NPCs are talking, or the microphone waits for the player
        self.notes.append("An event is generating...")
        if self.is_event_turn:
            self.handle(self.__send_pending_event())

    def __event_people(self, kind: str) -> list[Person]:
        """FindEventNearby. Random: the NPCs in the conversation too, in the same distance order"""
        people = self.shout_nearby()
        if kind == "random":
            people = sorted(people + [p for p in self.participants if not p.is_player], key=lambda p: p.distance)
        return people

    def __send_pending_event(self) -> dict[str, Any] | None:
        """TrySendPendingEvent"""
        if self.is_event_sent or not self.is_running:
            return None
        self.is_event_sent = True
        kind = self.pending_kind
        max_described = self.event_max_described[kind]
        # Papyrus can change a string's case (all strings share one case-insensitive table): the game sent "Random" for "random"
        request: dict[str, Any] = {comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_CONVERSATIONEVENT, comm_consts.KEY_EVENT_KIND: kind.capitalize()}
        if self.event_hint:
            request[comm_consts.KEY_EVENT_HINT] = self.event_hint
        self.add_shout_values(request)
        if kind != "conversation":
            if not self.is_shouting:
                request.update(self.shout_settings) # MantellaSoftware switches Shout on with the event, with these settings
            # AddEventNearby: the full actor settings of the nearest, except people in the conversation and those Shout sent already
            nearby = self.event_nearby
            sent = [p for p in nearby[:max_described] if p not in self.participants and not (self.is_shouting and p in self.shout_sent)]
            request[comm_consts.KEY_EVENT_NEARBY_REFIDS] = [0] + [p.ref_id for p in nearby]
            request[comm_consts.KEY_EVENT_NEARBY_NAMES] = [""] + [p.name for p in nearby]
            request[comm_consts.KEY_EVENT_MAXDESCRIBED] = max_described
            if sent:
                request[comm_consts.KEY_EVENT_NEARBY_ACTORS] = [p.actor() for p in sent]
            self.event_sent = sent
        request[comm_consts.KEY_ACTORS] = self.actors()
        request[comm_consts.KEY_CONTEXT] = self.context()
        return self.send(request)

    def __on_event_result(self, reply: dict[str, Any]) -> dict[str, Any] | None:
        """OnConversationEventResult"""
        kind, sent = self.pending_kind, self.event_sent
        self.__clear_pending_event()
        if not self.is_running or not kind:
            return None # Ended meanwhile
        if not reply[comm_consts.KEY_EVENT_RESULT]:
            self.notes.append("Nothing happened" if self.game_master_mode else "Events need Game Master Mode")
            self.is_player_turn = True
            return None
        self.notes.append("Something happened")
        self.event_line = len(self.lines)
        if kind != "conversation":
            self.shout_sent += [p for p in sent if p not in self.shout_sent] # MantellaSoftware keeps them for Shout too
            self.is_shouting = True
        return self.send(self.continue_request())

    def set_shout(self, is_on: bool, confirm: bool = True):
        """SetShout: Shout or Private on the wheel. confirm is the answer to the Private menu, asked when someone who joined by shouting would leave.
        In a room conversation the first Private keeps everyone, and they count as who the conversation is with from then on"""
        if is_on == self.is_shouting or not self.is_running:
            return
        if self.pending_kind:
            self.notes.append("Wait for the event first")
            return
        if is_on:
            self.is_shouting = True
            self.shout_sent = []
            return
        leaving: list[Person] = []
        if not self.is_room_conversation:
            leaving = [p for p in self.shout_joiners if p in self.participants]
            if leaving and not confirm:
                return
        else:
            self.is_room_conversation = False
            self.shout_joiners = []
        self.__clear_shout()
        for person in leaving: # RemoveActors
            self.participants.remove(person)
            self.actors_updated = True
        self.notes.append("Private: nobody new joins")

    def step_in(self):
        """RequestInterrupt while the player watches an NPC Event: the player joins, then the NPCs stop"""
        if self.npc_event_id and PLAYER not in self.participants:
            self.__add(PLAYER)
        if self.is_running and not self.is_player_turn and not self.pending_kind:
            self.interrupt()

    def end(self):
        """EndConversation"""
        if self.pending_kind:
            self.interrupt(**{comm_consts.KEY_EVENT_CANCEL: True}) # Stops the event being generated
            self.__clear_pending_event()
        self.is_running = False
        self.handle(self.send({comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_ENDCONVERSATION}))

    def npc_event_check(self, max_described: int = 6):
        """MantellaListenerScript.RunNpcEventCheck and OnNpcEventResult: everyone nearby by name, the nearest in full"""
        candidates = sorted(self.around, key=lambda p: p.distance)
        described = candidates[:max_described]
        reply = self.manager.npc_event_check({
            comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_NPCEVENT, comm_consts.KEY_STARTCONVERSATION_WORLDID: PLAYER.name + "Test1",
            comm_consts.KEY_ACTORS: [PLAYER.actor()] + [p.actor() for p in described],
            comm_consts.KEY_NPCEVENT_NEARBY_REFIDS: [p.ref_id for p in candidates], comm_consts.KEY_NPCEVENT_NEARBY_NAMES: [p.name for p in candidates],
            comm_consts.KEY_CONTEXT: {comm_consts.KEY_CONTEXT_LOCATION: "Whiterun", comm_consts.KEY_CONTEXT_TIME: 12}})
        refids = reply[comm_consts.KEY_NPCEVENT_PARTICIPANT_REFIDS]
        if not reply[comm_consts.KEY_NPCEVENT_ID] or len(refids) < 2:
            self.notes.append("NPC Event: nothing happens")
            return
        self.npc_event_id = reply[comm_consts.KEY_NPCEVENT_ID]
        self.start(*[next(p for p in candidates if p.ref_id == refid) for refid in refids])

    def speakers(self, since: int = 0) -> list[str]:
        return [speaker for speaker, _ in self.lines[since:]]


def silent_wav(folder) -> str:
    """A voice line without sound, so NPC lines need no waiting"""
    path = str(folder / "silence.wav")
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(22050)
    return path


@pytest.fixture
def model() -> FakeModel:
    return FakeModel()


@pytest.fixture
def manager(skyrim: Skyrim, default_config: ConfigLoader, english_language_info, model: FakeModel, tmp_path, monkeypatch):
    config = default_config
    config.multi_npc_game_master_mode = True
    config.game_master_use_decision_model = False
    config.wait_time_buffer = 0
    for factory in ("create_summary_client", "create_thoughts_client", "create_reflection_client"):
        monkeypatch.setattr(GameStateManager, factory, staticmethod(lambda *args, **kwargs: None))
    monkeypatch.setattr(GameStateManager, "create_game_master_client", staticmethod(lambda config, *args, **kwargs: model if config.multi_npc_game_master_mode else None))
    monkeypatch.setattr(skyrim, "prepare_sentence_for_game", lambda *args, **kwargs: None) # No voice files to copy into the game
    tts = MagicMock()
    tts.synthesize.return_value = silent_wav(tmp_path)
    chat_manager = ChatManager(config, tts, model, None, "secret_keys.json")
    game_manager = GameStateManager(skyrim, chat_manager, config, english_language_info, model, None, "STT_SECRET_KEY.txt", "GPT_SECRET_KEY.txt")
    chat_manager.update_multi_npc_client(None) # The fake model for every conversation
    game_manager._GameStateManager__get_npc_event_client = lambda: model
    game_manager._GameStateManager__get_npc_initiative_client = lambda: model
    yield game_manager
    talk = game_manager._GameStateManager__talk
    if talk:
        talk.end() # Stops anything still generating
    assert not model.mistakes


@pytest.fixture
def game(manager: GameStateManager) -> FakeGame:
    return FakeGame(manager, [YSOLDA, SAADIA, OLFINA])


def shout_state(manager: GameStateManager) -> ShoutState | None:
    talk = manager._GameStateManager__talk
    return talk.context.shout if talk and isinstance(talk.context.shout, ShoutState) else None


def in_conversation(manager: GameStateManager) -> list[str]:
    return manager._GameStateManager__talk.context.npcs_in_conversation.get_all_names()


# Conversation Event

def test_one_on_one_conversation_event_the_npc_reacts(game: FakeGame, model: FakeModel, caplog):
    game.start(HULDA)
    assert game.is_player_turn and game.game_master_mode
    asked_before = len(model.game_master_choices)
    model.picks = [HULDA.name]
    with caplog.at_level(logging.DEBUG):
        game.pick_event("conversation", hint="Something funny")

    assert game.notes[-1] == "Something happened"
    assert "Event (conversation) about: Hulda, Prisoner. With bios and memories: Hulda. Hint: Something funny" in caplog.text
    assert "The conversation so far:" in model.event_requests[-1] and "Something funny" in model.event_requests[-1]
    assert not any("Something funny" in request for request in model.npc_requests + model.game_master_requests) # Only the event generator gets the hint
    assert model.game_master_choices[asked_before:] == [[HULDA.name, PLAYER.name]] # Asked once who reacts first, the player included
    assert game.speakers(game.event_line) == [HULDA.name]
    assert EVENT_TEXT in model.npc_requests[-1]
    assert game.is_player_turn and not shout_state(game.manager)


def test_one_on_one_conversation_event_can_give_the_player_the_turn(game: FakeGame, model: FakeModel):
    game.start(HULDA)
    model.picks = [PLAYER.name]
    game.pick_event("conversation")

    assert game.notes[-1] == "Something happened"
    assert game.speakers(game.event_line) == [] and game.is_player_turn
    game.say("Did you see that?")
    assert EVENT_TEXT in model.npc_requests[-1] # The narration is in the conversation
    assert game.speakers(game.event_line) == [HULDA.name]


def test_group_conversation_event_the_game_master_picks_who_reacts_first(game: FakeGame, model: FakeModel):
    game.start(HULDA, MIKAEL)
    assert game.is_player_turn
    asked_before = len(model.game_master_choices)
    model.picks = [MIKAEL.name]
    game.pick_event("conversation")

    assert game.notes[-1] == "Something happened"
    assert model.game_master_choices[asked_before] == [HULDA.name, MIKAEL.name, PLAYER.name] # The player can be picked first
    assert game.speakers(game.event_line) == [MIKAEL.name]
    assert EVENT_TEXT in model.npc_requests[-1]
    assert game.is_player_turn


# Nearby and Random Events

def test_nearby_event_switches_shout_on_with_the_games_settings_and_someone_nearby_joins(game: FakeGame, model: FakeModel, caplog):
    game.event_max_described["nearby"] = 1
    game.config.shout_max_people = 5
    game.shout_settings[comm_consts.KEY_SHOUT_MAXDESCRIBED] = 4
    game.start(HULDA)
    asked_before = len(model.game_master_choices)
    model.picks = [YSOLDA.name]
    with caplog.at_level(logging.DEBUG):
        game.pick_event("nearby")

    assert game.notes[-1] == "Something happened"
    assert "Event (nearby) about: Ysolda, Saadia, Olfina Gray-Mane. With bios and memories: Ysolda." in caplog.text
    assert "The conversation so far:" not in model.event_requests[-1] # Send the Conversation So Far is off for Nearby Events
    state = shout_state(game.manager)
    assert state and (state.max_people, state.max_described) == (5, 4)
    assert model.game_master_choices[asked_before] == [HULDA.name, YSOLDA.name, SAADIA.name, OLFINA.name, PLAYER.name]
    assert game.joined == [YSOLDA] and YSOLDA.name in in_conversation(game.manager)
    assert game.speakers(game.event_line) == [YSOLDA.name] # Who joins speaks first
    # Newcomers Remember is Since Shout: Ysolda heard the event, as Shout was switched on before it was added
    assert EVENT_TEXT in model.npc_requests[-1]
    assert game.is_player_turn


def test_everyone_a_nearby_event_is_about_reacts_before_the_player_even_when_the_game_master_picks_the_player(game: FakeGame, model: FakeModel):
    """The Game Master gives the player the turn each time, but Ysolda and Saadia are part of the event: both react first"""
    game.start(HULDA)
    since = len(game.lines)
    model.picks, model.also = [PLAYER.name, PLAYER.name, PLAYER.name], [[YSOLDA.name, SAADIA.name]]
    game.pick_event("nearby")

    assert sorted(game.joined, key=lambda person: person.name) == [SAADIA, YSOLDA]
    assert sorted(speakers(game, since)) == [SAADIA.name, YSOLDA.name]
    assert game.is_player_turn


def test_everyone_a_nearby_event_is_about_reacts_past_the_reply_limit(game: FakeGame, model: FakeModel, default_config: ConfigLoader):
    for each_kind in MAX_NPC_TURNS_KINDS: # Each kind of conversation has its own
        setattr(default_config, max_npc_turns_identifier(each_kind), 1)
    default_config.extra_rounds = 20 # Enough extra rounds for all three (the default is one)
    game.start(HULDA)
    since = len(game.lines)
    model.picks, model.also = [PLAYER.name], [[YSOLDA.name, SAADIA.name, OLFINA.name]]
    game.pick_event("nearby")

    assert sorted(speakers(game, since)) == [OLFINA.name, SAADIA.name, YSOLDA.name]
    assert game.is_player_turn


def test_actors_built_again_after_a_join_change_nothing_in_the_conversation(game: FakeGame, model: FakeModel):
    game.start(HULDA)
    model.picks = [YSOLDA.name]
    game.pick_event("nearby")
    assert game.joined == [YSOLDA]
    # Ysolda joined to react to the event: she reacts to it, she doesn't walk over to the people talking
    joined_notes = model.npc_requests[-1].count("Ysolda reacts to what just happened")
    assert joined_notes >= 1
    assert "joins the conversation" not in model.npc_requests[-1]
    game.refresh_stale_actors() # The game builds again the actor settings kept by Ysolda's join, and sends them with the next request
    game.say("What happened?")
    assert sorted(in_conversation(game.manager)) == sorted([HULDA.name, YSOLDA.name, PLAYER.name])
    assert model.npc_requests[-1].count("Ysolda reacts to what just happened") == joined_notes # Nobody joins again
    assert "joins the conversation" not in model.npc_requests[-1]
    assert game.joined == [YSOLDA] and game.is_player_turn


@pytest.mark.parametrize("max_people, expected", [
    (2, [HULDA.name, PLAYER.name]), # Hulda and the player: nobody else can join
    (6, [HULDA.name, YSOLDA.name, SAADIA.name, OLFINA.name, PLAYER.name]),
])
def test_shouts_most_people_in_conversation_applies_to_who_reacts_first_to_a_conversation_event(game: FakeGame, model: FakeModel, max_people, expected):
    game.config.shout_max_people = max_people # Mantella's setting (Conversation page): the game no longer sends it
    game.start(HULDA)
    game.set_shout(True)
    asked_before = len(model.game_master_choices)
    game.pick_event("conversation")

    assert game.notes[-1] == "Something happened"
    assert model.game_master_choices[asked_before] == expected


def test_the_people_nearby_a_nearby_event_is_about_join_past_shouts_most_people_in_conversation(game: FakeGame, model: FakeModel):
    game.config.shout_max_people = 2
    game.start(HULDA)
    asked_before = len(model.game_master_choices)
    game.pick_event("nearby")

    assert game.notes[-1] == "Something happened"
    assert model.game_master_choices[asked_before] == [HULDA.name, YSOLDA.name, SAADIA.name, OLFINA.name, PLAYER.name]


def test_private_after_a_nearby_event_sends_away_who_joined(game: FakeGame, model: FakeModel):
    game.start(HULDA)
    model.picks = [YSOLDA.name]
    game.pick_event("nearby")
    assert game.shout_joiners == [YSOLDA]

    game.set_shout(False, confirm=False) # No in the Private menu: still shouting
    assert game.is_shouting and YSOLDA in game.participants
    game.set_shout(False, confirm=True)
    model.picks = [HULDA.name]
    game.say("Let's talk alone.")

    assert not shout_state(game.manager)
    assert sorted(in_conversation(game.manager)) == sorted([PLAYER.name, HULDA.name])
    assert "now speaking privately with Hulda" in model.npc_requests[-1]
    assert game.lines[-1][0] == HULDA.name


def test_private_after_shouting_sends_away_who_joined(game: FakeGame, model: FakeModel):
    """No event: the player talks to Hulda and Mikael, shouts, Ysolda joins, and Private brings it back to the three of them"""
    game.start(HULDA, MIKAEL)
    game.set_shout(True)
    model.picks = [YSOLDA.name]
    game.say("Anyone else seen the cart?")
    assert game.shout_joiners == [YSOLDA] and YSOLDA.name in in_conversation(game.manager)

    game.set_shout(False, confirm=True)
    model.picks = [HULDA.name]
    game.say("Back to what we were saying.")

    assert not shout_state(game.manager)
    assert sorted(in_conversation(game.manager)) == sorted([PLAYER.name, HULDA.name, MIKAEL.name])
    assert "now speaking privately with Hulda and Mikael." in model.npc_requests[-1] # Named after the game's update: without Ysolda
    assert game.lines[-1][0] == HULDA.name


def test_private_in_a_room_conversation_keeps_everyone_then_sends_away_who_joins_later(game: FakeGame, model: FakeModel):
    """Saying something to the room: Ysolda answers, and the first Private keeps her. A later Shout brings Saadia, and Private sends Saadia away"""
    model.picks = [YSOLDA.name]
    game.say_to_room("Anyone know the way to Riverwood?")
    assert game.shout_joiners == [YSOLDA] and YSOLDA.name in in_conversation(game.manager)

    game.set_shout(False) # No menu: nobody leaves
    assert YSOLDA in game.participants and not game.is_shouting

    game.set_shout(True)
    model.picks = [SAADIA.name]
    game.say("Anyone else been there?")
    assert game.shout_joiners == [SAADIA]
    game.set_shout(False, confirm=True)
    model.picks = [YSOLDA.name]
    game.say("Just us again.")

    assert sorted(in_conversation(game.manager)) == sorted([PLAYER.name, YSOLDA.name])
    assert "now speaking privately with Ysolda." in model.npc_requests[-1]


def test_random_event_in_a_group_describes_the_nearest_and_offers_everyone(game: FakeGame, model: FakeModel, caplog):
    nearest = Person(YSOLDA.name, YSOLDA.base_id, YSOLDA.ref_id, 0.5, gender=1) # Nearer than anyone in the conversation
    game.around = [nearest, SAADIA]
    game.event_max_described["random"] = 2
    game.start(HULDA, MIKAEL)
    asked_before = len(model.game_master_choices)
    with caplog.at_level(logging.DEBUG):
        game.pick_event("random")

    assert game.notes[-1] == "Something happened"
    assert "Event (random) about: Ysolda, Hulda, Mikael, Saadia, Prisoner. With bios and memories: Ysolda, Hulda." in caplog.text
    assert shout_state(game.manager)
    assert model.game_master_choices[asked_before:] == [[HULDA.name, MIKAEL.name, YSOLDA.name, SAADIA.name, PLAYER.name]]
    assert game.speakers(game.event_line) == [] and game.is_player_turn # The Game Master gave the player the turn


def test_nearby_event_with_nobody_nearby_sends_nothing(game: FakeGame, model: FakeModel):
    game.around = []
    game.start(HULDA)
    game.pick_event("nearby")
    assert game.notes[-1] == "Nobody is nearby" and model.event_requests == []
    assert game.is_player_turn


# Nothing happens

def test_nothing_happened_gives_the_player_the_turn(game: FakeGame, model: FakeModel):
    game.start(HULDA)
    lines_before = len(game.lines)
    model.event = ""
    game.pick_event("random")
    assert game.notes[-1] == "Nothing happened"
    assert game.speakers(lines_before) == [] and game.is_player_turn
    assert not shout_state(game.manager) # Shout is only switched on when something happened


def test_events_need_game_master_mode(game: FakeGame, model: FakeModel, default_config: ConfigLoader):
    game.start(HULDA)
    default_config.multi_npc_game_master_mode = False # Turned off during the conversation: the game still thinks it is on
    game.pick_event("conversation")
    assert game.notes[-1] == "Events need Game Master Mode" and model.event_requests == []
    assert not game.game_master_mode and game.is_player_turn
    game.pick_event("conversation") # The game knows now, so nothing is sent
    assert game.notes[-2:] == ["Events need Game Master Mode", "Events need Game Master Mode"]
    assert model.event_requests == []


def test_ending_while_the_event_is_written_cancels_it(game: FakeGame, model: FakeModel, caplog):
    game.start(HULDA)
    asked_before = len(model.game_master_choices)
    model.hold_event = True
    with caplog.at_level(logging.DEBUG):
        picker = threading.Thread(target=game.pick_event, args=("conversation",))
        picker.start()
        assert model.generating.wait(5)
        ender = threading.Thread(target=game.end)
        ender.start()
        while game.is_running:
            time.sleep(0.01) # The cancel goes out right away. The end request waits for the event request, as in the route's queue
        model.release.set()
        picker.join(5)
        ender.join(5)

    assert "Event cancelled: the conversation ended" in caplog.text
    assert "Something happened" not in game.notes
    assert len(model.game_master_choices) == asked_before # Nobody was asked to react
    assert game.manager._GameStateManager__talk is None


# Switching the microphone on or off during a conversation

def test_the_microphone_switched_on_during_a_conversation_listens_in_the_next_turn(manager: GameStateManager, model: FakeModel):
    game = FakeGame(manager, [YSOLDA])
    game.start(HULDA) # Typing
    microphone = FakeMicrophone()
    manager._GameStateManager__stt = microphone
    game.input_type = comm_consts.KEY_INPUTTYPE_MIC
    typed_continue = game.continue_request
    game.continue_request = lambda: {**typed_continue(), comm_consts.KEY_INPUTTYPE: game.input_type} # The game says so in its next request
    model.picks = [HULDA.name]
    game.say("Hello.")
    assert game.is_player_turn

    model.picks = [HULDA.name]
    listener = threading.Thread(target=game.listen)
    listener.start()
    assert microphone.waiting.wait(5) # Not an empty line sent to Hulda
    microphone.speak("I need your help.")
    listener.join(5)

    assert not listener.is_alive()
    assert "I need your help." in model.npc_requests[-1]


def test_switching_from_push_to_talk_to_the_open_microphone_turns_push_to_talk_off(manager: GameStateManager):
    manager._GameStateManager__stt = FakeMicrophone()
    manager.process_stt_setup({comm_consts.KEY_INPUTTYPE: comm_consts.KEY_INPUTTYPE_PTT})
    assert manager._GameStateManager__mic_ptt
    manager.process_stt_setup({comm_consts.KEY_INPUTTYPE: comm_consts.KEY_INPUTTYPE_MIC})
    assert not manager._GameStateManager__mic_ptt
    assert manager._GameStateManager__mic_input


def test_the_microphone_switched_off_during_a_conversation_takes_typed_lines(manager: GameStateManager, model: FakeModel):
    microphone = FakeMicrophone()
    manager._GameStateManager__stt = microphone
    game = FakeGame(manager, [YSOLDA])
    game.input_type = comm_consts.KEY_INPUTTYPE_MIC
    game.start(HULDA)
    game.input_type = comm_consts.KEY_INPUTTYPE_TEXT
    typed_continue = game.continue_request
    game.continue_request = lambda: {**typed_continue(), comm_consts.KEY_INPUTTYPE: game.input_type}
    model.picks = [HULDA.name]
    listener = threading.Thread(target=game.listen) # Switched off in the menu while the microphone waited: the player still speaks this turn
    listener.start()
    assert microphone.waiting.wait(5)
    microphone.speak("Hello.")
    listener.join(5)
    assert game.is_player_turn

    model.picks = [HULDA.name]
    game.say("Where is the market?")

    assert manager._GameStateManager__talk.stt is None
    assert "Where is the market?" in model.npc_requests[-1]
    assert not microphone.is_listening


# The player's turn before the conversation starts: Automatic Greeting off, so the game lets the player speak while it gathers the details

class SpokenMicrophone(FakeMicrophone):
    """Remembers that the player spoke until it stops listening, as speech recognition does"""
    def stop_listening(self):
        super().stop_listening()
        self.has_player_spoken = False


def listen_before_start_request() -> dict[str, Any]:
    """SendListenBeforeStart"""
    return {comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_LISTENBEFORESTART, comm_consts.KEY_INPUTTYPE: comm_consts.KEY_INPUTTYPE_MIC,
            comm_consts.KEY_LISTEN_NAMES: [HULDA.name, ""], comm_consts.KEY_LISTEN_PLAYERNAME: PLAYER.name}


def test_the_player_speaks_first_without_automatic_greeting(manager: GameStateManager):
    manager._GameStateManager__automatic_greeting = False
    assert manager.player_speaks_first
    manager._GameStateManager__automatic_greeting = True
    assert not manager.player_speaks_first


def test_what_the_player_said_before_the_start_is_their_first_line(manager: GameStateManager, model: FakeModel):
    manager._GameStateManager__automatic_greeting = False
    manager._GameStateManager__config.automatic_greeting = False
    microphone = SpokenMicrophone()
    manager._GameStateManager__stt = microphone
    reply = manager.listen_before_start(listen_before_start_request())
    assert reply[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_LISTENINGBEFORESTART
    assert microphone.is_listening

    microphone.has_player_spoken = True # While the game gathered the details for the start
    microphone.speak("Hello, Hulda.")
    game = FakeGame(manager)
    game.input_type = comm_consts.KEY_INPUTTYPE_MIC
    game.start(HULDA)
    assert game.is_player_turn # Not an interruption: nobody was talking
    assert not model.npc_requests

    game.listen()

    assert "Hello, Hulda." in model.npc_requests[-1]
    assert "Interrupting..." not in model.npc_requests[-1]


def test_listening_before_the_start_stops_when_the_conversation_is_ended_before_it_starts(manager: GameStateManager):
    microphone = FakeMicrophone()
    manager._GameStateManager__stt = microphone
    manager.listen_before_start(listen_before_start_request())
    manager.end_conversation({})
    assert not microphone.is_listening


def test_no_listening_for_a_start_ended_before_the_request_was_handled(manager: GameStateManager):
    microphone = FakeMicrophone()
    manager._GameStateManager__stt = microphone
    ends_before = manager.ends_requested
    manager.stop_conversation_now()
    manager.listen_before_start(listen_before_start_request(), ends_before)
    assert not microphone.is_listening


# Picking an event while the NPCs talk or the microphone waits

def test_an_event_picked_while_the_microphone_waits_is_sent_in_the_players_turn(manager: GameStateManager, model: FakeModel):
    microphone = FakeMicrophone()
    manager._GameStateManager__stt = microphone
    game = FakeGame(manager, [YSOLDA])
    game.input_type = comm_consts.KEY_INPUTTYPE_MIC
    game.start(HULDA)
    assert game.is_player_turn
    model.picks = [HULDA.name]
    listener = threading.Thread(target=game.listen) # The player's turn: the request waits for the player to speak
    listener.start()
    assert microphone.waiting.wait(5)
    game.is_player_turn = False # As in the game: the turn went to the microphone
    game.pick_event("conversation") # Ends the wait, and the event is sent in the player's turn
    listener.join(5) # The microphone would wait 10 seconds

    assert not listener.is_alive()
    assert game.notes[-1] == "Something happened"
    assert game.speakers(game.event_line) == [HULDA.name]
    assert EVENT_TEXT in model.npc_requests[-1]


def test_an_event_picked_while_the_npcs_talk_stops_them(game: FakeGame, model: FakeModel):
    game.start(HULDA, MIKAEL)
    model.picks = [HULDA.name, MIKAEL.name] # Hulda answers and Mikael would go on
    model.after_event = HULDA.name

    def pick_while_hulda_speaks():
        deadline = time.time() + 5
        while len(model.game_master_choices) < asked_before + 2 and time.time() < deadline:
            time.sleep(0.01) # Mikael was picked, and his line is being written
        game.pick_event("conversation")

    asked_before = len(model.game_master_choices)
    lines_before = len(game.lines)
    game.at_line[lines_before + 1] = pick_while_hulda_speaks
    game.say("Tell me about Whiterun.")

    assert game.notes[-2:] == ["An event is generating...", "Something happened"]
    assert game.speakers(lines_before) == [HULDA.name, HULDA.name] # Mikael never spoke. Hulda reacted to the event
    assert game.event_line == lines_before + 1
    assert EVENT_TEXT in model.npc_requests[-1]
    assert game.is_player_turn


def test_an_interruption_that_comes_after_the_npcs_stopped_does_not_stop_the_reaction(game: FakeGame, model: FakeModel):
    game.start(HULDA)
    # The NPC had finished, but the game picked the event before that reply arrived, so it sent the interruption
    game.is_player_turn = False
    model.picks = [HULDA.name]
    game.pick_event("conversation")
    game.handle({comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_PLAYERTALK}) # The reply that was on its way

    assert game.notes[-1] == "Something happened"
    assert game.speakers(game.event_line) == [HULDA.name]


# NPC Events

def test_npc_event_someone_nearby_walks_up_and_the_scene_ends_at_its_line_limit(game: FakeGame, model: FakeModel, default_config: ConfigLoader, caplog):
    default_config.npc_event_max_lines_without_player = 4
    game.around = [HULDA, MIKAEL, YSOLDA, SAADIA]
    model.participants = [HULDA.name, MIKAEL.name]
    model.picks = [HULDA.name, YSOLDA.name]
    with caplog.at_level(logging.DEBUG):
        game.npc_event_check(max_described=3)

    assert "NPC Event check in Whiterun: Hulda, Mikael, Ysolda, Saadia. With bios and memories: Hulda, Mikael, Ysolda" in caplog.text
    assert game.speakers()[:2] == [HULDA.name, YSOLDA.name]
    assert game.joined == [YSOLDA]
    assert EVENT_TEXT in model.npc_requests[1] # Ysolda overheard the whole scene
    assert len(game.lines) == 4 and not game.is_running # Ended at the line limit
    assert game.manager._GameStateManager__talk is None


def test_npc_event_the_player_steps_in_and_can_pick_events(game: FakeGame, model: FakeModel):
    game.around = [HULDA, MIKAEL, YSOLDA]
    model.participants = [HULDA.name, MIKAEL.name]
    model.picks = [HULDA.name, MIKAEL.name]
    game.at_line[2] = game.step_in
    game.npc_event_check()

    assert len(game.lines) == 2 and game.is_player_turn
    assert PLAYER.name in in_conversation(game.manager)
    assert shout_state(game.manager) # After stepping in it is a Shout conversation
    model.event = "A bard starts singing off-key."
    model.picks = [MIKAEL.name]
    game.pick_event("conversation")
    assert game.notes[-1] == "Something happened"
    assert game.speakers(game.event_line) == [MIKAEL.name] and game.is_player_turn


# Shout: several people answer one line

def speakers(game: FakeGame, since: int = 0) -> list[str]:
    return [speaker for speaker, _line in game.lines[since:]]


def test_a_line_to_two_people_nearby_adds_both_in_one_step_and_both_answer(game: FakeGame, model: FakeModel):
    """'Hello Ysolda and Saadia': both join together. Ysolda answers first, and Saadia answers although the Game Master gives the player the turn"""
    game.start(HULDA)
    game.set_shout(True)
    since = len(game.lines)
    model.picks, model.also = [YSOLDA.name, PLAYER.name], [[SAADIA.name]]
    game.say("Hello Ysolda and Saadia!")

    assert game.shout_joiners == [YSOLDA, SAADIA]
    assert speakers(game, since) == [YSOLDA.name, SAADIA.name]
    assert game.is_player_turn and not model.mistakes
    assert not shout_state(game.manager).still_to_answer


def test_the_order_stays_free_between_the_people_who_answer(game: FakeGame, model: FakeModel):
    """Hulda reacts to Ysolda's answer before Saadia answers the player"""
    game.start(HULDA)
    game.set_shout(True)
    since = len(game.lines)
    model.picks, model.also = [YSOLDA.name, HULDA.name, PLAYER.name], [[SAADIA.name]]
    game.say("Hello Ysolda and Saadia!")

    assert speakers(game, since) == [YSOLDA.name, HULDA.name, SAADIA.name]
    assert game.is_player_turn


def test_a_line_to_someone_in_the_conversation_and_someone_nearby(game: FakeGame, model: FakeModel):
    """'Hulda, Ysolda, gather round': Ysolda joins first, then Hulda, the Game Master's pick, speaks first and Ysolda answers after her"""
    game.start(HULDA, MIKAEL)
    game.set_shout(True)
    since = len(game.lines)
    model.picks, model.also = [HULDA.name, PLAYER.name], [[YSOLDA.name]]
    game.say("Hulda, Ysolda, gather round.")

    assert game.shout_joiners == [YSOLDA]
    assert speakers(game, since) == [HULDA.name, YSOLDA.name]


def test_someone_who_cant_join_is_skipped_and_the_rest_answer(game: FakeGame, model: FakeModel):
    game.start(HULDA)
    game.set_shout(True)
    game.cant_join = [YSOLDA.name]
    since = len(game.lines)
    model.picks, model.also = [YSOLDA.name, PLAYER.name], [[SAADIA.name]]
    game.say("Hello Ysolda and Saadia!")

    assert game.shout_joiners == [SAADIA]
    assert speakers(game, since) == [SAADIA.name]
    assert game.is_player_turn and not shout_state(game.manager).still_to_answer


def test_when_nobody_spoken_to_can_join_someone_in_the_conversation_answers(game: FakeGame, model: FakeModel):
    game.start(HULDA)
    game.set_shout(True)
    game.cant_join = [YSOLDA.name, SAADIA.name]
    since = len(game.lines)
    model.picks, model.also = [YSOLDA.name], [[SAADIA.name]]
    game.say("Hello Ysolda and Saadia!")

    assert game.shout_joiners == []
    assert speakers(game, since) == [HULDA.name]
    assert game.is_player_turn


def test_someone_an_npc_speaks_to_joins_but_the_game_master_can_still_give_the_player_the_turn(game: FakeGame, model: FakeModel):
    """Hulda answers the player and turns to Mikael and Ysolda. Ysolda joins and Mikael answers. The Game Master then gives the player the turn:
    Ysolda no longer has to answer Hulda first, so NPCs talking to each other can't keep the player waiting"""
    game.start(HULDA, MIKAEL)
    game.set_shout(True)
    since = len(game.lines)
    # Asked about the player's line (nobody else), then about Hulda's line: Mikael speaks next, and it was spoken to Ysolda too
    model.picks, model.also = [HULDA.name, MIKAEL.name, PLAYER.name], [[], [YSOLDA.name]]
    game.say("Hulda, what's the news?")

    assert game.shout_joiners == [YSOLDA]
    assert speakers(game, since) == [HULDA.name, MIKAEL.name]
    assert game.is_player_turn


def test_at_the_reply_limit_only_who_the_player_spoke_to_still_answers(game: FakeGame, model: FakeModel, default_config: ConfigLoader):
    for each_kind in MAX_NPC_TURNS_KINDS: # Each kind of conversation has its own
        setattr(default_config, max_npc_turns_identifier(each_kind), 1)
    game.start(HULDA, MIKAEL)
    game.set_shout(True)
    since = len(game.lines)
    model.picks, model.also = [HULDA.name], [[MIKAEL.name]]
    game.say("Hulda, Mikael, what's the news?")
    assert speakers(game, since) == [HULDA.name, MIKAEL.name] # Mikael answers past the limit: the player spoke to him

    since = len(game.lines)
    model.picks, model.also = [HULDA.name], [[], [MIKAEL.name]] # Hulda's line is spoken to Mikael... but the limit is reached before it is asked
    game.say("And you, Hulda?")
    assert speakers(game, since) == [HULDA.name]


def test_the_answer_to_a_line_for_several_people_gets_the_group_note_and_is_not_kept(game: FakeGame, model: FakeModel):
    game.start(HULDA, MIKAEL)
    game.set_shout(True)
    model.picks, model.also = [HULDA.name, PLAYER.name], [[MIKAEL.name]]
    game.say("Hulda, Mikael, good evening.")

    hulda_request, mikael_request = model.npc_requests[-2:]
    assert f"{HULDA.name} answers in one or two sentences" in hulda_request
    assert f"{MIKAEL.name} answers in one or two sentences" in mikael_request
    since = len(model.npc_requests)
    model.picks = [HULDA.name, PLAYER.name]
    game.say("Hulda, a mead please.")
    assert all("Keep it short" not in request for request in model.npc_requests[since:])


def test_a_nearby_event_brings_in_everyone_who_is_part_of_it(game: FakeGame, model: FakeModel):
    game.around = [YSOLDA, SAADIA, OLFINA]
    game.start(HULDA)
    since = len(game.lines)
    model.after_event, model.also, model.picks = YSOLDA.name, [[SAADIA.name]], [PLAYER.name]
    game.pick_event("nearby")

    assert game.shout_joiners == [YSOLDA, SAADIA]
    assert speakers(game, since) == [YSOLDA.name, SAADIA.name]
    assert "for whom this is true: that person did it" in model.game_master_requests[-3] # Asked who is part of what happened
    assert not model.mistakes


def test_watching_an_npc_event_people_spoken_to_join_but_nobody_has_to_answer(game: FakeGame, model: FakeModel, default_config: ConfigLoader):
    default_config.npc_event_max_lines_without_player = 2
    game.around = [HULDA, MIKAEL, YSOLDA, SAADIA]
    model.participants = [HULDA.name, MIKAEL.name]
    model.picks, model.also = [HULDA.name, MIKAEL.name], [[YSOLDA.name, SAADIA.name]] # Hulda's line is spoken to Mikael, Ysolda and Saadia
    game.npc_event_check()

    assert game.joined == [YSOLDA, SAADIA] # Both joined in one step
    assert speakers(game) == [HULDA.name, MIKAEL.name] # The Game Master's pick speaks. Then the scene reaches its line limit, and nobody is made to answer
    assert not game.is_running


def test_a_turn_that_fails_while_someone_still_has_to_answer_gives_the_player_the_turn(game: FakeGame, model: FakeModel, monkeypatch):
    """Mikael's reply can't be built (e.g. a broken prompt profile). Starting turn after turn for him would fail the same way, again and again"""
    from src.prompt_profile_manager import PromptSectionError
    build_prompt = shout.generate_npc_prompt
    def failing_for_mikael(self, context, speaker, *args, **kwargs):
        if speaker.name == MIKAEL.name:
            raise PromptSectionError("broken profile")
        return build_prompt(self, context, speaker, *args, **kwargs)
    monkeypatch.setattr(shout, "generate_npc_prompt", failing_for_mikael)
    game.start(HULDA, MIKAEL)
    game.set_shout(True)
    asked = len(model.game_master_choices)
    model.picks, model.also = [HULDA.name], [[MIKAEL.name]]
    game.say("Hulda, Mikael, good evening.")

    assert game.is_player_turn
    assert len(model.game_master_choices) - asked <= 2
