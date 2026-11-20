"""ElevenLabs and Inworld: each clip cloned once on the player's account and reused, cloned again after a swap with the old voice deleted,
a voice deleted on the website cloned again, a refused clone falling back to built-in voices, and voices cloned from several clips"""
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.tts import online_service, voice_notices
from src.tts.clip_service import ClipVoiceService, VoiceServiceFailure
from src.tts.elevenlabs import ElevenLabs
from src.tts.inworld import Inworld
from src.tts.online_voices import cloned_voices
from src.tts.registry import voice_services
from src.tts.test_line import voice_from_clip
from src.tts.voice_picker import BUILT_IN, VOICE_TYPE_CLIP, VoicePick, VoiceSpeaker
from tests.tts.fake_voice_server import tone_wav


class FakeOnlineService:
    """Answers the ElevenLabs and Inworld requests Mantella sends, and records them"""

    def __init__(self) -> None:
        self.clones: list[dict] = []
        self.spoken: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        self.gone: set[str] = set()
        self.gone_code = 404 # how the service answers a line in a voice it no longer has
        self.speak_error: tuple[int, dict] | None = None # every line fails like this, the voice still being there
        self.refuse: tuple[int, dict] | None = None # how a clone is refused
        self.headers: list[dict] = []
        self.slots = (30, 4) # voices the plan keeps, and how many are in use
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, code: int, body: bytes, content_type: str = "application/json"):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length", 0)))

            def do_POST(self):
                server.headers.append(dict(self.headers))
                data = self.body()
                if self.path == "/v1/voices/add" or self.path == "/voices/v1/voices:clone":
                    if server.refuse:
                        code, body = server.refuse
                        return self.send(code, json.dumps(body).encode())
                    server.clones.append({"path": self.path, "body": data})
                    voice_id = f"v{len(server.clones)}"
                    if self.path == "/v1/voices/add":
                        return self.send(200, json.dumps({"voice_id": voice_id, "requires_verification": False}).encode())
                    return self.send(200, json.dumps({"voice": {"voiceId": f"ws__{voice_id}"}}).encode())
                if self.path.startswith("/v1/text-to-speech/"):
                    voice_id = self.path.split("/")[3].split("?")[0]
                    if voice_id in server.gone:
                        return self.send(server.gone_code, b'{"detail": {"status": "voice_not_found", "message": "not found"}}')
                    if server.speak_error:
                        return self.send(server.speak_error[0], json.dumps(server.speak_error[1]).encode())
                    server.spoken.append((voice_id, json.loads(data)))
                    return self.send(200, tone_wav(), "audio/mpeg")
                if self.path == "/tts/v1/voice":
                    request = json.loads(data)
                    server.spoken.append((request["voiceId"], request))
                    return self.send(200, json.dumps({"audioContent": base64.b64encode(tone_wav()).decode()}).encode())
                self.send(404, b"{}")

            def do_DELETE(self):
                server.deleted.append(self.path.rsplit("/", 1)[-1])
                self.send(200, b"{}")

            def do_GET(self):
                if self.path == "/v1/user/subscription":
                    return self.send(200, json.dumps({"voice_limit": server.slots[0], "voice_slots_used": server.slots[1], "can_use_instant_voice_cloning": True}).encode())
                if self.path.startswith("/v1/voices/") or self.path.startswith("/voices/v1/voices/"):
                    voice_id = self.path.rsplit("/", 1)[-1]
                    return self.send(404, b"{}") if voice_id in server.gone else self.send(200, json.dumps({"voice_id": voice_id}).encode())
                if self.path.startswith("/voices/v1/voices"):
                    return self.send(200, json.dumps({"voices": [{"voiceId": "ws__v1", "source": "IVC"}, {"voiceId": "Alex", "source": "SYSTEM"}]}).encode())
                self.send(404, b"{}")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def wait_for_deleted(self, voice_id: str) -> bool:
        deadline = time.time() + 5
        while time.time() < deadline:
            if voice_id in self.deleted:
                return True
            time.sleep(0.02)
        return False


@pytest.fixture(autouse=True)
def no_problems_left():
    yield
    for name in ("ElevenLabs", "Inworld"):
        online_service.set_problem(name, None)


