"""The speech-to-text settings: moving old setups over, which choice the settings pick, and settings shown for a choice"""
import configparser
from types import SimpleNamespace

import pytest

from src.config.stt_carry_over import carried_over_stt
from src.stt_backends.presets import chosen
from tests.stt.fake_stt_server import key_folders # noqa: F401 (a fixture)
from src.config.types.config_value import condition_holds, shown_when_conditions


def test_external_openai_moves_to_online_service_on_the_new_model():
    changes, message, key_file = carried_over_stt({"external_whisper_service": "True", "whisper_url": "OpenAI"},
                                                  {"stt_service": "Whisper", "whisper_model_size": "whisper-1"})
    assert changes == {"stt_service": "Online Service", "stt_online_service": "OpenAI", "stt_model_openai": "gpt-transcribe",
                       "whisper_model_size": "base"}
    assert "Online Service" in message
    assert key_file == "OPENAI_SECRET_KEY.txt"


def test_external_groq_keeps_its_model():
    changes, _, key_file = carried_over_stt({"external_whisper_service": "True", "whisper_url": "Groq"},
                                            {"stt_service": "Whisper", "whisper_model_size": "whisper-large-v3"})
    assert key_file == "GROQ_SECRET_KEY.txt"
    assert changes["stt_service"] == "Online Service"
    assert changes["stt_online_service"] == "Groq"
    assert changes["stt_model_groq"] == "whisper-large-v3"
    assert changes["whisper_model_size"] == "base" # a Groq name Whisper on the PC can't run


def test_external_openai_with_a_pc_model_gets_the_recommended_model():
    # The old setup sent "base" to OpenAI, which never worked
    changes, _, _ = carried_over_stt({"external_whisper_service": "True", "whisper_url": "OpenAI"},
                                     {"stt_service": "Whisper", "whisper_model_size": "base"})
    assert changes["stt_model_openai"] == "gpt-transcribe"
    changes, _, _ = carried_over_stt({"external_whisper_service": "True", "whisper_url": "Groq"},
                                     {"stt_service": "Whisper", "whisper_model_size": "large-v3"})
    assert changes["stt_model_groq"] == "whisper-large-v3-turbo"


def test_external_groq_address_typed_in_full():
    changes, _, _ = carried_over_stt({"external_whisper_service": "True", "whisper_url": "https://api.groq.com/openai/v1/"},
                                  {"stt_service": "Whisper", "whisper_model_size": "whisper-large-v3-turbo"})
    assert changes["stt_online_service"] == "Groq"


@pytest.mark.parametrize("url, address", [
    ("whisper.cpp", "http://127.0.0.1:8080/inference"),
    ("http://192.168.1.20:8000/v1", "http://192.168.1.20:8000/v1"),
])
def test_external_program_moves_to_custom_service(url, address):
    changes, message, key_file = carried_over_stt({"external_whisper_service": "True", "whisper_url": url},
                                                  {"stt_service": "Whisper", "whisper_model_size": "Systran/faster-whisper-large-v3"})
    assert changes == {"stt_service": "Custom Service", "stt_custom_format": "OpenAI-style", "stt_custom_address": address,
                       "stt_custom_model": "Systran/faster-whisper-large-v3", "whisper_model_size": "base"}
    assert "Custom Service" in message
    assert key_file == "" # the old code never sent the key to these addresses, so it isn't moved there


def test_a_typed_openai_address_gets_the_old_key():
    changes, _, key_file = carried_over_stt({"external_whisper_service": "True", "whisper_url": "https://my-proxy.example/openai/v1"},
                                            {"stt_service": "Whisper", "whisper_model_size": "whisper-1"})
    assert changes["stt_service"] == "Custom Service"
    assert key_file == "CUSTOM_STT_SECRET_KEY.txt"


def test_external_off_moves_nothing_but_an_online_only_model():
    changes, _, key_file = carried_over_stt({"external_whisper_service": "False", "whisper_url": "OpenAI"},
                                            {"stt_service": "Moonshine", "whisper_model_size": "gpt-4o-transcribe"})
    assert changes == {"whisper_model_size": "base"}
    assert key_file == ""


def test_external_on_but_moonshine_moves_nothing():
    assert carried_over_stt({"external_whisper_service": "True", "whisper_url": "Groq"},
                            {"stt_service": "Moonshine", "whisper_model_size": "base"}) == ({}, "", "")


def test_a_retired_openai_model_moves_to_gpt_transcribe_on_any_start():
    changes, _, _ = carried_over_stt({}, {"stt_model_openai": "gpt-4o-mini-transcribe", "whisper_model_size": "whisper-1"})
    assert changes == {"stt_model_openai": "gpt-transcribe"} # the Whisper model is only fixed while the old settings are there


def test_nothing_to_move():
    assert carried_over_stt({}, {"stt_service": "Whisper", "whisper_model_size": "large-v3-turbo"}) == ({}, "", "")


