"""HeyGen, Qwen, Boson AI and Iodine: each clip cloned once and reused, cloned again after a swap with the old voice deleted, a voice deleted on
the website cloned again, a refused clone falling back to built-in voices, Check my key, and each one's own way of writing sounds"""
import base64
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import soundfile as sf

from src.tts import online_service, qwen
from src.tts.boson import BosonAI
from src.tts.clip_service import ClipVoiceService, VoiceServiceFailure
from src.tts.heygen import HeyGen
from src.tts.iodine import Iodine
from src.tts.qwen import Qwen
from src.tts.registry import voice_services
from src.tts.voice_picker import BUILT_IN, VOICE_TYPE_CLIP, VoicePick, VoiceSpeaker
from tests.tts.fake_voice_server import tone_wav

SERVICES = (HeyGen, Qwen, BosonAI, Iodine)


class FakeServices:
    """Answers the HeyGen, Qwen, Boson AI and Iodine requests Mantella sends, and records them. HeyGen and Qwen make a voice in the background:
    the first time Mantella asks, it isn't ready yet. Their lines come back as a link on this same server"""

    def __init__(self) -> None:
        self.clones: list[tuple[str, dict]] = []
        self.spoken: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        self.gone: set[str] = set()
        self.refuse: tuple[int, dict] | None = None
        self.headers: list[dict] = []
        self.asked_ready: dict[str, int] = {}
        self.ready_after = 1 # how many times a new voice is asked about before it is ready
        self.failing = False # the service fails to make new voices
        self.free_gone = False # HeyGen has ended Turbo Free
        self.line_refusal: tuple[int, dict] | None = None # HeyGen refuses every line this way
        self.missing_models: set[str] = set() # HeyGen answers 404 "not found" for lines on these models
        self.clone_tries: list[dict] = [] # Iodine: every clone asked for, also refused ones
        self.refuse_transcript = False # Iodine turns down the words sent with the clips
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, code: int, body, content_type: str = "application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def request_json(self) -> dict:
                return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

            def new_voice(self, kind: str, request: dict) -> str:
                server.clones.append((kind, request))
                return f"{kind}{len(server.clones)}"

            def status(self, voice_id: str, ready: str, making: str, failed: str) -> str:
                server.asked_ready[voice_id] = server.asked_ready.get(voice_id, 0) + 1
                if server.failing:
                    return failed
                return ready if server.asked_ready[voice_id] > server.ready_after else making

            def form(self) -> dict:
                """A multipart form's fields; the file as {"file": its bytes}"""
                from email.parser import BytesParser
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                message = BytesParser().parsebytes(f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + body)
                fields = {}
                for part in message.get_payload():
                    name = part.get_param("name", header="content-disposition")
                    data = part.get_payload(decode=True)
                    fields[name] = data if name == "file" else data.decode()
                return fields

            def do_POST(self):
                server.headers.append(dict(self.headers))
                if self.path == "/voices": # Iodine
                    fields = self.form()
                    server.clone_tries.append(fields)
                    if server.refuse:
                        return self.send(*server.refuse)
                    if server.refuse_transcript and "transcript" in fields:
                        return self.send(400, {"error": {"code": "invalid_transcript", "message": "transcript doesn't match"}})
                    voice_id = self.new_voice("i", fields)
                    return self.send(201, {"voice": {"id": voice_id, "model": "v2", "transcriptSource": "provided" if "transcript" in fields else "auto"}})
                request = self.request_json()
                if self.path == "/v3/models/audio/voices": # HeyGen
                    if server.refuse:
                        return self.send(*server.refuse)
                    return self.send(202, {"data": {"voice_id": self.new_voice("h", request)}})
                if self.path == "/v3/models/audio/tts":
                    if server.free_gone and request["model"] == "heygen-voice-1-turbo-free":
                        return self.send(400, {"error": {"code": "invalid_parameter", "message": "Unsupported model", "param": "model"}})
                    if request["voice_id"] in server.gone:
                        return self.send(400, {"error": {"code": "invalid_parameter", "message": "unknown voice", "param": "voice_id"}})
                    if request["model"] in server.missing_models:
                        return self.send(404, {"error": {"code": "not_found", "message": f"model {request['model']} not found"}})
                    if server.line_refusal:
                        return self.send(*server.line_refusal)
                    server.spoken.append((request["voice_id"], request))
                    return self.send(200, {"data": {"audio_url": f"{server.url}/sound.wav", "duration": 1.0}})
                if self.path == "/services/audio/tts/customization": # Qwen
                    action = request["input"]["action"]
                    voice_id = request["input"].get("voice_id", "")
                    if action == "create_voice":
                        if server.refuse:
                            return self.send(*server.refuse)
                        return self.send(200, {"output": {"voice_id": self.new_voice("q", request)}, "request_id": "r"})
                    if action == "query_voice":
                        if voice_id in server.gone:
                            return self.send(400, {"code": "BadRequest.VoiceNotFound", "message": "Voice not found."})
                        return self.send(200, {"output": {"status": self.status(voice_id, "OK", "DEPLOYING", "UNDEPLOYED")}})
                    if action == "delete_voice":
                        server.deleted.append(voice_id)
                        return self.send(200, {"output": {}})
                    if action == "list_voice":
                        return self.send(200, {"output": {"voice_list": []}})
                if self.path == "/services/audio/tts/SpeechSynthesizer":
                    voice_id = request["input"]["voice"]
                    if voice_id in server.gone:
                        return self.send(400, {"code": "BadRequest.VoiceNotFound", "message": f"Voice '{voice_id}' not found."})
                    server.spoken.append((voice_id, request))
                    return self.send(200, {"output": {"audio": {"url": f"{server.url}/sound.wav"}, "finish_reason": "stop"}})
                if self.path == "/audio/voices": # Boson AI
                    if server.refuse:
                        return self.send(*server.refuse)
                    return self.send(200, {"voice_id": self.new_voice("b", request), "ref_text": request["ref_text"]})
                if self.path == "/tts": # Iodine
                    if request["voice"] in server.gone:
                        return self.send(400, {"error": {"code": "unknown_voice", "message": "no such voice"}})
                    server.spoken.append((request["voice"], request))
                    return self.send(200, tone_wav(), "audio/wav")
                if self.path == "/audio/speech":
                    if request["voice"] in server.gone:
                        return self.send(404, {"error": {"code": "voice_not_found", "message": "not found"}})
                    server.spoken.append((request["voice"], request))
                    return self.send(200, tone_wav(), "audio/wav")
                self.send(404, {})

            def do_DELETE(self):
                server.deleted.append(self.path.rsplit("/", 1)[-1])
                self.send(200, {})

            def do_GET(self):
                if self.path == "/sound.wav":
                    return self.send(200, tone_wav(), "audio/wav")
                if self.path.startswith("/v3/models/audio/voices/"):
                    voice_id = self.path.rsplit("/", 1)[-1]
                    if voice_id in server.gone:
                        return self.send(404, {"error": {"code": "voice_not_found", "message": "not found"}})
                    return self.send(200, {"data": {"voice_id": voice_id, "status": self.status(voice_id, "ACTIVE", "PENDING", "FAILED"),
                                                    "failure_reason": "INVALID_AUDIO"}})
                if self.path.startswith("/v3/models/audio/voices"):
                    if "type=public" in self.path:
                        return self.send(200, {"data": [{"voice_id": "public_male"}], "has_more": True})
                    return self.send(200, {"data": [{"voice_id": "h1"}, {"voice_id": "h2"}], "has_more": False})
                if self.path.startswith("/audio/voices/"):
                    voice_id = self.path.rsplit("/", 1)[-1]
                    return self.send(404, {}) if voice_id in server.gone else self.send(200, {"voice_id": voice_id})
                if self.path == "/audio/voices":
                    return self.send(200, {"object": "list", "data": [{"voice_id": "b1"}]})
                if self.path == "/voices": # Iodine: its own voices, one cloned before, and the ones cloned here
                    cloned = [kind_id for kind_id in (f"i{n}" for n, (kind, _) in enumerate(server.clones, start=1) if kind == "i")
                              if kind_id not in server.gone]
                    return self.send(200, {"voices": [{"id": "mia"}, {"id": "zander"}, {"id": "vc_old", "custom": True},
                                                      *({"id": voice_id, "custom": True} for voice_id in cloned)]})
                self.send(404, {})

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
def no_problems_left(monkeypatch):
    monkeypatch.setattr(online_service, "POLL_SECONDS", 0.0)
    monkeypatch.setattr(online_service, "CLONE_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(online_service, "_cloning", {})
    monkeypatch.setattr(online_service, "_failed", {})
    yield
    for service in SERVICES:
        online_service.set_problem(service.NAME, None)


@pytest.fixture
def fake(monkeypatch):
    server = FakeServices()
    for service in (HeyGen, BosonAI, Iodine):
        monkeypatch.setattr(service, "API_URL", server.url)
    monkeypatch.setattr(Qwen, "API_URL", server.url)
    monkeypatch.setattr(HeyGen, "_male_voice", None)
    yield server
    server.close()


@pytest.fixture
def heard(monkeypatch):
    """Speech-to-text, faked: the words of each clip Boson AI clones from, and how often a clip was heard"""
    calls: list[str] = []
    def hear(clip_paths, config):
        calls.extend(clip_paths)
        return [f"words {n}" for n, _ in enumerate(clip_paths, start=1)]
    monkeypatch.setattr("src.voice_clips.transcripts._hear", hear)
    return calls


def make(monkeypatch, voice_config, service_class, key: str = "secret", **settings):
    monkeypatch.setattr(online_service, "read_key_file", lambda name: key)
    monkeypatch.setattr("src.tts.voice_setups.read_key_file", lambda name: key)
    voice_config.qwen_workspace_id = "ws1"
    for name, value in settings.items():
        setattr(voice_config, name, value)
    return service_class(voice_config, voice_services.find(service_class.NAME))


def clip(tmp_path, name: str = "clip.wav", seconds: float = 0.5, rate: int = 24000) -> str:
    path = tmp_path / name
    path.write_bytes(tone_wav(seconds, rate))
    return str(path)


def test_all_four_are_listed_as_online_services():
    for name, alias in (("HeyGen", "heygen"), ("Qwen", "alibaba"), ("Boson AI", "higgs"), ("Iodine", "iodine")):
        info = voice_services.find(alias)
        assert info.name == name and info.runs_online and info.voice_tags is not None
    assert voice_services.find("HeyGen").accepts_ipa and not voice_services.find("Qwen").accepts_ipa


@pytest.mark.parametrize("service_class", SERVICES)
def test_a_clip_is_cloned_once_and_reused(monkeypatch, voice_config, tmp_path, fake, heard, service_class):
    service = make(monkeypatch, voice_config, service_class)
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path))
    service.voice_into("Hello there.", pick, str(tmp_path / "one.wav"))
    service.voice_into("Hello again.", pick, str(tmp_path / "two.wav"))
    assert len(fake.clones) == 1 and len(fake.spoken) == 2 and fake.spoken[0][0] == fake.spoken[1][0]
    assert make(monkeypatch, voice_config, service_class).voice_ready(pick) # the next run keeps it


