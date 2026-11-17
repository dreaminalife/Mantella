"""ElevenLabs and Inworld: each clip cloned once on the player's account and reused, cloned again after a swap with the old voice deleted,
a voice deleted on the website cloned again, a refused clone falling back to built-in voices, and the Voice Clips page's tried voices"""
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
from src.tts.online_service import cost_per_conversation
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
                    return self.send(200, json.dumps({"voice_limit": 30, "voice_slots_used": 4, "can_use_instant_voice_cloning": True}).encode())
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
    assert cost_per_conversation(40.0) == "about $0.12 per conversation of 30 lines"


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


def test_a_new_key_forgets_voices_from_the_old_account(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, ElevenLabs, key="first")
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    assert not make(monkeypatch, voice_config, ElevenLabs, key="second").voice_ready(pick)


def test_a_tried_clip_is_kept_when_cloned_from_and_deleted_otherwise(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, ElevenLabs)
    keeper = ElevenLabs.keeper()
    used, tried, other = clip(tmp_path, "used.wav"), clip(tmp_path, "tried.wav", 0.6), clip(tmp_path, "other.wav", 0.8)
    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "malenord", used), str(tmp_path / "a.wav"))
    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "try_malenord", tried), str(tmp_path / "b.wav")) # Hear the cloned voice
    open(used, "wb").write(open(tried, "rb").read()) # Clone from this clip
    keeper.use_trial_voice(voice_config.save_folder, "malenord", used)
    store = cloned_voices(voice_config.save_folder, "ElevenLabs")
    assert store.get("malenord")["voice_id"] == "v2" and store.get("try_malenord") is None
    assert b"Mantella malenord" in fake.clones[1]["body"] and b"trying" not in fake.clones[1]["body"] # nothing marks the kept voice as spare
    assert fake.wait_for_deleted("v1") # the voice from the old clip

    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "try_malenord", other), str(tmp_path / "c.wav"))
    keeper.remove_trial_voices(voice_config.save_folder, keep="femaleeventoned") # another voice picked on the page
    assert fake.wait_for_deleted("v3") and store.get("malenord")["voice_id"] == "v2"


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


def test_an_old_problem_is_not_shown_as_right_now(monkeypatch):
    online_service.set_problem("Inworld", "Inworld can't speak.", online_service.SPEAKING)
    assert online_service.problem("Inworld") == "Inworld can't speak."
    online_service.set_problem("Inworld", None, online_service.CLONING) # a clone working says nothing about speaking
    assert online_service.problem("Inworld") == "Inworld can't speak."
    now = time.time()
    monkeypatch.setattr(online_service.time, "time", lambda: now + online_service.RETRY_MINUTES * 60 + 1)
    assert online_service.problem("Inworld") == "" # fixed meanwhile, or set again when it fails again
