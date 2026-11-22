"""Prepare all voices: asking the game for every voice type's clip, and making the voices in the background"""
import json
import os
import shutil
from types import SimpleNamespace

import pytest

from src.tts.voice_picker import BUILT_IN, VOICE_TYPE_CLIP, VoicePick, VoiceSpeaker
from src.voice_clips import preparing
from src.voice_clips.clip_store import VOICE_TYPES, ClipStore
from src.voice_clips.preparing import VoicePreparation
from src.voice_clips.requests import IN_TURN_AT_ONCE, RESULT_FILE, ClipRequests

GAME_LINE = os.path.join(os.path.dirname(__file__), "data", "dialoguesolitude__000dd60f_1.fuz")


@pytest.fixture
def store(tmp_path):
    return ClipStore(str(tmp_path / "voice_clips"))


@pytest.fixture
def requests(store):
    return ClipRequests(store)


@pytest.fixture
def preparation(store, requests, monkeypatch):
    found = VoicePreparation(store, requests)
    monkeypatch.setattr(found, "_VoicePreparation__start", lambda: None) # the tests make voices themselves, without the thread
    return found


class FakeService:
    """A service that copies voices: a voice is 'made' once prepared_voice ran for it"""
    def __init__(self, store: ClipStore, runs_online: bool = False, ready: bool = True) -> None:
        self.info = SimpleNamespace(name="Fake", runs_online=runs_online)
        self.store = store
        self.ready = ready
        self.made: list[str] = []
        self.in_background: list[bool] = []
        self.fail = False
        self.can_copy_voices = True

    def pick_voice(self, speaker, named_voice=None):
        clip = self.store.voice_type_clip(speaker.voice_type)
        return VoicePick(VOICE_TYPE_CLIP, speaker.voice_type.lower(), clip) if clip else VoicePick(BUILT_IN, "alba")

    def voice_ready(self, pick):
        return pick.key in self.made

    def is_ready(self):
        return self.ready

    def prepared_voice(self, pick):
        self.in_background.append(preparing.in_background())
        if self.fail:
            raise RuntimeError("no")
        self.made.append(pick.key)


def sent_requests(requests: ClipRequests) -> list[dict]:
    return [json.loads(r) for r in requests.requests_for_reply()]


def deliver(request: dict, voice_type: str | None = None, voice_types: list[str] | None = None) -> None:
    """What MantellaVoiceClips.dll does: a clip for a voice type, or the list of voice types"""
    os.makedirs(request["dest"], exist_ok=True)
    result = {"id": request["id"], "status": "ok", "clips": [], "missing": []}
    if voice_types is not None:
        result["voice_types"] = [{"name": v, "npcs": 1} for v in voice_types]
    if voice_type:
        shutil.copyfile(GAME_LINE, os.path.join(request["dest"], "1.fuz"))
        result["clips"] = [{"file": "1.fuz", "for": "voice_type", "path": f"sound\\voice\\skyrim.esm\\{voice_type.lower()}\\a__000dd60f_1.fuz",
                            "plugin": "Skyrim.esm", "info": "000DD60F", "size": 1, "source": "loose", "seconds": 4.6}]
    with open(os.path.join(request["dest"], RESULT_FILE), "w", encoding="utf-8") as file:
        json.dump(result, file)


def test_prepare_all_voices_asks_for_the_voice_types_then_their_clips_a_few_at_a_time(preparation, requests, store):
    preparation.add_service(FakeService(store))
    preparation.prepare_all()
    listing = sent_requests(requests)
    assert listing == [{"id": listing[0]["id"], "dest": listing[0]["dest"], "list_voice_types": True}]
    assert preparation.progress() == preparing.WAITING_FOR_THE_GAME

    names = [f"Voice{i:02d}" for i in range(IN_TURN_AT_ONCE + 3)]
    deliver(listing[0], voice_types=names)
    requests.collect()
    first = sent_requests(requests)
    assert [r["voice_type"] for r in first] == names[:IN_TURN_AT_ONCE] # not hundreds at once
    assert all(r["npc_lines"] == "none" and r["count"] == 1 for r in first) # one line each: a quarter of the game's reading
    assert sent_requests(requests) == [] # the next ones wait for answers

    deliver(first[0], voice_type=first[0]["voice_type"])
    requests.collect()
    assert [r["voice_type"] for r in sent_requests(requests)] == [names[IN_TURN_AT_ONCE]] # each answer brings the next
    assert f"**{len(names)}** voice types. Clips from the game: **1**" in preparation.progress()