@pytest.mark.parametrize("service_class", SERVICES)
def test_a_swapped_clip_is_cloned_again_and_the_old_voice_deleted(monkeypatch, voice_config, tmp_path, fake, heard, service_class):
    service = make(monkeypatch, voice_config, service_class)
    path = clip(tmp_path)
    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "malenord", path), str(tmp_path / "one.wav"))
    first = fake.spoken[-1][0]
    clip(tmp_path, seconds=0.7) # another line from the game, under the same name
    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "malenord", path), str(tmp_path / "two.wav"))
    assert len(fake.clones) == 2 and fake.spoken[-1][0] != first
    assert fake.wait_for_deleted(first)


@pytest.mark.parametrize("service_class", SERVICES)
def test_a_voice_deleted_on_the_website_is_cloned_again(monkeypatch, voice_config, tmp_path, fake, heard, service_class):
    service = make(monkeypatch, voice_config, service_class)
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    fake.gone.add(fake.spoken[-1][0])
    service.voice_into("Hello.", pick, str(tmp_path / "two.wav")) # the retry clones it again
    assert len(fake.clones) == 2 and fake.spoken[-1][0] not in fake.gone


@pytest.mark.parametrize("service_class,refusal", [
    (HeyGen, (400, {"error": {"code": "resource_limit_reached", "message": "voice clone allowance reached"}})),
    (Qwen, (429, {"code": "Throttling.AllocationQuota", "message": "Maximum voice-clone voice limit exceeded"})),
    (BosonAI, (403, {"error": {"code": "quota_exceeded", "message": "voice quota exceeded"}})),
    (Iodine, (400, {"error": {"code": "too_many_voices", "message": "voice limit reached"}})),
])
def test_a_refused_clone_uses_built_in_voices_and_says_why(monkeypatch, voice_config, tmp_path, fake, heard, service_class, refusal):
    service = make(monkeypatch, voice_config, service_class)
    fake.refuse = refusal
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    monkeypatch.setattr(ClipVoiceService, "pick_voice", lambda self, speaker, named_voice=None: pick) # the clip is there
    chosen = service.pick_voice(VoiceSpeaker(gender=1, voice_type="FemaleEvenToned")) # in game, before the line
    service.voice_into("Hello.", chosen, str(tmp_path / "one.wav")) # no failure: a built-in voice speaks
    assert "won't clone another voice" in service.why_not and not service.can_copy_voices
    assert fake.spoken[-1][0] == service.built_in_voice(1)


