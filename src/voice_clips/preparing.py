"""Prepare all voices, on the Text-to-Speech page: every voice ready before it is needed.

A voice service that copies voices needs a clip from the game, then (for Pocket TTS) a saved voice made from it, which takes a few
seconds. Done when an NPC first speaks, their first lines use a stand-in. Prepare all voices asks the game for the clip of every voice
type its NPCs use, and makes their voices in the background, one at a time, most used voice types first. It only runs when the player
presses the button: the work would otherwise make the game heavier for everyone.

Voices are made ahead only for services that run on this PC: an online service would spend the player's money or voice slots.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from src.tts.voice_picker import VOICE_TYPE_CLIP, VoiceSpeaker
from src.voice_clips.clip_store import ClipStore, voice_key
from src.voice_clips.requests import ClipRequests

GIVE_UP_SECONDS = 900 # a voice type whose clip never comes (the game was closed) stops being looked at
LOOK_AGAIN_SECONDS = 2.0
LIST_WAIT_SECONDS = 60 # pressing the button again sooner doesn't ask the game a second time
TRY_AGAIN_SECONDS = 60 # after a voice couldn't be made
TRIES = 3

# Mantella can't call the game: the request goes with Mantella's next reply to it. After that, each batch of clips the game hands over
# brings the next batch, so the game never has to check in
WAITING_FOR_THE_GAME = ("Waiting for the game. It starts the next time you talk to someone in game or load a save, "
                        "and then goes on by itself while you play.")


def online_only_message(names: list[str]) -> str:
    """Prepare all voices pressed while every voice service in use runs online"""
    who = " and ".join(names) if names else "Online voice services"
    return (f"Nothing to prepare: {who} clone{'s' if len(names) == 1 else ''} each voice the first time an NPC speaks. "
            "Prepare all voices is for Pocket TTS and voice setups on your PC.")

_background = threading.local()


def in_background() -> bool:
    """Whether the voice being made is made ahead (the program can then run at a lower priority)"""
    return getattr(_background, "on", False)


@dataclass
class Wanted:
    voice_type: str
    since: float
    failures: int = 0
    failed_at: float = 0.0


class VoicePreparation:
    def __init__(self, store: ClipStore, requests: ClipRequests) -> None:
        self.__store = store
        self.__requests = requests
        self.__lock = threading.RLock()
        self.__wake = threading.Event()
        self.__wanted: dict[str, Wanted] = {} # by voice type, most used first
        self.__services: list[Any] = [] # every service that copies voices, alive now
        self.__thread: threading.Thread | None = None
        self.__all: list[str] | None = None # the voice types of Prepare all voices, once the game listed them
        self.__all_asked: float | None = None # when Prepare all voices was pressed
        requests.on_voice_types = self.__voice_types_listed

    # ---- services ----

    def add_service(self, service: Any) -> None:
        with self.__lock:
            if service not in self.__services:
                self.__services.append(service)

    def remove_service(self, service: Any) -> None:
        with self.__lock:
            if service in self.__services:
                self.__services.remove(service)

    @property
    def in_use(self) -> bool:
        with self.__lock:
            return bool(self.__services)

    def services_in_use(self) -> list[Any]:
        """What each service that copies voices and has spoken a line in game this run is (its VoiceServiceInfo). A service only
        heard with a button on the page doesn't count"""
        with self.__lock:
            return [s.info for s in self.__services if getattr(s, "info", None) is not None and getattr(s, "spoke_in_game", False)]

    def __makers(self) -> list[Any]:
        with self.__lock:
            return [s for s in self.__services if not s.info.runs_online]

    # ---- Prepare all voices ----

    def prepare_all(self) -> str:
        with self.__lock:
            online = [s.info.name for s in self.__services if s.info.runs_online]
            if self.__services and len(online) == len(self.__services):
                # Nothing would be made, so the game isn't asked for every clip either
                return online_only_message(list(dict.fromkeys(online)))
            if self.__all_asked is not None and self.__all is None and time.time() - self.__all_asked < LIST_WAIT_SECONDS:
                return WAITING_FOR_THE_GAME
            self.__all_asked = time.time()
            self.__all = None
        self.__requests.ask_for_voice_type_list()
        self.__start()
        return WAITING_FOR_THE_GAME

    def __voice_types_listed(self, voice_types: list[str]) -> None:
        with self.__lock:
            self.__all = list(voice_types)
            for voice_type in voice_types:
                self.__wanted.setdefault(voice_key(voice_type), Wanted(voice_type, time.time()))
        self.__requests.ask_for_voice_types_in_turn(voice_types)
        self.__start()

    def progress(self) -> str:
        """Prepare all voices, in plain words, for the page"""
        with self.__lock:
            asked, listed = self.__all_asked, self.__all
        if asked is None:
            return ""
        if listed is None:
            return WAITING_FOR_THE_GAME
        with_clip = [v for v in listed if self.__store.voice_type_clip(v)]
        no_lines = sum(1 for v in listed if not self.__store.voice_type_clip(v) and self.__requests.voice_type_settled(v))
        text = f"**{len(listed)}** voice types. Clips from the game: **{len(with_clip)}**"
        if no_lines:
            text += f" ({no_lines} with no usable line)"
        makers = self.__makers()
        made = 0
        if makers:
            maker = makers[0]
            if not getattr(maker, "can_copy_voices", True):
                return (text + f". {maker.info.name} can't clone voices right now, so no voices are made and NPCs use its built-in voices. "
                        f"See {maker.info.name} on this page.")
            made = sum(1 for v in with_clip if self.__voice_made(maker, v))
            text += f". Voices made for {maker.info.name}: **{made}** of {len(with_clip)}"
        settled = len(with_clip) + no_lines >= len(listed)
        if settled and (not makers or made >= len(with_clip)):
            return text + ". Done."
        return text + ". Working on it while you play..."

    # ---- making voices ----

    @staticmethod
    def __to_make(service: Any, voice_type: str):
        """The voice type's voice to make, or None when it is made or there is no clip yet"""
        pick = service.pick_voice(VoiceSpeaker(voice_type=voice_type))
        if pick.kind != VOICE_TYPE_CLIP or not pick.clip_path or service.voice_ready(pick):
            return None
        return pick

    def __voice_made(self, service: Any, voice_type: str) -> bool:
        return self.__to_make(service, voice_type) is None

    def __start(self) -> None:
        with self.__lock:
            if self.__thread is None or not self.__thread.is_alive():
                self.__thread = threading.Thread(target=self.__run, name="Voice preparation", daemon=True)
                self.__thread.start()
        self.__wake.set()

    def __run(self) -> None:
        while True:
            self.__wake.wait(LOOK_AGAIN_SECONDS)
            self.__wake.clear()
            try:
                self.__requests.collect()
                self.__make_next()
            except Exception as e:
                logging.warning(f"Voice clips: could not get a voice ready ahead: {e}")
                time.sleep(LOOK_AGAIN_SECONDS)

    def __make_next(self) -> None:
        """Makes at most one voice per round, the most used voice type first"""
        with self.__lock:
            queue = list(self.__wanted.items())
        makers = self.__makers()
        now = time.time()
        for key, wanted in queue:
            settled = self.__requests.voice_type_settled(wanted.voice_type)
            waiting = False
            for maker in makers:
                pick = self.__to_make(maker, wanted.voice_type)
                if pick is None:
                    continue
                waiting = True
                if not maker.is_ready() or now - wanted.failed_at < TRY_AGAIN_SECONDS:
                    continue # the program is starting or downloading its model, or this voice just failed
                _background.on = True
                try:
                    maker.prepared_voice(pick)
                except Exception as e:
                    wanted.failures += 1
                    wanted.failed_at = time.time()
                    logging.debug(f"{maker.info.name}: could not make the voice '{pick.label}' ahead: {e}")
                finally:
                    _background.on = False
                self.__wake.set() # look again at once: there may be more
                return
            if (settled and not waiting) or wanted.failures >= TRIES or (not settled and now - wanted.since > GIVE_UP_SECONDS):
                with self.__lock:
                    if self.__wanted.get(key) is wanted:
                        del self.__wanted[key]


_shared: dict[str, VoicePreparation] = {}
_shared_lock = threading.Lock()


def preparation_for(store: ClipStore, requests: ClipRequests) -> VoicePreparation:
    """One per voice clips folder, shared by the services, the page and the replies to the game"""
    with _shared_lock:
        found = _shared.get(store.folder)
        if found is None:
            found = VoicePreparation(store, requests)
            _shared[store.folder] = found
        return found
