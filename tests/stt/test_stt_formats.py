"""The OpenAI-style format against a stand-in service, and where keys come from. No internet, no cost"""
import socket
import time
from types import SimpleNamespace

import numpy as np
import pytest

from src import utils
from src.stt_backends import keys
from src.stt_backends.base import FAILED, KEY_REFUSED, NO_KEY, UNKNOWN_MODEL, UNREACHABLE, SpeechError
from src.stt_backends.cleanup import names_within
from src.stt_backends.keys import copy_old_key, key_for, save_key
from src.stt_backends.openai_style import OpenAIStyleFormat, endpoint
from src.stt_backends.presets import custom_preset, find
from tests.stt.fake_stt_server import FakeSTTServer, key_folders # noqa: F401 (a fixture)

AUDIO = np.zeros(16000, dtype=np.float32)


def fmt(address, key="", needs_key=False, sends_prompt=True, model="whisper-large-v3-turbo", timeout=8.0, default_model="whisper-large-v3-turbo"):
    return OpenAIStyleFormat("Groq", address, model, key, "GROQ_SECRET_KEY.txt", needs_key=needs_key, sends_prompt=sends_prompt,
                             default_model=default_model, timeout=timeout)


@pytest.mark.parametrize("address, expected", [
    ("https://api.groq.com/openai/v1", "https://api.groq.com/openai/v1/audio/transcriptions"),
    ("https://api.groq.com/openai/v1/", "https://api.groq.com/openai/v1/audio/transcriptions"),
    ("https://api.openai.com/v1/audio/transcriptions", "https://api.openai.com/v1/audio/transcriptions"),
    ("http://127.0.0.1:8080/inference", "http://127.0.0.1:8080/inference"),
    ("http://192.168.1.20:8000", "http://192.168.1.20:8000/v1/audio/transcriptions"),
    ("192.168.1.20:8000/v1", "http://192.168.1.20:8000/v1/audio/transcriptions"),
])
def test_address_short_or_full(address, expected):
    assert endpoint(address) == expected


def test_sends_the_form_with_the_key():
    with FakeSTTServer() as server:
        text = fmt(server.base + "/openai/v1", key="sk-test").transcribe(AUDIO, "This is a conversation with Balgruuf.", [], "en")
    assert text == "Balgruuf, have you seen Proventus?"
    request = server.requests[0]
    assert request["path"] == "/openai/v1/audio/transcriptions"
    assert request["headers"]["Authorization"] == "Bearer sk-test"
    assert request["fields"]["model"] == b"whisper-large-v3-turbo"
    assert request["fields"]["language"] == b"en"
    assert request["fields"]["prompt"] == b"This is a conversation with Balgruuf."
    assert request["fields"]["file"][:4] == b"RIFF"


def test_no_key_no_header_for_a_program_at_home():
    with FakeSTTServer() as server:
        fmt(server.base + "/inference", key="", model="").transcribe(AUDIO, "", [], "en")
    request = server.requests[0]
    assert request["path"] == "/inference"
    assert "Authorization" not in request["headers"]
    assert "model" not in request["fields"]


def test_mistral_gets_each_name_as_its_own_field_and_no_prompt():
    mistral = find("Mistral")
    with FakeSTTServer() as server:
        backend = OpenAIStyleFormat("Mistral", server.base + "/v1", mistral.default_model, "k", mistral.key_file, needs_key=True,
                                    sends_prompt=mistral.sends_prompt, names_field=mistral.names_field)
        backend.transcribe(AUDIO, "This is a conversation with Lydia and Hulda in Whiterun.", ["Lydia", "Hulda", "Whiterun"], "fr")
    request = server.requests[0]
    assert "prompt" not in request["fields"]
    assert request["fields"]["language"] == b"fr"
    assert request["fields"]["model"] == b"voxtral-mini-transcribe-3"
    assert request["all_fields"].count(("context_bias", b"Lydia")) == 1
    assert [value for name, value in request["all_fields"] if name == "context_bias"] == [b"Lydia", b"Hulda", b"Whiterun"]