def test_check_my_key(monkeypatch, voice_config, fake):
    assert "workspace has 2 cloned voices" in HeyGen.check_key("secret", voice_config.save_folder)
    qwen.use_workspace("")
    assert "Workspace ID" in Qwen.check_key("secret", voice_config.save_folder)
    qwen.use_workspace("ws1")
    assert "Your key works" in Qwen.check_key("secret", voice_config.save_folder)
    assert "1 of 200 cloned voices" in BosonAI.check_key("secret", voice_config.save_folder)
    assert "1 of 50 cloned voices" in Iodine.check_key("secret", voice_config.save_folder) # its own voices don't count


def test_heygen_speaks_with_the_chosen_model_its_sounds_and_sound_symbols(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, HeyGen, heygen_model="Turbo Free", heygen_audio_tags=True)
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path))
    service.voice_into("[sigh] [laugh] Fine. [reset] Go.", pick, str(tmp_path / "one.wav"), use_word_lists=False)
    request = fake.spoken[-1][1]
    assert request["model"] == "heygen-voice-1-turbo-free" and request["text"] == "[sighs] Fine. Go." and request["language"] == "en"
    assert fake.headers[0]["X-Api-Key"] == "secret"
    clone = fake.clones[0][1]
    assert clone["mode"] == "instant" and clone["audio"][0]["type"] == "base64" and fake.asked_ready["h1"] == 2 # waited until ready
    assert service.format_ipa("Dovahkiin", "ˈdoʊvɑːkiːn") == '<pron ipa="/ˈdoʊvɑːkiːn/">Dovahkiin</pron>'


def test_heygen_speaks_with_the_voices_expressiveness(monkeypatch, voice_config, tmp_path, fake):
    from src.voice_clips.clip_store import VOICE_TYPES
    service = make(monkeypatch, voice_config, HeyGen, heygen_audio_tags=True)
    lydia, serana = (VoicePick(VOICE_TYPE_CLIP, key, clip(tmp_path, f"{key}.wav")) for key in ("femalenord", "femaleeventoned"))
    service.voice_into("Hello.", lydia, str(tmp_path / "one.wav"))
    assert fake.spoken[-1][1]["expressiveness_boost"] == 0.5 # the page's default
    service._clips.set_voice_setting(VOICE_TYPES, "femalenord", "heygen_expressiveness", 0.2) # this voice's own, from the Voice Clips page
    service.voice_into("Hello.", lydia, str(tmp_path / "two.wav"))
    service.voice_into("[angry] Go.", serana, str(tmp_path / "three.wav"), use_word_lists=False)
    assert [request["expressiveness_boost"] for _, request in fake.spoken[1:]] == [0.2, 0.5]
    assert fake.spoken[-1][1]["text"] == "[angry] Go."
    assert len(fake.clones) == 2 # Expressiveness doesn't clone a voice again


