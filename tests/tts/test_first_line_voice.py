"""A new voice's first line: its clips are asked for when the NPC joins, and the line waits a while for them instead of using a stand-in"""
import json
import threading
import time

import pytest

from src.tts import clip_service
from src.tts.clip_service import ClipVoiceService
from src.tts.registry import VoiceServiceInfo
from src.tts.voice_picker import BUILT_IN, STAND_INS, VOICE_TYPE_CLIP, VoiceSpeaker
from tests.voice_clips.test_preparing import deliver


class FakeService(ClipVoiceService):
    def built_in_voice(self, gender):
        return "alba" if gender == 1 else "george"

    def speak(self, text, voice, out_path):
        raise AssertionError("not spoken in these tests")


LYDIA = VoiceSpeaker(name="Lydia", gender=1, race="NordRace", voice_type="FemaleEvenToned", plugin="Skyrim.esm", local_id="0A2C94")


@pytest.fixture
def service(voice_config):
    found = FakeService(voice_config, VoiceServiceInfo("Fake"))
    yield found
    found.stop()


def handed_to_game(service) -> list[dict]:
    return [json.loads(r) for r in service._clip_requests.requests_for_reply()]


def wait_for_own_voice(service, speaker=LYDIA):
    return service._ClipVoiceService__wait_for_own_voice(speaker, None, service.pick_voice(speaker))


def voice_type_request(requests: list[dict], voice_type: str) -> dict:
    return next(r for r in requests if r["voice_type"] == voice_type and r["npc_lines"] == "if_different")


def test_joining_the_conversation_asks_for_the_clips_without_waiting_for_the_first_line(service):
    service.get_ready_for(LYDIA, make_ahead=False)
    request = voice_type_request(handed_to_game(service), "FemaleEvenToned")
    assert request["count"] == service.info.clip_lines


def test_the_first_line_waits_for_clips_already_on_their_way_and_speaks_in_the_npcs_own_voice(service):
    service.get_ready_for(LYDIA, make_ahead=False)
    request = voice_type_request(handed_to_game(service), "FemaleEvenToned")
    assert service.pick_voice(LYDIA).kind in STAND_INS

    threading.Timer(0.3, lambda: deliver(request, voice_type="FemaleEvenToned")).start() # the game copies the lines meanwhile
    pick = wait_for_own_voice(service)
    assert pick.kind == VOICE_TYPE_CLIP and pick.key == "femaleeventoned"


def test_clips_that_dont_come_in_time_hold_up_one_line_only(service, monkeypatch):
    monkeypatch.setattr(clip_service, "FIRST_LINE_WAIT_SECONDS", 0.3)
    service.get_ready_for(LYDIA, make_ahead=False)
    handed_to_game(service)

    started = time.monotonic()
    assert wait_for_own_voice(service).kind == BUILT_IN
    assert time.monotonic() - started >= 0.3

    started = time.monotonic()
    assert wait_for_own_voice(service).kind == BUILT_IN
    assert time.monotonic() - started < 0.2 # the next line doesn't wait again


def test_a_line_doesnt_wait_for_clips_not_handed_to_the_game_yet(service):
    """An NPC who joins with the request this line answers: the clips go out with this line's reply, so waiting can't help"""
    service.get_ready_for(LYDIA, make_ahead=False)
    started = time.monotonic()
    assert wait_for_own_voice(service).kind == BUILT_IN
    assert time.monotonic() - started < 0.2


def test_no_wait_without_the_voice_clips_plugin(service):
    """The game sends where an NPC comes from only when MantellaVoiceClips.dll is installed: without it no clips ever come"""
    no_plugin = VoiceSpeaker(name="Lydia", gender=1, race="NordRace", voice_type="FemaleEvenToned")
    service.get_ready_for(no_plugin, make_ahead=False)
    handed_to_game(service)
    started = time.monotonic()
    assert wait_for_own_voice(service, no_plugin).kind == BUILT_IN
    assert time.monotonic() - started < 0.2


def test_the_stand_in_isnt_made_ahead_while_the_npcs_own_clips_are_on_their_way(service, monkeypatch):
    made = []
    monkeypatch.setattr(service, "prepared_voice", lambda pick: made.append(pick.key))
    service.get_ready_for(LYDIA, make_ahead=False)
    service.change_voice("", "FemaleEvenToned", voice_gender=1, voice_race="NordRace")
    assert made == []
