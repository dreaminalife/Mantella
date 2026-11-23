"""Pocket TTS: the sign-in check, the program download, falling back to built-in voices, saved voices handed over through Mantella's web address"""
import hashlib
import io
import logging
import os
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.http.routes.voice_files_route import voice_files_route
from src.tts import pocket_tts
from src.tts.registry import voice_services
from src.tts.voice_picker import BUILT_IN, VOICE_TYPE_CLIP, VoicePick, VoiceSpeaker
from src.voice_clips.clip_store import VOICE_TYPES, ClipInfo, shared_clip_store
from src.tts.voice_folders import voice_clips_folder

GAME_LINE = os.path.join(os.path.dirname(__file__), "..", "voice_clips", "data", "dialoguesolitude__000dd60f_1.fuz")


def answer(status: int):
    return lambda *args, **kwargs: SimpleNamespace(status_code=status)


@pytest.mark.parametrize("status,expected", [(302, pocket_tts.ACCESS_OK), (401, pocket_tts.ACCESS_BAD_TOKEN), (403, pocket_tts.ACCESS_NO_TERMS),
                                             (500, pocket_tts.ACCESS_UNKNOWN)])
def test_the_sign_in_check(monkeypatch, status, expected):
    seen = {}
    def head(url, headers, timeout, allow_redirects):
        seen.update(url=url, headers=headers)
        return SimpleNamespace(status_code=status)
    monkeypatch.setattr(pocket_tts.requests, "head", head)
    assert pocket_tts.check_access("hf_token", "english") == expected
    assert "kyutai/pocket-tts/resolve/db04f0e653473e5c7333c69686ac92b7460b0c1d/languages/english/model.safetensors" in seen["url"]
    assert seen["headers"] == {"Authorization": "Bearer hf_token"}


def test_no_token_is_known_without_asking(monkeypatch):
    monkeypatch.setattr(pocket_tts.requests, "head", lambda *a, **k: pytest.fail("asked Hugging Face"))
    assert pocket_tts.check_access("", "french") == pocket_tts.ACCESS_NO_TOKEN


def test_the_language_setting_picks_kyutais_language():
    assert pocket_tts.kyutai_language("de") == "german" and pocket_tts.kyutai_language("en") == "english"
    assert voice_services.find("pocket tts").name == "Pocket TTS" and voice_services.find("Pocket TTS").speaks("nl")
    assert not voice_services.find("Pocket TTS").speaks("ja")


class FakeDownload:
    def __init__(self, data: bytes):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def raise_for_status(self):
        pass
    def iter_content(self, size):
        yield self.data


def zipped_program() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("pocket-tts/pocket-tts.exe", b"MZ")
    return buffer.getvalue()


def test_the_program_is_downloaded_once_and_checked(monkeypatch, tmp_path):
    data = zipped_program()
    downloads = []
    monkeypatch.setattr(pocket_tts.requests, "get", lambda *a, **k: downloads.append(1) or FakeDownload(data))
    monkeypatch.setattr(pocket_tts, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest())
    exe = pocket_tts.install(str(tmp_path))
    assert exe.endswith(os.path.join("pocket-tts", "pocket-tts.exe")) and pocket_tts.PROGRAM_VERSION in exe
    assert pocket_tts.install(str(tmp_path)) == exe and len(downloads) == 1


def test_a_damaged_download_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(pocket_tts.requests, "get", lambda *a, **k: FakeDownload(zipped_program()))
    with pytest.raises(RuntimeError, match="checksum"):
        pocket_tts.install(str(tmp_path))
    assert not any(name.endswith(".exe") for _, _, files in os.walk(tmp_path) for name in files)


@pytest.fixture(autouse=True)
def fresh_program(monkeypatch):
    """Each test starts with no Kyutai program running"""
    monkeypatch.setattr(pocket_tts, "_program", pocket_tts._Program())


def make_service(monkeypatch, voice_config, access: str):
    voice_config.port = 4999
    monkeypatch.setattr(pocket_tts, "has_avx2", lambda: True)
    monkeypatch.setattr(pocket_tts, "read_key_file", lambda name: "hf_token")
    monkeypatch.setattr(pocket_tts, "check_access", lambda token, language: access)
    exe = os.path.join(voice_config.save_folder, "pocket-tts.exe")
    monkeypatch.setattr(pocket_tts, "install", lambda folder: exe)
    launches = []
    monkeypatch.setattr(pocket_tts.PocketTTS, "_PocketTTS__launch", lambda self: launches.append(1))
    service = pocket_tts.PocketTTS(voice_config, voice_services.find("Pocket TTS"))
    service.launches = launches
    assert service._PocketTTS__ready.wait(5)
    return service