@pytest.fixture
def fake(monkeypatch):
    server = FakeOnlineService()
    for service in (ElevenLabs, Inworld):
        monkeypatch.setattr(service, "API_URL", server.url)
    yield server
    server.close()


def make(monkeypatch, voice_config, service_class, key: str = "secret"):
    monkeypatch.setattr(online_service, "read_key_file", lambda name: key)
    monkeypatch.setattr("src.tts.voice_setups.read_key_file", lambda name: key)
    return service_class(voice_config, voice_services.find(service_class.NAME))


def clip(tmp_path, name: str = "clip.wav", seconds: float = 0.5) -> str:
    path = tmp_path / name
    path.write_bytes(tone_wav(seconds))
    return str(path)


def test_both_are_listed_as_online_services():
    for name, alias in (("ElevenLabs", "11labs"), ("Inworld", "inworld")):
        info = voice_services.find(alias)
        assert info.name == name and info.runs_online # Prepare all voices skips them
    assert voice_services.find("Inworld").accepts_ipa and not voice_services.find("ElevenLabs").accepts_ipa


def test_a_clip_is_cloned_once_and_reused(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path))
    service.voice_into("Hello there.", pick, str(tmp_path / "one.wav"))
    service.voice_into("Hello again.", pick, str(tmp_path / "two.wav"))
    assert len(fake.clones) == 1 and b"Mantella femaleeventoned" in fake.clones[0]["body"]
    assert [voice for voice, _ in fake.spoken] == ["v1", "v1"] and fake.spoken[0][1]["model_id"] == "eleven_v4_turbo"
    assert fake.headers[0]["xi-api-key"] == "secret"
    again = make(monkeypatch, voice_config, ElevenLabs) # the next run keeps the voice cloned before
    assert again.voice_ready(pick)
    again.voice_into("Hello.", pick, str(tmp_path / "three.wav"))
    assert len(fake.clones) == 1


def test_a_swapped_clip_is_cloned_again_and_the_old_voice_deleted(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, ElevenLabs)
    path = clip(tmp_path)
    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "malenord", path), str(tmp_path / "one.wav"))
    clip(tmp_path, seconds=0.7) # another line from the game, under the same name
    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "malenord", path), str(tmp_path / "two.wav"))
    assert len(fake.clones) == 2 and fake.spoken[-1][0] == "v2"
    assert fake.wait_for_deleted("v1")


def test_a_voice_deleted_on_the_website_is_cloned_again(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    fake.gone.add("v1")
    fake.gone_code = 400 # ElevenLabs may say it with a general error and its reason
    service.voice_into("Hello.", pick, str(tmp_path / "two.wav")) # the retry clones it again
    assert len(fake.clones) == 2 and fake.spoken[-1][0] == "v2"


def test_a_not_found_about_something_else_never_clones_again(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    fake.speak_error = (404, {"detail": {"status": "model_not_found", "message": "model not found"}})
    with pytest.raises(VoiceServiceFailure):
        service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert len(fake.clones) == 1 # the voice is still there, so cloning again would only use up a voice


def test_a_refused_clone_uses_built_in_voices_and_says_why(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, ElevenLabs)
    fake.refuse = (400, {"detail": {"status": "voice_limit_reached", "message": "voice limit reached"}})
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    with pytest.raises(VoiceServiceFailure): # the backup service speaks this line
        service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    assert not service.can_copy_voices and "won't clone another voice" in service.why_not and "tries again in 5 minutes" in service.why_not
    assert any("ElevenLabs can't clone voices right now" in n for n in voice_notices.take_notices())
    assert "won't clone another voice" in online_service.problem("ElevenLabs") # shown on its part of the page
    monkeypatch.setattr(ClipVoiceService, "pick_voice", lambda self, speaker, named_voice=None: pick) # the clip is there
    assert service.pick_voice(VoiceSpeaker(gender=1, voice_type="FemaleEvenToned")).kind == BUILT_IN
    with pytest.raises(ValueError, match="won't clone another voice"): # Hear the cloned voice says why
        voice_from_clip(service, "ElevenLabs", pick, "Hello.")

    # The player made room on their account: a few minutes later cloning is tried again
    fake.refuse = None
    now = time.time()
    monkeypatch.setattr(online_service.time, "time", lambda: now + online_service.RETRY_MINUTES * 60)
    assert service.can_copy_voices
    service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert fake.spoken[-1][0] == "v1" and online_service.problem("ElevenLabs") == ""


@pytest.mark.parametrize("status,expected", [("quota_exceeded", "no characters left"), ("missing_permissions", "isn't allowed to clone voices")])
def test_elevenlabs_out_of_credits_or_permissions_is_not_a_bad_key(monkeypatch, voice_config, tmp_path, fake, status, expected):
    service = make(monkeypatch, voice_config, ElevenLabs)
    fake.refuse = (401, {"detail": {"status": status, "message": "no"}})
    with pytest.raises(VoiceServiceFailure):
        service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path)), str(tmp_path / "one.wav"))
    assert expected in service.why_not and "doesn't accept your key" not in service.why_not