def test_heygen_sends_sound_symbols_only_for_a_cloned_voice_on_turbo(monkeypatch, voice_config, tmp_path, fake):
    """HeyGen refuses the whole line over a pron tag on Turbo Free or with its own voices, so those get the word's spelling"""
    from src.tts.word_lists import IPA, SPELLING
    service = make(monkeypatch, voice_config, HeyGen, heygen_model="Turbo Free")
    service._words.add_to_my_list("Zorgath", "Zor-gath", SPELLING)
    service._words.add_to_my_list("Zorgath", "ˈzɔːrɡæθ", IPA)
    lydia = VoicePick(VOICE_TYPE_CLIP, "femalenord", clip(tmp_path))
    service.voice_into("Hail, Zorgath.", lydia, str(tmp_path / "one.wav"))
    assert fake.spoken[-1][1]["text"] == "Hail, Zor-gath."
    service.voice_into('Hail, <pron ipa="/ˈzɔːrɡæθ/">Zorgath</pron>.', lydia, str(tmp_path / "two.wav"), use_word_lists=False)
    assert fake.spoken[-1][1]["text"] == "Hail, Zorgath." # a line that comes with the tag is still spoken
    voice_config.heygen_model = "Turbo"
    service.voice_into("Hail, Zorgath.", lydia, str(tmp_path / "three.wav"))
    assert fake.spoken[-1][1]["text"] == 'Hail, <pron ipa="/ˈzɔːrɡæθ/">Zorgath</pron>.'
    service.voice_into("Hail, Zorgath.", VoicePick(BUILT_IN, service.built_in_voice(1), None), str(tmp_path / "four.wav"))
    assert fake.spoken[-1][1]["text"] == "Hail, Zor-gath."


def test_heygen_hear_it_fixed_says_when_the_voice_cant_read_sound_symbols(monkeypatch, voice_config, tmp_path, fake):
    """Fix Pronunciation's Hear it fixed: the word in HeyGen's own pron tag, or a reason instead of the word as written"""
    from src.tts.clip_service import NoSoundSymbols
    service = make(monkeypatch, voice_config, HeyGen, heygen_model="Turbo Free")
    lydia = VoicePick(VOICE_TYPE_CLIP, "femalenord", clip(tmp_path))
    with pytest.raises(NoSoundSymbols, match="such as Turbo, not on Turbo Free"):
        service.voice_into("Say Zorgath.", lydia, str(tmp_path / "one.wav"), use_word_lists=False, ipa_word=("Zorgath", "/ˈzɔːrɡæθ/"))
    with pytest.raises(NoSoundSymbols, match="isn't cloned yet"):
        voice_config.heygen_model = "Turbo"
        service.voice_into("Say Zorgath.", VoicePick(BUILT_IN, service.built_in_voice(1), None), str(tmp_path / "two.wav"),
                           use_word_lists=False, ipa_word=("Zorgath", "/ˈzɔːrɡæθ/"))
    assert not fake.spoken
    service.voice_into("Say Zorgath.", lydia, str(tmp_path / "three.wav"), use_word_lists=False, ipa_word=("Zorgath", "/ˈzɔːrɡæθ/"))
    assert fake.spoken[-1][1]["text"] == 'Say <pron ipa="/ˈzɔːrɡæθ/">Zorgath</pron>.'


def test_heygen_clones_again_only_the_voice_whose_similarity_changed(monkeypatch, voice_config, tmp_path, fake):
    from src.voice_clips.clip_store import VOICE_TYPES
    service = make(monkeypatch, voice_config, HeyGen)
    lydia, serana = (VoicePick(VOICE_TYPE_CLIP, key, clip(tmp_path, f"{key}.wav")) for key in ("femalenord", "femaleeventoned"))
    service.voice_into("Hello.", lydia, str(tmp_path / "one.wav"))
    service.voice_into("Hello.", serana, str(tmp_path / "two.wav"))
    assert [request["similarity"] for _, request in fake.clones] == ["max", "max"] # Highest, the page's default
    first = fake.spoken[0][0]
    service._clips.set_voice_setting(VOICE_TYPES, "femalenord", "heygen_similarity", "Medium")
    service.voice_into("Hello.", lydia, str(tmp_path / "three.wav"))
    service.voice_into("Hello.", serana, str(tmp_path / "four.wav"))
    assert len(fake.clones) == 3 and fake.clones[-1][1]["similarity"] == "medium" and fake.clones[-1][1]["name"] == "Mantella femalenord"
    assert fake.spoken[-2][0] != first and fake.spoken[-1][0] == fake.spoken[1][0] # Serana's voice stays
    assert fake.wait_for_deleted(first)
    voice_config.heygen_similarity = "High" # on the Text-to-Speech page: every voice without its own is cloned again
    service.voice_into("Hello.", serana, str(tmp_path / "five.wav"))
    service.voice_into("Hello.", lydia, str(tmp_path / "six.wav"))
    assert len(fake.clones) == 4 and fake.clones[-1][1]["similarity"] == "high"