def add_clip(voice_config, tmp_path) -> None:
    from src.voice_clips import fuz
    wav = str(tmp_path / "clip.wav")
    fuz.to_wav(GAME_LINE, wav)
    shared_clip_store(voice_clips_folder(voice_config.save_folder)).set_clip(VOICE_TYPES, "femaleeventoned", wav, ClipInfo(name="FemaleEvenToned"))


def test_without_access_npcs_use_built_in_voices(monkeypatch, voice_config, tmp_path):
    add_clip(voice_config, tmp_path)
    service = make_service(monkeypatch, voice_config, pocket_tts.ACCESS_NO_TERMS)
    pick = service.pick_voice(VoiceSpeaker(gender=1, voice_type="FemaleEvenToned"))
    assert pick.kind == BUILT_IN and pick.key == "alba"
    from src.tts.voice_notices import take_notices
    assert any("built-in voices" in notice for notice in take_notices()) # told once in game


def test_a_clip_becomes_a_saved_voice_once_handed_over_by_web_address(monkeypatch, voice_config, tmp_path):
    add_clip(voice_config, tmp_path)
    service = make_service(monkeypatch, voice_config, pocket_tts.ACCESS_OK)
    assert service.launches == [1] # started at once, so the model downloads before the first line is due
    monkeypatch.setattr(pocket_tts.PocketTTS, "is_ready", lambda self: True)
    exports = []
    def run(command, **kwargs):
        exports.append(command)
        with open(command[3], "wb") as file:
            file.write(b"voice")
        assert kwargs["env"]["HF_TOKEN"] == "hf_token"
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(pocket_tts.subprocess, "run", run)
    pick = service.pick_voice(VoiceSpeaker(gender=1, voice_type="FemaleEvenToned"))
    url = service.prepared_voice(pick)
    assert url.startswith("http://127.0.0.1:4999/voices/english/femaleeventoned-") and "?v=" in url
    assert service.prepared_voice(pick) == url and len(exports) == 1
    assert exports[0][1] == "export-voice" and exports[0][-3:] == ["--language", "english", "-q"]

    app = FastAPI()
    voice_files_route(SimpleNamespace(save_folder=voice_config.save_folder)).add_route_to_server(app)
    client = TestClient(app)
    path = url.split("4999", 1)[1].split("?")[0]
    assert client.get(path).content == b"voice"
    assert client.get("/voices/english/..%2F..%2Fconfig.ini").status_code == 404


REFUSAL = "Error: We could not download the weights for the model with voice cloning, but you're trying to use voice cloning."


@pytest.mark.parametrize("asked_again,copies_voices", [(pocket_tts.ACCESS_NO_TERMS, False), (pocket_tts.ACCESS_BAD_TOKEN, False),
                                                       (pocket_tts.ACCESS_OK, True), (pocket_tts.ACCESS_UNKNOWN, True)])
def test_refused_voice_copying_asks_hugging_face_why(monkeypatch, voice_config, tmp_path, asked_again, copies_voices):
    add_clip(voice_config, tmp_path)
    service = make_service(monkeypatch, voice_config, pocket_tts.ACCESS_UNKNOWN)
    monkeypatch.setattr(pocket_tts.PocketTTS, "is_ready", lambda self: True)
    monkeypatch.setattr(pocket_tts.subprocess, "run", lambda command, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=REFUSAL))
    monkeypatch.setattr(pocket_tts, "check_access", lambda token, language: asked_again)
    pick = service.pick_voice(VoiceSpeaker(gender=0, voice_type="FemaleEvenToned"))
    with pytest.raises(RuntimeError):
        service.prepared_voice(pick)
    # Only a sign-in problem switches to built-in voices; a failed download is tried again with the next line
    assert service.can_copy_voices == copies_voices
    next_pick = service.pick_voice(VoiceSpeaker(gender=0, voice_type="FemaleEvenToned"))
    assert (next_pick.key != "jean") == copies_voices


def test_a_line_is_sent_as_a_form(monkeypatch, voice_config, tmp_path):
    service = make_service(monkeypatch, voice_config, pocket_tts.ACCESS_OK)
    sent = {}
    def post(url, data, timeout):
        sent.update(url=url, data=data)
        return SimpleNamespace(status_code=200, content=b"RIFF....", text="")
    monkeypatch.setattr(pocket_tts.requests, "post", post)
    out = str(tmp_path / "out.wav")
    service.speak("Hello there.", "alba", out)
    assert sent == {"url": f"http://127.0.0.1:{pocket_tts.PORT}/tts", "data": {"text": "Hello there.", "voice_url": "alba"}}
    assert open(out, "rb").read() == b"RIFF...."