def test_words_to_listen_for_stay_under_500_characters():
    names = [f"Name{i:03d}" for i in range(100)] # 7 characters each, 9 with ", "
    kept = names_within(names + ["Lydia", "lydia"])
    assert len(", ".join(kept)) <= 500
    assert kept == names[:len(kept)] # the most likely, first ones are kept
    assert names_within(["Lydia", "lydia", " ", "Hulda"]) == ["Lydia", "Hulda"]


def test_online_service_without_a_key_sends_nothing():
    with FakeSTTServer() as server:
        with pytest.raises(SpeechError) as error:
            fmt(server.base + "/v1", key="", needs_key=True).transcribe(AUDIO, "", [], "en")
    assert error.value.kind == NO_KEY
    assert "Key box" in error.value.message
    assert server.requests == []


@pytest.mark.parametrize("status, body, kind", [
    (401, {"error": {"message": "Incorrect API key provided: sk-proj-******************************-123. You can find your API key at https://platform.openai.com/account/api-keys."}}, KEY_REFUSED),
    (402, {"error": {"message": "Payment required"}}, KEY_REFUSED),
    (429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}}, KEY_REFUSED),
    (404, {"error": {"message": "The model `whisper-2` does not exist", "code": "model_not_found"}}, UNKNOWN_MODEL),
    (404, {"detail": "Not Found"}, UNREACHABLE),
    (400, {"error": {"message": "bad audio"}}, FAILED),
])
def test_problems_get_a_plain_message_and_no_retry(status, body, kind):
    with FakeSTTServer(answers=[(status, body)]) as server:
        with pytest.raises(SpeechError) as error:
            fmt(server.base + "/v1", key="sk-secret-123", model="whisper-2").transcribe(AUDIO, "", [], "en")
    assert error.value.kind == kind
    assert len(server.requests) == 1
    assert "sk-secret-123" not in error.value.message
    assert "-123" not in error.value.message and "sk-proj" not in error.value.message # not even the part a service repeats
    if kind == UNKNOWN_MODEL:
        assert "Try its recommended model, whisper-large-v3-turbo." in error.value.message


def test_no_suggestion_of_the_model_already_in_use():
    with FakeSTTServer(answers=[(404, {"error": {"message": "The model does not exist", "code": "model_not_found"}})]) as server:
        with pytest.raises(SpeechError) as error:
            fmt(server.base + "/v1").transcribe(AUDIO, "", [], "en")
    assert error.value.kind == UNKNOWN_MODEL and "recommended model" not in error.value.message


def test_the_recording_is_sent_as_16_bit_sound():
    from scipy.io import wavfile
    import io
    with FakeSTTServer() as server:
        fmt(server.base + "/v1").transcribe(np.array([0.0, 0.5, -1.0, 2.0], dtype=np.float32), "", [], "en")
    rate, samples = wavfile.read(io.BytesIO(server.requests[0]["fields"]["file"]))
    assert rate == 16000 and samples.dtype == np.int16
    assert list(samples) == [0, 16383, -32767, 32767] # too loud is clipped, not wrapped around


def test_keys_are_hidden_in_what_a_service_says():
    from src.stt_backends.openai_style import hide_keys
    said = hide_keys("Incorrect API key provided: sk-proj-****abcd. Key gsk_9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c rejected", "")
    assert "abcd" not in said and "gsk_9f8e" not in said
    assert "Incorrect API key provided" in said
    assert hide_keys("bad key my-secret-key-1234", "my-secret-key-1234") == "bad key (key hidden)"
    assert hide_keys("The model `whisper-large-v3-turbo` does not exist", "") == "The model `whisper-large-v3-turbo` does not exist"
    assert hide_keys("The model `voxtral-mini-transcribe-3` is unknown", "") == "The model `voxtral-mini-transcribe-3` is unknown"
    assert hide_keys("Deepgram key 3f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a invalid", "") == "Deepgram key (hidden) invalid"


def test_a_server_error_is_tried_once_more():
    with FakeSTTServer(answers=[(503, {"error": {"message": "busy"}})]) as server:
        text = fmt(server.base + "/v1", key="k").transcribe(AUDIO, "", [], "en")
    assert text == "Balgruuf, have you seen Proventus?"
    assert len(server.requests) == 2


def test_two_server_errors_give_up():
    with FakeSTTServer(answers=[(500, {}), (500, {})]) as server:
        with pytest.raises(SpeechError) as error:
            fmt(server.base + "/v1", key="k").transcribe(AUDIO, "", [], "en")
    assert error.value.kind == FAILED
    assert len(server.requests) == 2