def test_heygen_looks_up_a_male_voice_of_its_own_once(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, HeyGen)
    voice = service.built_in_voice(0)
    service.voice_into("Hello.", VoicePick(BUILT_IN, voice, None), str(tmp_path / "one.wav"))
    service.voice_into("Hello.", VoicePick(BUILT_IN, voice, None), str(tmp_path / "two.wav"))
    assert [v for v, _ in fake.spoken] == ["public_male", "public_male"]


def test_qwen_clones_at_24_khz_and_takes_a_direction_as_its_instruction(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, Qwen, qwen_audio_tags=True, qwen_directions=True)
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path, rate=16000))
    service.voice_into("[say coldly] Take it, [whisper] and go. [sigh]", pick, str(tmp_path / "one.wav"), use_word_lists=False)
    fields = fake.spoken[-1][1]["input"]
    assert fields["text"] == "Take it, and go. [sighing]" and fields["instruction"] == "say coldly"
    assert fake.spoken[-1][1]["model"] == "qwen-audio-3.1-tts-plus" and fake.headers[0]["Authorization"] == "Bearer secret"
    clone = fake.clones[0][1]["input"]
    assert clone["target_model"] == "qwen-audio-3.1-tts-plus" and clone["prefix"] == "mantella" and clone["language_hints"] == ["en"]
    sample = base64.b64decode(clone["audio"].split(",", 1)[1])
    assert sf.info(io.BytesIO(sample)).samplerate >= 24000


def test_qwen_takes_directions_only_in_english(voice_config):
    voice_config.qwen_directions, voice_config.qwen_audio_tags = True, True
    assert qwen.voice_tags(voice_config).directions
    voice_config.language = "de"
    tags = qwen.voice_tags(voice_config)
    assert not tags.directions and tags.sounds # the sounds still work


def test_qwen_without_a_workspace_id_says_why(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, Qwen)
    voice_config.qwen_workspace_id = ""
    with pytest.raises(VoiceServiceFailure): # the backup service speaks
        service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path)), str(tmp_path / "one.wav"))
    assert "Workspace ID" in service.why_not and not fake.spoken


def test_boson_clones_with_the_words_of_each_clip_heard_once(monkeypatch, voice_config, tmp_path, fake, heard):
    service = make(monkeypatch, voice_config, BosonAI, boson_audio_tags=True)
    path = clip(tmp_path)
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", path)
    service.voice_into("[laugh] Ha. [say angrily] Go.", pick, str(tmp_path / "one.wav"), use_word_lists=False)
    # Lively, the page's Expressiveness, starts the line. [say angrily] is no mood of Boson's, so it is left out
    assert fake.clones[0][1]["ref_text"] == "words 1" and fake.spoken[-1][1]["input"] == "<|prosody:expressive_high|><|sfx:laughter|> Ha. Go."
    clip(tmp_path, seconds=0.7) # a new line for the voice: cloned again, its words heard once
    service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    make(monkeypatch, voice_config, BosonAI)._voices.pop("femaleeventoned") # cloned again with the same clip: heard before
    make(monkeypatch, voice_config, BosonAI).voice_into("Hello.", pick, str(tmp_path / "three.wav"))
    assert len(fake.clones) == 3 and heard == [path, path]


def test_boson_without_the_clips_words_uses_built_in_voices(monkeypatch, voice_config, tmp_path, fake):
    def cannot_hear(clip_paths, config):
        raise RuntimeError("Moonshine heard no words")
    monkeypatch.setattr("src.voice_clips.transcripts._hear", cannot_hear)
    service = make(monkeypatch, voice_config, BosonAI)
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    monkeypatch.setattr(ClipVoiceService, "pick_voice", lambda self, speaker, named_voice=None: pick)
    chosen = service.pick_voice(VoiceSpeaker(gender=0, voice_type="MaleNord"))
    service.voice_into("Hello.", chosen, str(tmp_path / "one.wav"))
    assert fake.spoken[-1][0] == "jake" and "Speech-to-Text couldn't hear them" in service.why_not and not fake.clones


@pytest.mark.parametrize("service_class,first", [(HeyGen, "h1"), (Qwen, "q1")])
def test_a_voice_still_being_made_is_asked_about_again_not_cloned_again(monkeypatch, voice_config, tmp_path, fake, service_class, first):
    service = make(monkeypatch, voice_config, service_class)
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    fake.ready_after = 10 ** 6 # the service is slow today
    with pytest.raises(VoiceServiceFailure, match="still cloning"): # the backup service speaks this line
        service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    with pytest.raises(VoiceServiceFailure):
        service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert len(fake.clones) == 1 and fake.deleted == [] # one voice on the account, kept for a later line
    fake.ready_after = 0 # made at last
    service.voice_into("Hello.", pick, str(tmp_path / "three.wav"))
    assert len(fake.clones) == 1 and fake.spoken[-1][0] == first