def test_no_key_means_the_backup_speaks(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, Inworld, key="")
    with pytest.raises(VoiceServiceFailure):
        service.voice_into("Hello.", VoicePick(BUILT_IN, "Alex", None), str(tmp_path / "one.wav"))
    assert "INWORLD_SECRET_KEY.txt" in service.why_not and not fake.spoken
    with pytest.raises(ValueError, match="has no key"): # Hear the cloned voice: not "it would use its built-in voices"
        voice_from_clip(service, "Inworld", VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path)), "Hello.")


def test_inworld_clones_with_the_clip_itself_and_speaks_sound_symbols(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, Inworld, key="aBase64Key")
    path = clip(tmp_path)
    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", path), str(tmp_path / "one.wav"))
    body = json.loads(fake.clones[0]["body"])
    assert base64.b64decode(body["voiceSamples"][0]["audioData"]) == open(path, "rb").read() and body["languageCode"] == "en"
    assert fake.headers[0]["Authorization"] == "Basic aBase64Key"
    assert fake.spoken[0][0] == "ws__v1" and fake.spoken[0][1]["modelId"] == "inworld-tts-2"
    assert service.format_ipa("Dovahkiin", "ˈdoʊvɑːkiːn") == "/ˈdoʊvɑːkiːn/"
    service.voice_into("Say Dovahkiin.", VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", path), str(tmp_path / "two.wav"), use_word_lists=False,
                       ipa_word=("Dovahkiin", "ˈdoʊvɑːkiːn")) # Fix Pronunciation's Hear it fixed
    assert fake.spoken[-1][1]["text"] == "Say /ˈdoʊvɑːkiːn/."


def test_inworld_speaks_with_the_chosen_model(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, Inworld)
    voice_config.inworld_model = "TTS-2 Flash"
    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path)), str(tmp_path / "one.wav"))
    assert fake.spoken[0][1]["modelId"] == "inworld-tts-2-flash"


def test_inworld_delivers_lines_by_the_voices_delivery_on_tts_2_only(monkeypatch, voice_config, tmp_path, fake):
    from src.voice_clips.clip_store import VOICE_TYPES
    service = make(monkeypatch, voice_config, Inworld)
    voice_config.inworld_model, voice_config.inworld_delivery = "TTS-2", "Creative"
    lydia, serana = (VoicePick(VOICE_TYPE_CLIP, key, clip(tmp_path, f"{key}.wav")) for key in ("femalenord", "femaleeventoned"))
    service._clips.set_voice_setting(VOICE_TYPES, "femalenord", "inworld_delivery", "Stable") # this voice's own, from the Voice Clips page
    service.voice_into("Hello.", lydia, str(tmp_path / "one.wav"))
    service.voice_into("Hello.", serana, str(tmp_path / "two.wav"))
    assert [request["deliveryMode"] for _, request in fake.spoken] == ["STABLE", "CREATIVE"] # the other voice has the page's
    voice_config.inworld_model = "TTS-2 Flash" # Flash has no Delivery
    service.voice_into("Hello.", lydia, str(tmp_path / "three.wav"))
    assert "deliveryMode" not in fake.spoken[-1][1]