def test_voice_types_that_already_have_a_clip_are_skipped(preparation, requests, store, tmp_path):
    from src.voice_clips import fuz
    from src.voice_clips.clip_store import ClipInfo
    wav = str(tmp_path / "a.wav")
    fuz.to_wav(GAME_LINE, wav)
    store.set_clip(VOICE_TYPES, "malenord", wav, ClipInfo(name="MaleNord"))
    preparation.add_service(FakeService(store))
    preparation.prepare_all()
    deliver(sent_requests(requests)[0], voice_types=["MaleNord", "FemaleNord"])
    requests.collect()
    assert [r["voice_type"] for r in sent_requests(requests)] == ["FemaleNord"]


def make_all(preparation, rounds: int = 20) -> None:
    for _ in range(rounds):
        preparation._VoicePreparation__make_next()


def listed(preparation, requests, voice_types: list[str]) -> None:
    """Prepare all voices pressed, the game lists its voice types and hands over every clip"""
    preparation.prepare_all()
    deliver(sent_requests(requests)[0], voice_types=voice_types)
    requests.collect()
    for request in sent_requests(requests):
        deliver(request, voice_type=request["voice_type"])
    requests.collect()


def test_voices_are_made_in_the_background_most_used_first(preparation, requests, store):
    service = FakeService(store)
    preparation.add_service(service)
    listed(preparation, requests, ["MaleNord", "FemaleYoungEager"])
    preparation._VoicePreparation__make_next()
    assert service.made == ["malenord"] # one per round, in the order the game listed them
    make_all(preparation)
    assert service.made == ["malenord", "femaleyoungeager"] and all(service.in_background)
    assert preparation.progress().endswith("Done.")
    assert preparation._VoicePreparation__wanted == {} # nothing left to look at


def test_no_voice_is_made_while_the_program_isnt_ready_or_for_an_online_service(preparation, requests, store):
    starting, online = FakeService(store, ready=False), FakeService(store, runs_online=True)
    preparation.add_service(starting)
    preparation.add_service(online)
    listed(preparation, requests, ["FemaleYoungEager"])
    make_all(preparation)
    assert starting.made == [] and online.made == []
    starting.ready = True
    make_all(preparation)
    assert starting.made == ["femaleyoungeager"] and online.made == [] # an online service would spend money or voice slots


def test_a_voice_that_fails_is_tried_again_later_then_given_up(preparation, requests, store, monkeypatch):
    service = FakeService(store)
    service.fail = True
    preparation.add_service(service)
    listed(preparation, requests, ["FemaleYoungEager"])
    clock = [1000.0]
    monkeypatch.setattr(preparing.time, "time", lambda: clock[0])
    make_all(preparation)
    assert len(service.in_background) == 1 # not again at once
    for _ in range(preparing.TRIES):
        clock[0] += preparing.TRY_AGAIN_SECONDS + 1
        make_all(preparation)
    assert len(service.in_background) == preparing.TRIES and preparation._VoicePreparation__wanted == {}


def test_nothing_is_asked_for_until_prepare_all_voices_is_pressed(preparation, requests, store):
    preparation.add_service(FakeService(store))
    assert requests.requests_for_reply() == [] # only Prepare all voices asks, when pressed


def test_a_stopped_service_makes_no_more_voices(preparation, store):
    service = FakeService(store)
    preparation.add_service(service)
    assert preparation.in_use
    preparation.remove_service(service)
    assert not preparation.in_use