def a_voice_file(folder: str, name: str) -> str:
    path = os.path.join(folder, name)
    os.makedirs(folder, exist_ok=True)
    open(path, "w").close()
    return path


def test_a_saved_voice_moves_with_its_clip(tmp_path):
    clip = str(tmp_path / "clip.wav")
    with open(clip, "wb") as file:
        file.write(b"lydia's clip")
    folder = pocket_tts.voices_folder(str(tmp_path), "english")
    digest = pocket_tts.clip_hash(clip)
    a_voice_file(folder, f"skyrimesm000a2c94-{digest}.safetensors")
    a_voice_file(folder, "femaleeventoned-0000000000000000.safetensors")
    pocket_tts.move_saved_voice(str(tmp_path), "skyrimesm000a2c94", "femaleeventoned", clip)
    assert os.listdir(folder) == [f"femaleeventoned-{digest}.safetensors"]


def test_a_removed_voices_saved_voices_go(tmp_path):
    folder = pocket_tts.voices_folder(str(tmp_path), "english")
    for name in ("skyrimesm000a2c94-1.safetensors", "skyrimesm000a2c94-2.safetensors", "femaleeventoned-3.safetensors"):
        a_voice_file(folder, name)
    pocket_tts.remove_saved_voices(str(tmp_path), "skyrimesm000a2c94")
    assert os.listdir(folder) == ["femaleeventoned-3.safetensors"]


def test_a_saved_voice_removed_from_disk_is_made_again(monkeypatch, voice_config, tmp_path):
    add_clip(voice_config, tmp_path)
    service = make_service(monkeypatch, voice_config, pocket_tts.ACCESS_OK)
    monkeypatch.setattr(pocket_tts.PocketTTS, "is_ready", lambda self: True)
    exports = []
    def run(command, **kwargs):
        exports.append(command)
        with open(command[3], "wb") as file:
            file.write(b"voice")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(pocket_tts.subprocess, "run", run)
    pick = service.pick_voice(VoiceSpeaker(gender=1, voice_type="FemaleEvenToned"))
    service.prepared_voice(pick)
    os.remove(exports[0][3])
    service.prepared_voice(pick)
    assert len(exports) == 2 and os.path.exists(exports[1][3])


def test_saved_voices_survive_a_start_and_old_versions_go(tmp_path):
    saved = os.path.join(pocket_tts.voices_folder(str(tmp_path), "english"), "femaleeventoned-1.safetensors")
    os.makedirs(os.path.dirname(saved))
    open(saved, "w").close()
    old_program = os.path.join(pocket_tts.program_folder(str(tmp_path)), "v3.0.0")
    old_voices = os.path.join(pocket_tts.program_folder(str(tmp_path)), "voices", "v3.0.0")
    os.makedirs(old_program)
    os.makedirs(old_voices)
    pocket_tts._remove_other_versions(str(tmp_path))
    assert os.path.exists(saved) and not os.path.exists(old_program) and not os.path.exists(old_voices)