def test_inworld_gets_the_line_with_its_sounds_and_directions_and_elevenlabs_without(monkeypatch, voice_config, tmp_path, fake):
    from src.tts.synthesization_options import SynthesizationOptions
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path))
    options = SynthesizationOptions(False, True)
    for service_class in (Inworld, ElevenLabs):
        service = make(monkeypatch, voice_config, service_class)
        service._pick, service._voice_text = pick, "[sigh] Fine, [say coldly] take it."
        service.tts_synthesize("Fine, take it.", str(tmp_path / "one.wav"), options)
        service._pick = pick # the next line has no sounds or directions
        service.tts_synthesize("Go.", str(tmp_path / "two.wav"), options)
    inworld_lines = [request["text"] for _, request in fake.spoken if "voiceId" in request]
    elevenlabs_lines = [request["text"] for _, request in fake.spoken if "voiceId" not in request]
    assert inworld_lines == ["[sigh] Fine, [say coldly] take it.", "Go."]
    assert elevenlabs_lines == ["Fine, take it.", "Go."]


def test_a_new_key_forgets_voices_from_the_old_account(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, ElevenLabs, key="first")
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    assert not make(monkeypatch, voice_config, ElevenLabs, key="second").voice_ready(pick)


def a_voice(service, tmp_path, seconds: list[float], key: str = "femaleeventoned") -> VoicePick:
    """A voice type in the service's clip store, with its chosen clips (tones of these lengths) in order"""
    from src.voice_clips.clip_store import VOICE_TYPES, ClipInfo
    store = service._clips
    store.set_clip(VOICE_TYPES, key, clip(tmp_path, "top.wav", seconds[0]), ClipInfo(name="FemaleEvenToned",
                   path=r"sound\voice\skyrim.esm\femaleeventoned\a__000dd600_1.fuz"))
    for n, length in enumerate(seconds[1:], start=1):
        store.add_candidate(VOICE_TYPES, key, clip(tmp_path, f"{n}.wav", length),
                            ClipInfo(path=rf"sound\voice\skyrim.esm\femaleeventoned\a__000dd60{n}_1.fuz", order=n))
    return VoicePick(VOICE_TYPE_CLIP, key, store.clip(VOICE_TYPES, key))