@pytest.mark.parametrize("service_class,first", [(HeyGen, "h1"), (Qwen, "q1")])
def test_a_voice_the_service_failed_to_make_is_deleted_there_and_not_cloned_again(monkeypatch, voice_config, tmp_path, fake, service_class, first):
    from src.tts import voice_notices
    voice_notices.take_notices()
    service = make(monkeypatch, voice_config, service_class)
    fake.failing = True # the service turns these clips down
    pick = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path))
    monkeypatch.setattr(ClipVoiceService, "pick_voice", lambda self, speaker, named_voice=None: pick) # the clip is there
    for line in ("one", "two", "three"):
        chosen = service.pick_voice(VoiceSpeaker(gender=0, voice_type="MaleNord"))
        service.voice_into("Hello.", chosen, str(tmp_path / f"{line}.wav")) # no failure: the built-in voice speaks
    assert len(fake.clones) == 1 and fake.wait_for_deleted(first) # cloned once, not on every line
    spoken = [voice for voice, _ in fake.spoken]
    assert len(spoken) == 3 and len(set(spoken)) == 1 and spoken[0] != first # the built-in voice (HeyGen's looked up)
    assert service.can_copy_voices # other NPCs still get cloned voices
    assert "couldn't clone the voice for malenord" in online_service.problem(service_class.NAME)
    assert any("couldn't clone some voices" in n for n in voice_notices.take_notices())
    with pytest.raises(VoiceServiceFailure, match="couldn't clone the voice"): # Hear the voice on the page says why, without cloning
        make(monkeypatch, voice_config, service_class).voice_into("Hello.", pick, str(tmp_path / "four.wav"))
    assert len(fake.clones) == 1


def test_heygen_says_when_turbo_free_has_ended(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, HeyGen, heygen_model="Turbo Free")
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    fake.free_gone = True # Nov 30, 2026
    with pytest.raises(VoiceServiceFailure): # the backup service speaks
        service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert "no longer takes Turbo Free" in service.why_not and "Pick Turbo" in online_service.problem("HeyGen")
    assert len(fake.clones) == 1 # not taken for a deleted voice


def test_boson_leaves_out_a_clip_with_no_words_heard(monkeypatch, voice_config, tmp_path, fake):
    monkeypatch.setattr("src.voice_clips.transcripts._hear", lambda clip_paths, config: ["" if "shout" in p else "I used to be an adventurer" for p in clip_paths])
    service = make(monkeypatch, voice_config, BosonAI)
    shout, line = clip(tmp_path, "shout.wav", 0.3), clip(tmp_path, "line.wav", 0.6)
    service.clone("Mantella malenord", [shout, line], "en")
    request = fake.clones[-1][1]
    assert request["ref_text"] == "I used to be an adventurer"
    assert base64.b64decode(request["ref_audio"]) == open(line, "rb").read() # the shout isn't in the sound either


def test_boson_with_no_words_in_one_voices_clips_keeps_cloning_the_others(monkeypatch, voice_config, tmp_path, fake):
    monkeypatch.setattr("src.voice_clips.transcripts._hear", lambda clip_paths, config: ["" if "shout" in p else "Hello there" for p in clip_paths])
    service = make(monkeypatch, voice_config, BosonAI)
    shout = VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path, "shout.wav"))
    monkeypatch.setattr(ClipVoiceService, "pick_voice", lambda self, speaker, named_voice=None: shout)
    service.voice_into("Hello.", service.pick_voice(VoiceSpeaker(gender=0, voice_type="MaleNord")), str(tmp_path / "one.wav"))
    assert fake.spoken[-1][0] == "jake" and not fake.clones and service.can_copy_voices
    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path, "line.wav", 0.8)), str(tmp_path / "two.wav"))
    assert len(fake.clones) == 1 and fake.spoken[-1][0] == "b1"


def test_qwen_without_a_workspace_id_says_so_in_game_and_on_its_page(monkeypatch, voice_config, fake):
    from src.tts import voice_notices
    voice_notices.take_notices()
    service = make(monkeypatch, voice_config, Qwen)
    voice_config.qwen_workspace_id = ""
    qwen.use_workspace("")
    service.serve_in_game()
    assert any("no Workspace ID" in n for n in voice_notices.take_notices())
    service.stop()


# ---- settings per model, and models Mantella doesn't know (Step 11) ----

def test_settings_mantella_doesnt_have_yet_go_with_every_line(monkeypatch, voice_config, tmp_path, fake):
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path))
    service = make(monkeypatch, voice_config, HeyGen, heygen_extra_request='{"speed": 1.1, "expressiveness_boost": 0.1}')
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    request = fake.spoken[-1][1]
    assert request["speed"] == 1.1 and request["expressiveness_boost"] == 0.1 # the player's fields win
    qwen_service = make(monkeypatch, voice_config, Qwen, qwen_extra_request='{"input": {"rate": 1.2}, "parameters": {"seed": 7}}')
    qwen_service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    request = fake.spoken[-1][1]
    assert request["input"]["rate"] == 1.2 and request["input"]["text"] == "Hello." and request["parameters"] == {"seed": 7} # merged, not replaced
    make(monkeypatch, voice_config, HeyGen, heygen_extra_request="not json").voice_into("Hello.", pick, str(tmp_path / "three.wav"))
    assert "speed" not in fake.spoken[-1][1] # unreadable: nothing extra is sent