def test_config_ini_is_moved_and_rewritten_once(tmp_path, key_folders):
    from src.config.config_loader import ConfigLoader
    mod, _ = key_folders
    (mod / "STT_SECRET_KEY.txt").write_text("openai-key\n")
    (tmp_path / "config.ini").write_text("[STT]\nstt_service = Whisper\nwhisper_model_size = whisper-1\nexternal_whisper_service = True\n"
                                         "whisper_url = OpenAI\nstt_language = default\n", encoding="utf-8")
    config = ConfigLoader(str(tmp_path), prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    assert config.stt_service == "Online Service"
    assert config.stt_online_service == "OpenAI"
    assert config.stt_models["stt_model_openai"] == "gpt-transcribe"
    assert config.stt_models["whisper_model_size"] == "base"
    written = configparser.ConfigParser()
    written.read(tmp_path / "config.ini", encoding="utf-8")
    keys = {key for section in written.sections() for key in written[section]}
    assert "external_whisper_service" not in keys and "whisper_url" not in keys
    assert written["STT"]["stt_service"] == "Online Service"
    choice = chosen(config)
    assert (choice.preset.name, choice.model) == ("OpenAI", "gpt-transcribe")
    from src.stt_backends.keys import read_key
    assert read_key("OPENAI_SECRET_KEY.txt") == "openai-key" # the old key moved with the setup


def settings(**changes):
    values = dict(stt_service="Whisper", stt_online_service="Groq", stt_models={"whisper_model_size": "large-v3-turbo", "stt_model_groq": "whisper-large-v3"},
                  stt_custom_format="OpenAI-style", stt_custom_address="http://127.0.0.1:8080/inference",
                  stt_custom_model="", stt_custom_timeout=20.0, whisper_process_device="cuda", stt_translate=False, moonshine_folder="")
    values.update(changes)
    return SimpleNamespace(**values)


def test_each_choice_keeps_its_own_model():
    assert (chosen(settings()).preset.name, chosen(settings()).model) == ("Whisper", "large-v3-turbo")
    online = chosen(settings(stt_service="Online Service"))
    assert (online.preset.name, online.model) == ("Groq", "whisper-large-v3")
    openai = chosen(settings(stt_service="Online Service", stt_online_service="OpenAI"))
    assert (openai.preset.name, openai.model) == ("OpenAI", "gpt-transcribe") # never used before: its recommended model


def test_custom_service_is_a_choice_in_the_list():
    choice = chosen(settings(stt_service="Custom Service"))
    assert choice.preset.name == "Custom Service"
    assert choice.preset.base_address == "http://127.0.0.1:8080/inference"
    assert choice.preset.timeout == 20.0
    assert not choice.preset.online


def test_a_change_that_needs_a_new_backend():
    base = chosen(settings()).identity
    assert chosen(settings(stt_translate=True)).identity != base
    assert chosen(settings(whisper_process_device="cpu")).identity != base
    assert chosen(settings(stt_models={"whisper_model_size": "base"})).identity != base
    assert chosen(settings(stt_custom_address="http://elsewhere")).identity == base # not in use


def test_shown_when_conditions():
    assert shown_when_conditions(None) == ()
    assert shown_when_conditions("ptt_enabled") == ("ptt_enabled",)
    both = ("stt_service=Online Service", "stt_online_service=Groq")
    assert shown_when_conditions(both) == both
    assert condition_holds("ptt_enabled", True)
    assert not condition_holds("ptt_enabled", False)
    assert condition_holds("stt_service=Online Service", "Online Service")
    assert not condition_holds("stt_service=Online Service", "Whisper")


def test_the_test_button_says_when_the_language_is_not_understood(monkeypatch):
    import numpy as np
    from src.ui import stt_ui

    class Answers:
        takes_hint = False
        def load(self): pass
        def transcribe(self, audio, hint, names, language): return "Balgruuf, have you seen Proventus in Dragonsreach?"
        def close(self): pass

    monkeypatch.setattr(stt_ui, "build_backend", lambda choice, settings: Answers())
    monkeypatch.setattr(stt_ui, "read_sample", lambda: np.zeros(16000, dtype=np.float32))
    settings = SimpleNamespace(stt_service="Moonshine", stt_online_service="OpenAI", stt_models={},
                               moonshine_folder="", stt_language="fr")
    result = stt_ui.run_test(settings)
    assert result.startswith("**Moonshine only understands en") and "All names heard right." in result
    settings.stt_language = "en"
    assert not stt_ui.run_test(settings).startswith("**Moonshine only")


@pytest.mark.parametrize("service, online_service, custom_format, expected", [
    ("Online Service", "Deepgram", "OpenAI-style", True),
    ("Online Service", "Mistral", "OpenAI-style", True),
    ("Online Service", "ElevenLabs", "OpenAI-style", True),
    ("Online Service", "Groq", "Deepgram-style", False), # Groq takes the names as one line
    ("Custom Service", "Deepgram", "OpenAI-style", False),
    ("Custom Service", "Groq", "ElevenLabs-style", True),
    ("Whisper", "Deepgram", "Deepgram-style", False),
])
def test_catch_first_names_works_only_where_names_are_separate_words(service, online_service, custom_format, expected):
    from src.stt_backends.presets import takes_name_list
    assert takes_name_list(service, online_service, custom_format) is expected