def test_pressing_the_button_twice_asks_the_game_once(preparation, requests, store, monkeypatch):
    preparation.add_service(FakeService(store))
    clock = [1000.0]
    monkeypatch.setattr(preparing.time, "time", lambda: clock[0])
    preparation.prepare_all()
    assert preparation.prepare_all() == preparing.WAITING_FOR_THE_GAME
    assert len(sent_requests(requests)) == 1
    clock[0] += preparing.LIST_WAIT_SECONDS + 1 # the list never came (an old add-on): pressing again asks again
    preparation.prepare_all()
    assert len(sent_requests(requests)) == 1


def test_a_service_that_cant_copy_voices_isnt_reported_done(preparation, requests, store):
    service = FakeService(store)
    service.can_copy_voices = False # Pocket TTS without a working sign-in
    preparation.add_service(service)
    listed(preparation, requests, ["MaleNord"])
    assert "can't clone voices right now" in preparation.progress() and "Done" not in preparation.progress()


def test_a_first_clip_for_someone_speaking_still_brings_lines_to_hear(requests):
    requests.ask_for("FemaleEvenToned")
    assert json.loads(requests.requests_for_reply()[0])["count"] == 4


def test_with_only_online_services_there_is_nothing_to_prepare(preparation, requests, store):
    online = FakeService(store, runs_online=True)
    online.info.name = "ElevenLabs"
    preparation.add_service(online)
    message = preparation.prepare_all()
    assert "Nothing to prepare: ElevenLabs clones each voice the first time an NPC speaks" in message
    assert requests.requests_for_reply() == [] # the game isn't asked for every clip
    assert preparation.progress() == ""
    preparation.add_service(FakeService(store)) # an NPC on Pocket TTS too
    assert "Nothing to prepare" not in preparation.prepare_all()


# ---- an NPC who joins a conversation ----

def make_soon(preparation) -> bool:
    return preparation._VoicePreparation__make_soon()


def test_a_new_voices_request_is_on_the_way_from_when_it_is_handed_to_the_game_until_it_is_delivered(requests):
    requests.ask_for("MaleNord")
    requests.ask_for("FemaleNord")
    assert requests.on_the_way(["voice_types/malenord"]) == [] # still waiting for a reply to ride on
    male, female = sent_requests(requests)
    assert requests.on_the_way(["voice_types/malenord", "voice_types/femalenord"]) == [male["id"], female["id"]]

    deliver(male, voice_type="MaleNord")
    requests.collect()
    assert requests.on_the_way(["voice_types/malenord"]) == []
    requests.waited_for([female["id"]])
    assert requests.on_the_way(["voice_types/femalenord"]) == [] # a line waited for it once already


def test_an_npc_who_joins_gets_their_voice_made_as_soon_as_their_clips_arrive(preparation, requests, store):
    service = FakeService(store)
    preparation.add_service(service)
    preparation.make_soon(service, VoiceSpeaker(voice_type="MaleNord"))
    requests.ask_for("MaleNord")
    request = sent_requests(requests)[0]
    assert not make_soon(preparation) and service.made == [] # not the stand-in while their clips are on the way

    deliver(request, voice_type="MaleNord")
    requests.collect()
    assert make_soon(preparation)
    assert service.made == ["malenord"] and service.in_background == [False] # needed now: not at the lower priority
    assert not make_soon(preparation) # done


def test_an_npc_whose_clips_never_come_is_left_to_their_first_line(preparation, requests, store, monkeypatch):
    service = FakeService(store)
    preparation.add_service(service)
    preparation.make_soon(service, VoiceSpeaker(voice_type="MaleNord"))
    monkeypatch.setattr(preparing, "SOON_GIVE_UP_SECONDS", -1)
    assert not make_soon(preparation)
    assert service.made == [] and preparation._VoicePreparation__soon == []


def test_a_voice_program_still_starting_makes_the_voice_once_it_is_ready(preparation, requests, store):
    service = FakeService(store, ready=False)
    preparation.add_service(service)
    preparation.make_soon(service, VoiceSpeaker(voice_type="MaleNord"))
    requests.ask_for("MaleNord")
    deliver(sent_requests(requests)[0], voice_type="MaleNord")
    requests.collect()
    assert not make_soon(preparation) and service.made == [] # the program is still starting: tried again later
    service.ready = True
    assert make_soon(preparation) and service.made == ["malenord"]