def test_a_line_refused_for_the_players_fields_or_a_typed_model_says_why(monkeypatch, voice_config, tmp_path, fake):
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path))
    make(monkeypatch, voice_config, HeyGen).voice_into("Hello.", pick, str(tmp_path / "zero.wav")) # the voice is cloned
    fake.line_refusal = (400, {"error": {"code": "invalid_parameter", "message": "unknown field bogus", "param": "bogus"}})
    with pytest.raises(VoiceServiceFailure): # a model on the list, with nothing extra: as before, only this line
        make(monkeypatch, voice_config, HeyGen).voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    assert not online_service.problem("HeyGen")
    service = make(monkeypatch, voice_config, HeyGen, heygen_extra_request='{"bogus": 1}')
    with pytest.raises(VoiceServiceFailure): # the backup service speaks
        service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert 'HeyGen refused lines on Turbo: "unknown field bogus"' in online_service.problem("HeyGen")
    assert "Settings Mantella doesn't have yet for Turbo" in online_service.problem("HeyGen") # on the page; in game, the usual notice
    online_service.set_problem("HeyGen", None)
    service = make(monkeypatch, voice_config, HeyGen, heygen_extra_request="", heygen_model="heygen-voice-2")
    with pytest.raises(VoiceServiceFailure): # a typed model, refused for something else: only this line
        service.voice_into("Hello.", pick, str(tmp_path / "three.wav"))
    assert not online_service.problem("HeyGen")
    fake.line_refusal = (400, {"error": {"code": "invalid_parameter", "message": "Unsupported model", "param": "model"}})
    with pytest.raises(VoiceServiceFailure):
        service.voice_into("Hello.", pick, str(tmp_path / "four.wav"))
    assert "refused lines on heygen-voice-2" in online_service.problem("HeyGen") and "model's name" in online_service.problem("HeyGen")


def test_a_typed_model_the_service_says_it_doesnt_have_pauses_it_and_keeps_the_voice(monkeypatch, voice_config, tmp_path, fake):
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path))
    make(monkeypatch, voice_config, HeyGen).voice_into("Hello.", pick, str(tmp_path / "zero.wav")) # the voice is cloned
    online_service.set_problem("HeyGen", None)
    fake.missing_models.add("heygen-voice-9")
    asked = []
    monkeypatch.setattr(HeyGen, "voice_exists", classmethod(lambda cls, key, voice_id: asked.append(voice_id) or True))
    service = make(monkeypatch, voice_config, HeyGen, heygen_model="heygen-voice-9")
    with pytest.raises(VoiceServiceFailure):
        service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    assert "refused lines on heygen-voice-9" in online_service.problem("HeyGen") # not "the voice is gone"
    assert not asked and len(fake.clones) == 1 # the voice wasn't checked or cloned again


def test_a_typed_model_is_sent_as_typed(monkeypatch, voice_config, tmp_path, fake):
    service = make(monkeypatch, voice_config, HeyGen, heygen_model="heygen-voice-2")
    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path)), str(tmp_path / "one.wav"))
    assert fake.spoken[-1][1]["model"] == "heygen-voice-2" and "expressiveness_boost" in fake.spoken[-1][1] # every setting
    assert not service.takes_ipa("h1") # sound symbols only where Mantella's list says so


def test_qwen_clones_a_voice_again_for_another_model_and_heygen_doesnt(monkeypatch, voice_config, tmp_path, fake):
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path))
    service = make(monkeypatch, voice_config, Qwen)
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    voice_config.qwen_model = "qwen-audio-3.2-tts-plus" # a newer model, typed in
    service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert [request["input"]["target_model"] for _, request in fake.clones] == ["qwen-audio-3.1-tts-plus", "qwen-audio-3.2-tts-plus"]
    assert fake.spoken[-1][1]["model"] == "qwen-audio-3.2-tts-plus" and fake.wait_for_deleted("q1") # the old model's voice goes
    heygen_service = make(monkeypatch, voice_config, HeyGen)
    heygen_service.voice_into("Hello.", pick, str(tmp_path / "three.wav"))
    voice_config.heygen_model = "Turbo Free"
    heygen_service.voice_into("Hello.", pick, str(tmp_path / "four.wav"))
    assert len([c for c in fake.clones if c[0] == "h"]) == 1 # HeyGen's voices work on every model


def test_qwens_built_in_voices_come_from_mantellas_list(monkeypatch, voice_config):
    service = make(monkeypatch, voice_config, Qwen)
    assert (service.built_in_voice(0), service.built_in_voice(1)) == ("xunanchuan_v3.1", "longanhuan_v3.1")
    voice_config.qwen_model = "qwen-audio-9-tts" # not on the list: Qwen Audio 3.1's, the best guess
    assert service.built_in_voice(1) == "longanhuan_v3.1"


def test_a_model_that_skips_a_setting_doesnt_get_it(monkeypatch, voice_config, tmp_path):
    from src.tts.inworld import Inworld
    sent = []
    class Answer:
        status_code, text = 200, ""
        def json(self):
            return {"audioContent": base64.b64encode(tone_wav()).decode()}
    monkeypatch.setattr("src.tts.inworld.requests.post", lambda url, headers, json, timeout: sent.append(json) or Answer())
    service = make(monkeypatch, voice_config, Inworld, inworld_model="TTS-2 Flash", inworld_delivery="Creative")
    service.speak_with("Hello.", "Alex", str(tmp_path / "one.wav"))
    assert "deliveryMode" not in sent[-1] and sent[-1]["modelId"] == "inworld-tts-2-flash"
    voice_config.inworld_model = "inworld-tts-3" # typed in: every setting
    service.speak_with("Hello.", "Alex", str(tmp_path / "two.wav"))
    assert sent[-1]["deliveryMode"] == "CREATIVE" and sent[-1]["modelId"] == "inworld-tts-3"