def test_while_the_model_downloads_the_backup_speaks_at_once(monkeypatch, voice_config):
    service = make_service(monkeypatch, voice_config, pocket_tts.ACCESS_OK)
    pocket_tts._program.process = SimpleNamespace(poll=lambda: None) # running, not answering yet
    pocket_tts._program.settings = service._PocketTTS__settings()
    monkeypatch.setattr(pocket_tts.PocketTTS, "_PocketTTS__answers", lambda self: False)
    with pytest.raises(RuntimeError, match="still getting ready"):
        service.start()
    with pytest.raises(RuntimeError, match="still getting ready"): # no voice is made before the model is there
        service.prepare_voice(VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", GAME_LINE))


def downloading_with_no_backup(monkeypatch, voice_config, answers_after: int):
    """A service whose program runs and answers after *answers_after* checks, with None as the Backup Voice Service"""
    voice_config.tts_backup_service_name = "None"
    service = make_service(monkeypatch, voice_config, pocket_tts.ACCESS_OK)
    pocket_tts._program.process = SimpleNamespace(poll=lambda: None)
    pocket_tts._program.settings = service._PocketTTS__settings()
    checks = []
    def answers(self):
        checks.append(1)
        return len(checks) > answers_after
    monkeypatch.setattr(pocket_tts.PocketTTS, "_PocketTTS__answers", answers)
    monkeypatch.setattr(pocket_tts.PocketTTS, "WAIT_STEP_SECONDS", 0.01)
    return service, checks


def test_with_no_backup_a_line_waits_for_the_download(monkeypatch, voice_config, caplog):
    service, checks = downloading_with_no_backup(monkeypatch, voice_config, answers_after=5)
    launched = len(service.launches) # the setup starts it
    with caplog.at_level(logging.DEBUG):
        service.start() # doesn't fail: the line is spoken once the model is in
        service.start()
    assert len(checks) > 5 and service.is_ready()
    assert caplog.text.count("no Backup Voice Service is picked, so the NPC's line waits for it") == 1
    assert len(service.launches) == launched # the running program is waited for, not started again


def test_a_waiting_line_stops_with_the_reply(monkeypatch, voice_config):
    service, _ = downloading_with_no_backup(monkeypatch, voice_config, answers_after=10**9)
    service.stop_waiting = lambda: True # the player ended the conversation
    with pytest.raises(RuntimeError, match="still starting"):
        service.start()


def test_a_waiting_line_gives_up_after_the_longest_wait(monkeypatch, voice_config):
    service, _ = downloading_with_no_backup(monkeypatch, voice_config, answers_after=10**9)
    monkeypatch.setattr(pocket_tts.PocketTTS, "DOWNLOAD_WAIT_SECONDS", 0.05)
    with pytest.raises(RuntimeError, match="still starting"):
        service.start()


def test_a_copy_already_running_is_used(monkeypatch, voice_config):
    monkeypatch.setattr(pocket_tts, "has_avx2", lambda: True)
    monkeypatch.setattr(pocket_tts, "read_key_file", lambda name: "")
    monkeypatch.setattr(pocket_tts, "check_access", lambda token, language: pocket_tts.ACCESS_NO_TOKEN)
    monkeypatch.setattr(pocket_tts, "install", lambda folder: "pocket-tts.exe")
    monkeypatch.setattr(pocket_tts, "_answers", lambda: True)
    monkeypatch.setattr(pocket_tts.subprocess, "Popen", lambda *a, **k: pytest.fail("started a second copy"))
    voice_config.port = 4999
    service = pocket_tts.PocketTTS(voice_config, voice_services.find("Pocket TTS"))
    assert service._PocketTTS__ready.wait(5)
    service.start()
    assert service.is_ready()


class FakeProcess:
    def __init__(self, command):
        self.command = command
        self.stopped = False
        self.pid = 0
        self.stdout = io.BytesIO(b"") # what the program prints
    def poll(self):
        return 0 if self.stopped else None
    def terminate(self):
        self.stopped = True
    def wait(self, timeout=None):
        return 0


def real_launches(monkeypatch, voice_config, language="en"):
    """Services that start the program for real, with a fake program behind it"""
    started = []
    def popen(command, **kwargs):
        started.append(FakeProcess(command))
        return started[-1]
    monkeypatch.setattr(pocket_tts.subprocess, "Popen", popen)
    monkeypatch.setattr(pocket_tts, "_answers", lambda: False)
    monkeypatch.setattr(pocket_tts, "has_avx2", lambda: True)
    monkeypatch.setattr(pocket_tts, "read_key_file", lambda name: "hf_token")
    monkeypatch.setattr(pocket_tts, "check_access", lambda token, language: pocket_tts.ACCESS_OK)
    monkeypatch.setattr(pocket_tts, "install", lambda folder: os.path.join(voice_config.save_folder, "pocket-tts.exe"))
    voice_config.port = 4999
    return started


def new_service(voice_config, language="en"):
    voice_config.language = language
    service = pocket_tts.PocketTTS(voice_config, voice_services.find("Pocket TTS"))
    assert service._PocketTTS__ready.wait(5)
    return service


def downloaded_model(hub, repository: str, language: str):
    folder = hub / repository / "snapshots" / "db04f0e653473e5c7333c69686ac92b7460b0c1d" / "languages" / language
    folder.mkdir(parents=True)
    (folder / "model.safetensors").write_bytes(b"")


def test_whether_the_model_is_on_this_pc(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert not pocket_tts._model_on_pc("english", with_cloning=True)
    downloaded_model(tmp_path, "models--kyutai--pocket-tts-without-voice-cloning", "english")
    assert pocket_tts._model_on_pc("english", with_cloning=False)
    assert not pocket_tts._model_on_pc("english", with_cloning=True) # the sign-in gets the model that copies voices, not there yet
    downloaded_model(tmp_path, "models--kyutai--pocket-tts", "english")
    assert pocket_tts._model_on_pc("english", with_cloning=True)
    assert not pocket_tts._model_on_pc("german", with_cloning=True)


def test_the_first_start_says_the_model_downloads(monkeypatch, voice_config, tmp_path, caplog):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty"))
    started = real_launches(monkeypatch, voice_config)
    with caplog.at_level(logging.DEBUG):
        service = new_service(voice_config)
    assert len(started) == 1
    assert "Pocket TTS: downloading Kyutai's voice model for english (about 220 MB)" in caplog.text
    service.stop()


def test_a_later_start_doesnt(monkeypatch, voice_config, tmp_path, caplog):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    downloaded_model(tmp_path, "models--kyutai--pocket-tts", "english")
    real_launches(monkeypatch, voice_config)
    with caplog.at_level(logging.DEBUG):
        service = new_service(voice_config)
    assert "Pocket TTS: starting (english)" in caplog.text and "downloading Kyutai's voice model" not in caplog.text
    service.stop()


def test_the_log_says_when_it_is_ready(tmp_path, caplog):
    output = b"INFO: Loading model from config at english.yaml...\nINFO: Started server process [1]\n" \
             b"INFO: Server running on http://127.0.0.1:8030 (Press CTRL+C to quit)\n"
    log_path = tmp_path / "pocket-tts.log"
    with caplog.at_level(logging.DEBUG):
        pocket_tts._follow_output(SimpleNamespace(stdout=io.BytesIO(output)), str(log_path), 0.0, True)
    assert log_path.read_bytes() == output # the program's own lines are still in its log file
    assert caplog.text.count("Pocket TTS: ready, NPCs can speak with it") == 1 and "with the download" in caplog.text


def test_its_output_is_read_to_the_end_when_the_log_file_cant_be_written(tmp_path, caplog):
    stdout = io.BytesIO(b"INFO: Server running on http://127.0.0.1:8030\nmore\n")
    with caplog.at_level(logging.DEBUG):
        pocket_tts._follow_output(SimpleNamespace(stdout=stdout), str(tmp_path / "missing folder" / "pocket-tts.log"), 0.0, False)
    assert stdout.read() == b"" # nothing left in the pipe for the program to wait on
    assert "Pocket TTS: ready" in caplog.text and "with the download" not in caplog.text


def test_a_settings_change_keeps_the_running_program(monkeypatch, voice_config):
    started = real_launches(monkeypatch, voice_config)
    old = new_service(voice_config)
    new = new_service(voice_config) # a settings change makes a new service, then stops the old one
    old.stop()
    assert len(started) == 1 and not started[0].stopped # the model stays loaded, so no line goes to the backup voice
    new.stop()
    new.stop()
    assert started[0].stopped # the last service gone: the program stops


def test_a_new_language_starts_the_program_again(monkeypatch, voice_config):
    started = real_launches(monkeypatch, voice_config)
    old = new_service(voice_config, "en")
    new = new_service(voice_config, "de")
    old.stop()
    assert len(started) == 2 and started[0].stopped and not started[1].stopped
    assert started[1].command[-2:] == ["--language", "german"]


def test_a_service_stopped_before_its_setup_is_done_starts_nothing(monkeypatch, voice_config):
    started = real_launches(monkeypatch, voice_config)
    gate = __import__("threading").Event()
    monkeypatch.setattr(pocket_tts, "check_access", lambda token, language: gate.wait(5) and pocket_tts.ACCESS_OK)
    voice_config.language = "en"
    service = pocket_tts.PocketTTS(voice_config, voice_services.find("Pocket TTS"))
    service.stop()
    gate.set()
    assert service._PocketTTS__ready.wait(5)
    assert started == []


@pytest.fixture
def mantella_folders(tmp_path, monkeypatch):
    """A Mantella install inside a mod folder: <mod>/SKSE/Plugins/MantellaSoftware, run from another folder"""
    from src import utils
    mod = tmp_path / "mod"
    program = mod / "SKSE" / "Plugins" / "MantellaSoftware"
    program.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(utils, "resolve_path", lambda: str(program))
    monkeypatch.chdir(elsewhere)
    return mod, program


def test_the_token_is_saved_next_to_the_ai_models_key(mantella_folders):
    from src.tts.voice_setups import read_key_file, save_key_file
    mod, program = mantella_folders
    (program / "GPT_SECRET_KEY.txt").write_text("sk-1")
    saved = save_key_file(pocket_tts.KEY_FILE, "  hf_first \n")
    assert saved == program / pocket_tts.KEY_FILE and read_key_file(pocket_tts.KEY_FILE) == "hf_first"
    (mod / pocket_tts.KEY_FILE).write_text("hf_old") # a token file already in the place read first is the one replaced
    assert save_key_file(pocket_tts.KEY_FILE, "hf_new") == mod / pocket_tts.KEY_FILE
    assert read_key_file(pocket_tts.KEY_FILE) == "hf_new"


def test_without_any_key_file_the_token_goes_in_mantellas_own_folder(mantella_folders):
    from src.tts.voice_setups import save_key_file
    _, program = mantella_folders
    assert save_key_file(pocket_tts.KEY_FILE, "hf_x") == program / pocket_tts.KEY_FILE


def test_save_token_writes_it_checks_it_and_restarts_pocket_tts(mantella_folders, monkeypatch, tmp_path):
    import gradio as gr
    from src.ui import voice_clips_ui
    _, program = mantella_folders
    changed = []
    config = SimpleNamespace(language="en", notify_voice_services_changed=lambda: changed.append(1))
    monkeypatch.setattr(pocket_tts, "check_access", lambda token, language: pocket_tts.ACCESS_OK if token == "hf_good" else pocket_tts.ACCESS_NO_TERMS)
    with gr.Blocks() as blocks:
        voice_clips_ui.render_pocket_tts_panel(config)
    handlers = {f.fn.__name__: f.fn for f in blocks.fns}
    assert handlers["on_save"]("  ")[1] == "Paste your token first." and not changed
    box, message = handlers["on_save"](" hf_good ")
    assert box == "" and "Your sign-in works" in message and changed == [1]
    assert (program / pocket_tts.KEY_FILE).read_text().strip() == "hf_good"
    assert handlers["on_check"]().startswith("Your sign-in works") # reads the saved file
    password_boxes = [b for b in blocks.blocks.values() if isinstance(b, gr.Textbox) and b.type == "password"]
    assert len(password_boxes) == 1 and password_boxes[0].value == ""


def test_a_saved_voice_from_an_earlier_run_counts_as_ready_and_one_made_ahead_runs_below_normal_priority(monkeypatch, voice_config, tmp_path):
    add_clip(voice_config, tmp_path)
    service = make_service(monkeypatch, voice_config, pocket_tts.ACCESS_OK)
    monkeypatch.setattr(pocket_tts.PocketTTS, "is_ready", lambda self: True)
    flags = []
    def run(command, **kwargs):
        flags.append(kwargs["creationflags"])
        with open(command[3], "wb") as file:
            file.write(b"voice")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(pocket_tts.subprocess, "run", run)
    pick = service.pick_voice(VoiceSpeaker(gender=1, voice_type="FemaleEvenToned"))
    assert not service.voice_ready(pick)
    monkeypatch.setattr(pocket_tts, "in_background", lambda: True)
    service.prepare_voice(pick)
    assert flags[0] & pocket_tts.subprocess.BELOW_NORMAL_PRIORITY_CLASS
    assert service.voice_ready(pick)
    again = make_service(monkeypatch, voice_config, pocket_tts.ACCESS_OK) # Mantella started again
    assert again.voice_ready(pick)


def test_making_one_voice_never_holds_up_a_line_in_another(monkeypatch, voice_config):
    import threading
    from src.tts.voice_picker import VOICE_TYPE_CLIP, VoicePick
    service = make_service(monkeypatch, voice_config, pocket_tts.ACCESS_OK)
    started, release = threading.Event(), threading.Event()
    def prepare(self, pick):
        if pick.key == "slow":
            started.set()
            release.wait(5)
        return f"voice:{pick.key}"
    monkeypatch.setattr(pocket_tts.PocketTTS, "prepare_voice", prepare)
    slow, other = VoicePick(VOICE_TYPE_CLIP, "slow"), VoicePick(VOICE_TYPE_CLIP, "other")
    making = threading.Thread(target=service.prepared_voice, args=(slow,)) # Prepare all voices, making a voice
    making.start()
    assert started.wait(5)
    assert service.prepared_voice(other) == "voice:other" # a line in another voice goes ahead
    release.set()
    making.join(5)
    assert service.prepared_voice(slow) == "voice:slow"
