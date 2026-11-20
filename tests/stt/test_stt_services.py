"""Deepgram, ElevenLabs and OpenRouter: each request checked field by field against a pretend service, including every name arriving
as its own word to listen for (comma-joined names are silently ignored by Deepgram)"""
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import numpy as np
import pytest

from src.stt_backends import build_backend, chosen
from src.stt_backends.base import KEY_REFUSED, UNKNOWN_MODEL, SpeechError
from src.stt_backends.keys import key_for, save_key
from src.stt_backends.model_lists import speech_models
from src.stt_backends.presets import STTChoice, find, online_choices
from tests.stt.fake_stt_server import FakeSTTServer, key_folders # noqa: F401 (a fixture)

AUDIO = np.zeros(16000, dtype=np.float32)
NAMES = ["Balgruuf the Greater", "Proventus", "Dragonsreach"]
HINT = "Names: Balgruuf the Greater, Proventus, Dragonsreach."


def deepgram_reply(text):
    return {"metadata": {}, "results": {"channels": [{"alternatives": [{"transcript": text, "confidence": 0.98}]}]}}


def backend(name, address, model=None, llm_api=""):
    """The ready-made choice *name*, sending to the pretend service at *address*"""
    preset = replace(find(name), base_address=address)
    return build_backend(STTChoice(preset, model or preset.default_model), SimpleNamespace(llm_api=llm_api))


def query(request):
    return parse_qsl(urlsplit(request["path"]).query)


def test_the_online_service_list():
    assert online_choices() == ["OpenAI", "Groq", "Mistral", "Deepgram", "ElevenLabs", "OpenRouter"]


def test_deepgram_sends_the_recording_and_each_name_as_its_own_keyterm(key_folders):
    save_key("DEEPGRAM_SECRET_KEY.txt", "dg-key")
    with FakeSTTServer(reply=deepgram_reply) as server:
        text = backend("Deepgram", server.base + "/v1").transcribe(AUDIO, HINT, NAMES, "fr")
    assert text == "Balgruuf, have you seen Proventus?"
    request = server.requests[0]
    assert urlsplit(request["path"]).path == "/v1/listen"
    asked = query(request)
    assert ("model", "nova-3") in asked and ("language", "fr") in asked and ("punctuate", "true") in asked
    assert [value for name, value in asked if name == "keyterm"] == NAMES # never "Balgruuf the Greater, Proventus, Dragonsreach"
    assert not any(name in ("filler_words", "prompt") for name, _ in asked) # "um" and "uh" dropped (Deepgram's default)
    assert request["headers"]["Authorization"] == "Token dg-key"
    assert request["headers"]["Content-Type"] == "audio/wav"
    assert request["body"][:4] == b"RIFF"


def test_deepgram_keyterms_only_for_nova_3(key_folders):
    save_key("DEEPGRAM_SECRET_KEY.txt", "dg-key")
    with FakeSTTServer(reply=deepgram_reply) as server:
        backend("Deepgram", server.base + "/v1", model="nova-2").transcribe(AUDIO, HINT, NAMES, "en")
    assert not any(name == "keyterm" for name, _ in query(server.requests[0]))


@pytest.mark.parametrize("status, body, kind, words", [
    (401, {"err_code": "INVALID_AUTH", "err_msg": "Invalid credentials.", "request_id": "x"}, KEY_REFUSED, "Invalid credentials."),
    (400, {"err_code": "Bad Request", "err_msg": "No such model/language/tier combination found."}, UNKNOWN_MODEL, "No such model"),
])
def test_deepgram_problems_in_plain_words(key_folders, status, body, kind, words):
    save_key("DEEPGRAM_SECRET_KEY.txt", "dg-key")
    with FakeSTTServer(answers=[(status, body)]) as server:
        with pytest.raises(SpeechError) as problem:
            backend("Deepgram", server.base + "/v1").transcribe(AUDIO, HINT, NAMES, "en")
    assert problem.value.kind == kind and words in problem.value.message
    assert "DEEPGRAM_SECRET_KEY.txt" in problem.value.message or kind == UNKNOWN_MODEL


def test_deepgram_names_the_spoken_language_when_it_may_be_the_problem(key_folders):
    save_key("DEEPGRAM_SECRET_KEY.txt", "dg-key")
    answer = (400, {"err_code": "Bad Request", "err_msg": "No such model/language/tier combination found."})
    with FakeSTTServer(answers=[answer]) as server:
        with pytest.raises(SpeechError) as problem:
            backend("Deepgram", server.base + "/v1").transcribe(AUDIO, HINT, NAMES, "yo")
    message = problem.value.message
    assert "Spoken Language 'yo'" in message
    assert "recommended model" not in message # nova-3 is already the recommended one


def test_an_unexpected_answer_from_a_ready_made_choice(key_folders):
    save_key("DEEPGRAM_SECRET_KEY.txt", "dg-key")
    with FakeSTTServer(answers=[(200, {"results": {}})]) as server:
        with pytest.raises(SpeechError) as problem:
            backend("Deepgram", server.base + "/v1").transcribe(AUDIO, HINT, NAMES, "en")
    assert "Custom Service" not in problem.value.message and "another Online Service" in problem.value.message