def test_elevenlabs_clones_from_every_chosen_clip_as_separate_files(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = a_voice(service, tmp_path, [2.0, 2.0, 2.0])
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    assert fake.clones[0]["body"].count(b'name="files"') == 3
    assert ElevenLabs(voice_config, voice_services.find("ElevenLabs")).voice_ready(pick) # the next run keeps it


def test_inworld_clones_from_the_chosen_clips_joined_and_within_30_seconds(monkeypatch, voice_config, tmp_path, fake):
    import io
    import soundfile as sf
    service = make(monkeypatch, voice_config, Inworld)
    pick = a_voice(service, tmp_path, [9.0, 9.0, 9.0, 9.0]) # 4 lines of 9 s (and their quiet): only 2 fit in 30 s
    assert len(service.clips_for(pick)) == 2
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    sample = base64.b64decode(json.loads(fake.clones[0]["body"])["voiceSamples"][0]["audioData"])
    seconds = sf.info(io.BytesIO(sample)).duration
    assert 19.0 < seconds < 30.0 # two clips and the gap between them


def test_choosing_other_clips_clones_again_and_deletes_the_old_voice(monkeypatch, voice_config, tmp_path, fake):
    from src.voice_clips.clip_store import VOICE_TYPES
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = a_voice(service, tmp_path, [1.0, 1.0, 1.0])
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    store = service._clips
    store.take_out(VOICE_TYPES, "femaleeventoned", store.voice_lines(VOICE_TYPES, "femaleeventoned")[2][0])
    assert not service.voice_ready(pick)
    service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert len(fake.clones) == 2 and fake.clones[1]["body"].count(b'name="files"') == 2 and fake.spoken[-1][0] == "v2"
    assert fake.wait_for_deleted("v1")


def refuse_first_clone(monkeypatch) -> None:
    """ElevenLabs refuses the next clone, saying the plan holds no more voices"""
    refused = []
    real_clone = ElevenLabs.clone
    def clone(self, name, clip_paths, language, pick=None):
        if not refused:
            refused.append(True)
            from src.tts.online_service import PlanFull
            raise PlanFull("voice limit reached")
        return real_clone(self, name, clip_paths, language, pick)
    monkeypatch.setattr(ElevenLabs, "clone", clone)


def test_a_full_plan_makes_room_with_the_voices_old_copy(monkeypatch, voice_config, tmp_path, fake):
    from src.voice_clips.clip_store import VOICE_TYPES
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = a_voice(service, tmp_path, [1.0, 1.0])
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    store = service._clips
    store.take_out(VOICE_TYPES, "femaleeventoned", store.voice_lines(VOICE_TYPES, "femaleeventoned")[1][0])
    fake.slots = (30, 30) # every slot in use, as ElevenLabs says
    refuse_first_clone(monkeypatch)
    service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert "v1" in fake.deleted and fake.spoken[-1][0] == "v2" and service.can_copy_voices


def test_a_refusal_when_the_plan_isnt_full_keeps_the_voice(monkeypatch, voice_config, tmp_path, fake):
    """Another limit (such as a monthly cap on making voices): deleting the voice wouldn't help, so NPCs keep it"""
    from src.voice_clips.clip_store import VOICE_TYPES
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = a_voice(service, tmp_path, [1.0, 1.0])
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    store = service._clips
    store.take_out(VOICE_TYPES, "femaleeventoned", store.voice_lines(VOICE_TYPES, "femaleeventoned")[1][0])
    refuse_first_clone(monkeypatch) # the fake plan has room: 4 of 30 in use
    service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert fake.deleted == [] and cloned_voices(voice_config.save_folder, "ElevenLabs").get("femaleeventoned")["voice_id"] == "v1"
    assert fake.spoken[-1][0] == "v1" # NPCs keep the voice made before, not a built-in one
    assert not service.can_copy_voices and service.why_not # refused for a while, and says why
    service.voice_into("Hello.", pick, str(tmp_path / "three.wav"))
    assert fake.spoken[-1][0] == "v1" and len(fake.clones) == 1 # not asked again during the wait


def test_a_short_voice_asks_the_game_for_more_lines(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = a_voice(service, tmp_path, [1.0, 1.0, 1.0, 1.0]) # 4 short lines, far less than a minute
    service._top_up(pick)
    [sent] = service._clip_requests.requests_for_reply()
    assert json.loads(sent)["count"] == 12 # enough lines of that length for a minute, up to 16 clips in all


def test_a_short_voice_whose_clips_the_player_chose_still_gets_more_lines(monkeypatch, voice_config, tmp_path, fake):
    """The player's clips stay as they are: the new lines go after them, and a line they took out isn't asked for again"""
    from src.voice_clips.clip_store import VOICE_TYPES
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = a_voice(service, tmp_path, [1.0, 1.0, 1.0, 1.0])
    store = service._clips
    store.take_out(VOICE_TYPES, "femaleeventoned", store.voice_lines(VOICE_TYPES, "femaleeventoned")[1][0]) # the player changed it
    assert store.is_hand_picked(VOICE_TYPES, "femaleeventoned")
    service._top_up(pick)
    [sent] = service._clip_requests.requests_for_reply()
    request = json.loads(sent)
    assert request["count"] > 0 and "skyrim.esm|000dd601" in request["exclude"]


def test_only_a_refusal_about_the_number_of_voices_means_the_plan_is_full():
    from src.tts.online_service import says_plan_full
    for full in ("voice_limit_reached", "You have reached your voice limit.", "Maximum number of voices reached", "Too many cloned voices"):
        assert says_plan_full(full), full
    for other in ("Audio exceeds the length limit for voice cloning", "character limit reached", "Rate limit exceeded", ""):
        assert not says_plan_full(other), other


def test_check_my_key(monkeypatch, voice_config, fake):
    assert "plan keeps 30 voices and 4 are in use" in ElevenLabs.check_key("secret", voice_config.save_folder)
    assert "workspace has 1 cloned voices" in Inworld.check_key("secret", voice_config.save_folder)


def test_a_clone_refused_while_an_npc_speaks_uses_a_built_in_voice_not_the_backup(monkeypatch, voice_config, tmp_path, fake):
    from src.tts.elevenlabs import FEMALE_VOICE
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path))
    monkeypatch.setattr(ClipVoiceService, "pick_voice", lambda self, speaker, named_voice=None: pick) # the clip is there
    chosen = service.pick_voice(VoiceSpeaker(gender=1, voice_type="FemaleEvenToned")) # in game, before the line
    fake.refuse = (400, {"detail": {"status": "voice_limit_reached", "message": "voice limit reached"}})
    service.voice_into("Hello.", chosen, str(tmp_path / "one.wav")) # no failure: the backup service doesn't speak
    assert fake.spoken[-1][0] == FEMALE_VOICE and "won't clone another voice" in service.why_not