# ---- Iodine ----

def test_iodine_clones_with_the_words_of_each_clip_and_consent(monkeypatch, voice_config, tmp_path, fake, heard):
    service = make(monkeypatch, voice_config, Iodine)
    path = clip(tmp_path)
    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", path), str(tmp_path / "one.wav"))
    fields = fake.clone_tries[-1]
    assert fields["transcript"] == "words 1" and fields["consent"] == "true" and fields["language"] == "auto"
    assert fields["name"] == "Mantella femaleeventoned" and fields["file"] == open(path, "rb").read()


def test_iodine_leaves_out_a_clip_with_no_words_heard(monkeypatch, voice_config, tmp_path, fake):
    monkeypatch.setattr("src.voice_clips.transcripts._hear", lambda clip_paths, config: ["" if "shout" in p else "I used to be an adventurer" for p in clip_paths])
    service = make(monkeypatch, voice_config, Iodine)
    shout, line = clip(tmp_path, "shout.wav", 0.3), clip(tmp_path, "line.wav", 0.6)
    service.clone("Mantella malenord", [shout, line], "en")
    fields = fake.clone_tries[-1]
    assert fields["transcript"] == "I used to be an adventurer" and fields["file"] == open(line, "rb").read()


def test_iodine_clones_without_words_when_speech_to_text_cant_hear_them(monkeypatch, voice_config, tmp_path, fake):
    def cannot_hear(clip_paths, config):
        raise RuntimeError("Moonshine heard no words")
    monkeypatch.setattr("src.voice_clips.transcripts._hear", cannot_hear)
    service = make(monkeypatch, voice_config, Iodine)
    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path)), str(tmp_path / "one.wav"))
    assert "transcript" not in fake.clone_tries[-1] and fake.spoken[-1][0] == "i1" # Iodine hears the clips itself


def test_iodine_tries_once_more_without_words_it_turns_down(monkeypatch, voice_config, tmp_path, fake, heard):
    fake.refuse_transcript = True
    service = make(monkeypatch, voice_config, Iodine)
    service.voice_into("Hello.", VoicePick(VOICE_TYPE_CLIP, "malenord", clip(tmp_path)), str(tmp_path / "one.wav"))
    assert ["transcript" in fields for fields in fake.clone_tries] == [True, False] and fake.spoken[-1][0] == "i1"


def test_iodine_speaks_with_the_voices_temperature_its_language_and_its_cues(monkeypatch, voice_config, tmp_path, fake, heard):
    from src.tts.voice_style import IODINE_TEMPERATURE, ServiceStyle
    from src.voice_clips.clip_store import VOICE_TYPES
    service = make(monkeypatch, voice_config, Iodine, iodine_audio_tags=True, iodine_directions=True, iodine_temperature=0.7, language="de")
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path))
    service.voice_into("[nervous] [quietly] Hallo. [whisper]Leise[/whisper] [pause] bitte.", pick, str(tmp_path / "one.wav"), use_word_lists=False)
    request = fake.spoken[-1][1]
    assert request["text"] == "{nervous} Hallo. {whisper}Leise{/whisper} {pause} bitte." # a mood and a direction side by side: the first
    assert request["temperature"] == 0.7 and request["language"] == "de" and request["model"] == "v2" and request["format"] == "wav"
    ServiceStyle(voice_config, "Iodine").set_voice_own(service._clips, VOICE_TYPES, "femaleeventoned", IODINE_TEMPERATURE, 1.5)
    service.voice_into("Hallo.", pick, str(tmp_path / "two.wav"))
    assert fake.spoken[-1][1]["temperature"] == 1.5 # the voice's own, for v2
    voice_config.language = "ha" # Iodine doesn't speak Hausa: it hears the language from the words
    service.voice_into("Hallo.", pick, str(tmp_path / "three.wav"))
    assert "language" not in fake.spoken[-1][1]


def test_iodine_without_credit_pauses_and_says_where_to_add_some(monkeypatch, voice_config, tmp_path, fake, heard):
    service = make(monkeypatch, voice_config, Iodine)
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clip(tmp_path))
    service.voice_into("Hello.", pick, str(tmp_path / "one.wav"))
    class NoCredit:
        status_code, reason, content, text = 402, "Payment Required", b"", ""
        def json(self):
            return {"error": {"code": "insufficient_credits", "message": "out of credits"}}
    monkeypatch.setattr("src.tts.iodine.requests.post", lambda *args, **kwargs: NoCredit())
    with pytest.raises(VoiceServiceFailure): # the backup service speaks
        service.voice_into("Hello.", pick, str(tmp_path / "two.wav"))
    assert "no credit left" in online_service.problem("Iodine") and "billing" in online_service.problem("Iodine")


def test_iodine_counts_only_cloned_voices_as_full(monkeypatch, fake):
    monkeypatch.setattr("src.tts.iodine.VOICE_LIMIT", 1)
    assert Iodine.plan_is_full("secret") is True # vc_old fills it; mia and zander are Iodine's own
    monkeypatch.setattr("src.tts.iodine.VOICE_LIMIT", 2)
    assert Iodine.plan_is_full("secret") is False