def test_a_slow_service_gives_up_at_the_time_limit():
    with FakeSTTServer(delay=3) as server:
        start = time.monotonic()
        with pytest.raises(SpeechError) as error:
            fmt(server.base + "/v1", key="k", timeout=1).transcribe(AUDIO, "", [], "en")
        took = time.monotonic() - start
    assert error.value.kind == UNREACHABLE
    assert took < 2


def test_nothing_listening_is_tried_twice_then_unreachable():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1] # free, and closed once this block ends
    with pytest.raises(SpeechError) as error:
        fmt(f"http://127.0.0.1:{port}/v1", key="k", timeout=2).transcribe(AUDIO, "", [], "en")
    assert error.value.kind == UNREACHABLE


def test_made_up_phrases_are_thrown_away():
    with FakeSTTServer(text="Thanks for watching!") as server:
        assert fmt(server.base + "/v1", key="k").transcribe(AUDIO, "", [], "en") == ""


def test_key_lookup_order(key_folders):
    mod, _ = key_folders
    groq = find("Groq")
    assert key_for(groq, "Groq") == ("", "")
    (mod / "GPT_SECRET_KEY.txt").write_text("chat-key\n")
    assert key_for(groq, "OpenAI")[0] == "" # the chat key never goes to another service
    (mod / "STT_SECRET_KEY.txt").write_text("old-stt-key\n")
    assert key_for(groq, "OpenAI")[0] == "" # the old shared key is only read by the move
    (mod / "GROQ_SECRET_KEY.txt").write_text("groq-key\n")
    assert key_for(groq, "OpenAI") == ("groq-key", "GROQ_SECRET_KEY.txt")


def test_the_old_key_is_copied_once_to_the_service_in_use(key_folders):
    mod, _ = key_folders
    (mod / "GPT_SECRET_KEY.txt").write_text("chat-key\n")
    (mod / "STT_SECRET_KEY.txt").write_text("groq-key\n")
    assert copy_old_key("GROQ_SECRET_KEY.txt")
    assert (mod / "GROQ_SECRET_KEY.txt").read_text().strip() == "groq-key"
    (mod / "STT_SECRET_KEY.txt").write_text("another\n")
    assert not copy_old_key("GROQ_SECRET_KEY.txt") # a service that has a key keeps it
    assert (mod / "GROQ_SECRET_KEY.txt").read_text().strip() == "groq-key"


def test_chat_key_only_for_the_same_service(key_folders):
    mod, _ = key_folders
    (mod / "GPT_SECRET_KEY.txt").write_text("chat-key\n")
    openai = find("OpenAI")
    assert key_for(openai, "OpenRouter")[0] == ""
    assert key_for(openai, "OpenAI")[0] == "chat-key"
    custom = custom_preset("OpenAI-style", "http://127.0.0.1:8080/inference", "", 8)
    assert key_for(custom, "OpenAI")[0] == "" # never to an address the player typed


def test_key_box_saves_next_to_the_chat_key(key_folders):
    mod, mantella = key_folders
    (mod / "GPT_SECRET_KEY.txt").write_text("chat-key\n")
    saved = save_key("GROQ_SECRET_KEY.txt", "  groq-key  ")
    assert saved == mod / "GROQ_SECRET_KEY.txt"
    assert keys.read_key("GROQ_SECRET_KEY.txt") == "groq-key"
    save_key("GROQ_SECRET_KEY.txt", "")
    assert not (mod / "GROQ_SECRET_KEY.txt").exists()


def test_an_empty_key_box_never_deletes_the_saved_key(key_folders):
    from src.ui.stt_ui import save_from_box
    mod, _ = key_folders
    (mod / "GPT_SECRET_KEY.txt").write_text("chat-key\n")
    assert save_from_box("GROQ_SECRET_KEY.txt", " groq-key ")[0]
    saved, message = save_from_box("GROQ_SECRET_KEY.txt", "  ")
    assert not saved and message == "Paste a key first."
    assert keys.read_key("GROQ_SECRET_KEY.txt") == "groq-key"