def test_out_of_credits_while_speaking_says_why_and_stops_asking_for_a_while(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    fake.speak_error = (401, {"detail": {"status": "quota_exceeded", "message": "quota exceeded"}})
    with pytest.raises(VoiceServiceFailure): # the backup service speaks this line
        service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert any("ElevenLabs can't speak right now" in n for n in voice_notices.take_notices())
    assert "no characters left" in online_service.problem("ElevenLabs") and "Backup Voice Service speaks" in service.why_not
    asked = len(fake.headers)
    with pytest.raises(VoiceServiceFailure):
        service.voice_into("Hello.", pick, str(tmp_path / "three.wav"))
    assert len(fake.headers) == asked # straight to the backup, without asking ElevenLabs again

    fake.speak_error = None # credits added
    now = time.time()
    monkeypatch.setattr(online_service.time, "time", lambda: now + online_service.RETRY_MINUTES * 60)
    service.voice_into("Hello.", pick, str(tmp_path / "four.wav"))
    assert fake.spoken[-1][0] == "v1" and online_service.problem("ElevenLabs") == ""


def test_a_service_that_doesnt_answer_is_asked_once_then_the_backup_speaks_for_a_while(monkeypatch, voice_config, tmp_path, fake):
    import requests
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    asked = []

    def no_answer(*args, **kwargs):
        asked.append(args[0])
        raise requests.ConnectionError("no internet")
    monkeypatch.setattr(requests, "post", no_answer)
    for line in ("one", "two", "three"):
        with pytest.raises(VoiceServiceFailure): # the backup service speaks these lines
            service.voice_into("Hello.", pick, str(tmp_path / f"{line}.wav"))
    assert len(asked) == 1 # one clone tried, not one per try and per line
    assert "lost the connection to ElevenLabs" in online_service.problem("ElevenLabs")


def cut_off_lines(monkeypatch, how_many: int, error):
    """The next *how_many* lines sent are cut off with *error*; returns the lines sent"""
    import requests
    real_post = requests.post
    sent = []

    def post(url, *args, **kwargs):
        if "text-to-speech" not in url:
            return real_post(url, *args, **kwargs)
        sent.append(url)
        if len(sent) <= how_many:
            raise error
        return real_post(url, *args, **kwargs)
    monkeypatch.setattr(requests, "post", post)
    return sent


def test_a_line_cut_off_once_is_sent_once_more(monkeypatch, voice_config, tmp_path, fake):
    import requests
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav")) # cloned
    sent = cut_off_lines(monkeypatch, 1, requests.ConnectionError("Connection aborted."))
    service.voice_into("Hello.", pick, str(tmp_path / "two.wav")) # no failure: the backup service doesn't speak
    assert len(sent) == 2 and online_service.problem("ElevenLabs") == "" and service.why_not == ""


def test_a_line_cut_off_twice_lets_the_backup_speak_for_a_while(monkeypatch, voice_config, tmp_path, fake):
    import requests
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    sent = cut_off_lines(monkeypatch, 2, requests.ConnectionError("Connection aborted."))
    with pytest.raises(VoiceServiceFailure):
        service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert len(sent) == 2 and "lost the connection to ElevenLabs" in online_service.problem("ElevenLabs")


def test_a_line_not_answered_in_time_is_not_sent_again(monkeypatch, voice_config, tmp_path, fake):
    import requests
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    sent = cut_off_lines(monkeypatch, 1, requests.ReadTimeout("read timed out"))
    with pytest.raises(VoiceServiceFailure, match="didn't answer in time"): # the NPC doesn't wait that long twice
        service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert len(sent) == 1


def test_one_slow_line_doesnt_turn_the_service_off(monkeypatch, voice_config, tmp_path, fake):
    import requests
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    sent = cut_off_lines(monkeypatch, 1, requests.ReadTimeout("read timed out"))
    with pytest.raises(VoiceServiceFailure):
        service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert online_service.problem("ElevenLabs") == "" and service.why_not == ""
    service.voice_into("Hello.", pick, str(tmp_path / "three.wav")) # the next line is asked for again, and works
    assert len(sent) == 2


def test_two_slow_lines_in_a_row_let_the_backup_speak_for_a_while(monkeypatch, voice_config, tmp_path, fake):
    import requests
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    sent = cut_off_lines(monkeypatch, 2, requests.ReadTimeout("read timed out"))
    for line in ("two", "three", "four"):
        with pytest.raises(VoiceServiceFailure): # the backup service speaks these lines
            service.voice_into("Hello.", pick, str(tmp_path / f"{line}.wav"))
    assert len(sent) == 2 # the fourth line goes straight to the backup, without asking again
    assert "2 lines in a row" in online_service.problem("ElevenLabs") and "tries again in 5 minutes" in service.why_not
    assert "internet down" not in service.why_not


def test_a_slow_line_between_answered_ones_starts_counting_again(monkeypatch, voice_config, tmp_path, fake):
    import requests
    service = make(monkeypatch, voice_config, ElevenLabs)
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    for line in ("two", "three"):
        cut_off_lines(monkeypatch, 1, requests.ReadTimeout("read timed out"))
        with pytest.raises(VoiceServiceFailure):
            service.voice_into("Hello.", pick, str(tmp_path / f"{line}.wav"))
        service.voice_into("Hello.", pick, str(tmp_path / f"{line}-answered.wav"))
    assert online_service.problem("ElevenLabs") == ""


def test_a_longer_line_gets_a_longer_wait(monkeypatch, voice_config):
    service = make(monkeypatch, voice_config, ElevenLabs)
    short, longer = service.line_wait("Hello."), service.line_wait("word " * 60)
    assert 12 <= short < longer <= service.LONGEST_LINE_WAIT
    assert service.line_wait("word " * 1000) == service.LONGEST_LINE_WAIT


def test_an_old_problem_is_not_shown_as_right_now(monkeypatch):
    online_service.set_problem("Inworld", "Inworld can't speak.", online_service.SPEAKING)
    assert online_service.problem("Inworld") == "Inworld can't speak."
    online_service.set_problem("Inworld", None, online_service.CLONING) # a clone working says nothing about speaking
    assert online_service.problem("Inworld") == "Inworld can't speak."
    now = time.time()
    monkeypatch.setattr(online_service.time, "time", lambda: now + online_service.RETRY_MINUTES * 60 + 1)
    assert online_service.problem("Inworld") == "" # fixed meanwhile, or set again when it fails again


def test_voices_left_over_from_trying_clips_are_deleted_once(monkeypatch, voice_config, fake):
    store = cloned_voices(voice_config.save_folder, "Inworld")
    store.set("try_malenord", "old_try", "abc")
    store.set("malenord", "kept", "def")
    monkeypatch.setattr(online_service.OnlineCloneService, "_tidied", set())
    make(monkeypatch, voice_config, Inworld)
    assert fake.wait_for_deleted("old_try") and store.get("try_malenord") is None and store.get("malenord")["voice_id"] == "kept"


def test_only_a_service_that_spoke_in_game_counts_as_in_use(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, Inworld)
    service.serve_in_game() # started, as Hear the voice on the page does through the running Mantella
    service.voice_into("Hello.", a_voice(service, tmp_path, [1.0]), str(tmp_path / "one.wav"))
    assert service.info not in service._preparation.services_in_use()
    service.spoke_in_game = True # a line in game
    assert service.info in service._preparation.services_in_use()
    service.stop()