def test_elevenlabs_sends_its_form_and_each_name_as_its_own_keyterms_field(key_folders):
    save_key("ELEVENLABS_SECRET_KEY.txt", "el-key")
    names = NAMES + ["Ulfric Stormcloak the Bear of Markarth Jarl", "x" * 51, "Hold [Rift]"] # too many words, too long, a character it refuses
    with FakeSTTServer() as server:
        text = backend("ElevenLabs", server.base + "/v1").transcribe(AUDIO, HINT, names, "de")
    assert text == "Balgruuf, have you seen Proventus?"
    request = server.requests[0]
    assert request["path"] == "/v1/speech-to-text"
    assert request["headers"]["xi-api-key"] == "el-key" and "Authorization" not in request["headers"]
    fields = request["fields"]
    assert fields["model_id"] == b"scribe_v3" and fields["language_code"] == b"de" and fields["tag_audio_events"] == b"false"
    assert fields["file"][:4] == b"RIFF"
    assert "prompt" not in fields
    assert [value.decode() for name, value in request["all_fields"] if name == "keyterms"] == NAMES


def test_elevenlabs_uses_the_voices_key_file():
    from src.tts.elevenlabs import KEY_FILE
    assert find("ElevenLabs").key_file == KEY_FILE


def test_elevenlabs_refused_key(key_folders):
    save_key("ELEVENLABS_SECRET_KEY.txt", "el-key")
    with FakeSTTServer(answers=[(401, {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}})]) as server:
        with pytest.raises(SpeechError) as problem:
            backend("ElevenLabs", server.base + "/v1").transcribe(AUDIO, HINT, NAMES, "en")
    assert problem.value.kind == KEY_REFUSED and "Invalid API key" in problem.value.message


def test_openrouter_takes_openais_form_without_names(key_folders):
    save_key("OPENROUTER_SECRET_KEY.txt", "or-key")
    with FakeSTTServer() as server:
        text = backend("OpenRouter", server.base + "/api/v1").transcribe(AUDIO, HINT, NAMES, "en")
    assert text == "Balgruuf, have you seen Proventus?"
    request = server.requests[0]
    assert request["path"] == "/api/v1/audio/transcriptions"
    assert request["headers"]["Authorization"] == "Bearer or-key"
    assert request["fields"]["model"] == b"openai/gpt-transcribe" and request["fields"]["language"] == b"en"
    assert set(request["fields"]) == {"file", "model", "language"} # OpenRouter has no prompt and no field for names


def test_openrouter_uses_the_chat_key_only_when_chatting_through_openrouter(key_folders):
    save_key("GPT_SECRET_KEY.txt", "chat-key")
    openrouter = find("OpenRouter")
    assert key_for(openrouter, "OpenRouter") == ("chat-key", "GPT_SECRET_KEY.txt")
    assert key_for(openrouter, "OpenAI") == ("", "")
    assert key_for(find("Deepgram"), "OpenRouter") == ("", "")


@pytest.mark.parametrize("format_name, address, path", [
    ("Deepgram-style", "/v1", "/v1/listen"),
    ("Deepgram-style", "", "/v1/listen"),
    ("ElevenLabs-style", "/v1/speech-to-text", "/v1/speech-to-text"),
])
def test_custom_service_in_another_format(key_folders, format_name, address, path):
    with FakeSTTServer(reply=deepgram_reply if format_name.startswith("Deepgram") else None) as server:
        settings = SimpleNamespace(stt_service="Custom Service", stt_custom_format=format_name, stt_custom_address=server.base + address,
                                   stt_custom_model="", stt_custom_timeout=8.0, llm_api="")
        text = build_backend(chosen(settings), settings).transcribe(AUDIO, HINT, NAMES, "en")
    assert text == "Balgruuf, have you seen Proventus?"
    request = server.requests[0]
    assert urlsplit(request["path"]).path == path
    assert "Authorization" not in request["headers"] and "xi-api-key" not in request["headers"] # no key typed: none sent


def test_model_lists(key_folders, monkeypatch):
    save_key("DEEPGRAM_SECRET_KEY.txt", "dg-key")
    deepgram_models = {"stt": [{"name": "nova-3", "canonical_name": "nova-3"}, {"name": "nova-3-medical", "canonical_name": "nova-3-medical"}],
                       "tts": [{"canonical_name": "aura-2-thalia-en"}]}
    with FakeSTTServer(models=deepgram_models) as server:
        found = speech_models(replace(find("Deepgram"), base_address=server.base + "/v1"))
    assert found == ["nova-3", "nova-2", "nova-3-medical"] # the speech models only
    assert server.requests[0]["path"] == "/v1/models" and server.requests[0]["headers"]["Authorization"] == "Token dg-key"

    openrouter_models = {"data": [{"id": "openai/gpt-transcribe"}, {"id": "nvidia/parakeet-tdt-0.6b-v3"}]}
    with FakeSTTServer(models=openrouter_models) as server: # no key: OpenRouter lists its speech models anyway
        found = speech_models(replace(find("OpenRouter"), base_address=server.base + "/api/v1"))
    assert found[:4] == list(find("OpenRouter").suggestions) and found[-1] == "nvidia/parakeet-tdt-0.6b-v3"
    assert server.requests[0]["path"] == "/api/v1/models?output_modalities=transcription"

    save_key("ELEVENLABS_SECRET_KEY.txt", "el-key")
    assert speech_models(find("ElevenLabs")) == ["scribe_v3", "scribe_v2"] # a fixed list, nothing asked