def test_key_box_says_when_the_chat_key_is_used(key_folders):
    from src.ui.stt_ui import CHAT_KEY_PLACEHOLDER, EMPTY_PLACEHOLDER, SAVED_PLACEHOLDER, placeholder_for
    mod, _ = key_folders
    (mod / "GPT_SECRET_KEY.txt").write_text("chat-key\n")
    assert placeholder_for("OpenAI", False, "OpenAI") == CHAT_KEY_PLACEHOLDER
    assert placeholder_for("OpenAI", False, "OpenRouter") == EMPTY_PLACEHOLDER
    assert placeholder_for("Groq", False, "OpenAI") == EMPTY_PLACEHOLDER
    (mod / "OPENAI_SECRET_KEY.txt").write_text("openai-key\n")
    assert placeholder_for("OpenAI", False, "OpenAI") == SAVED_PLACEHOLDER
    assert placeholder_for(None, True, "OpenAI") == EMPTY_PLACEHOLDER


def test_a_model_that_failed_to_load_is_not_tried_on_every_line(monkeypatch):
    import faster_whisper
    from src.stt_backends.faster_whisper_program import FasterWhisperProgram
    tries = []
    def failing_model(*args, **kwargs):
        tries.append(args)
        raise RuntimeError("offline")
    monkeypatch.setattr(faster_whisper, "WhisperModel", failing_model)
    monkeypatch.setattr(utils, "play_error_sound", lambda: None)
    whisper = FasterWhisperProgram("large-v3-turbo")
    for _ in range(3):
        with pytest.raises(SpeechError):
            whisper.transcribe(AUDIO, "", [], "en")
    assert len(tries) == 1
    assert tries[0][0] == "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    whisper.forget_failed_load() # the Test button
    with pytest.raises(SpeechError):
        whisper.transcribe(AUDIO, "", [], "en")
    assert len(tries) == 2


def test_key_box_without_a_chat_key_uses_mantellas_folder(key_folders):
    _, mantella = key_folders
    assert save_key("DEEPGRAM_SECRET_KEY.txt", "dg") == mantella / "DEEPGRAM_SECRET_KEY.txt"


def stt_config(**changes):
    values = dict(stt_language="en", listen_timeout=30, play_cough_sound=False, proactive_mic_mode=True, min_refresh_secs=0.3,
                  pause_threshold=0.25, audio_threshold=0.4, save_mic_input=False, save_folder="", ptt_enabled=False, ptt_hotkey="V",
                  stt_service="Online Service", stt_online_service="Groq", stt_models={},
                  stt_custom_format="OpenAI-style", stt_custom_address="http://127.0.0.1:1/v1", stt_custom_model="", stt_custom_timeout=1.0,
                  whisper_process_device="cpu", stt_translate=False, moonshine_folder="", llm_api="OpenRouter")
    values.update(changes)
    return SimpleNamespace(**values)


def test_transcriber_online_no_key_does_not_quit_and_proactive_is_off(key_folders):
    from src.stt import Transcriber
    transcriber = Transcriber(stt_config(), "STT_SECRET_KEY.txt", "GPT_SECRET_KEY.txt")
    assert transcriber.proactive_mic_mode is False # every update would be billed
    assert transcriber._transcribe(AUDIO) == "" # no key: an empty line, so the cough sound plays
    custom = Transcriber(stt_config(stt_service="Custom Service"), "STT_SECRET_KEY.txt", "GPT_SECRET_KEY.txt")
    assert custom.proactive_mic_mode is True # Custom Service keeps the player's setting


def test_transcriber_uses_the_saved_key_without_a_rebuild(key_folders):
    from src.stt import Transcriber
    mod, _ = key_folders
    with FakeSTTServer() as server:
        config = stt_config(stt_service="Custom Service", stt_custom_address=server.base + "/v1")
        transcriber = Transcriber(config, "STT_SECRET_KEY.txt", "GPT_SECRET_KEY.txt")
        transcriber.names = ["Lydia"]
        transcriber._transcribe(AUDIO)
        (mod / "CUSTOM_STT_SECRET_KEY.txt").write_text("custom-key\n")
        transcriber.refresh_key(config)
        transcriber._transcribe(AUDIO)
    assert "Authorization" not in server.requests[0]["headers"]
    assert server.requests[1]["headers"]["Authorization"] == "Bearer custom-key"
