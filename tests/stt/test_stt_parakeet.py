"""Parakeet on the PC (the onnx-asr program). Loading the real model (670 MB) is checked by hand; here a fake onnx_asr stands in"""
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from src.stt_backends import build_backend, chosen, language_problem
from src.stt_backends.base import NOT_DOWNLOADED, SpeechError
from src.stt_backends.onnx_asr_program import OnnxAsrProgram
from src.stt_backends.presets import service_choices


def settings(**values):
    base = dict(stt_service="Parakeet", stt_online_service="OpenAI", stt_models={}, moonshine_folder="")
    return SimpleNamespace(**(base | values))


@pytest.fixture
def fake_onnx_asr(monkeypatch):
    """onnx_asr.load_model, recording what was asked for. *missing*: quantizations that have no files"""
    asked: list[tuple[str, str | None]] = []
    module = ModuleType("onnx_asr")
    module.missing = set()
    utils = ModuleType("onnx_asr.utils")

    class ModelFileNotFoundError(FileNotFoundError):
        pass

    utils.ModelFileNotFoundError = ModelFileNotFoundError

    class Model:
        def recognize(self, audio, sample_rate=16000):
            assert audio.dtype == np.float32 and sample_rate == 16000
            return "Balgruuf, avez-vous vu Proventus"

    def load_model(model, quantization=None):
        asked.append((model, quantization))
        if model == "offline":
            raise OSError("no connection")
        if model == "unreachable": # what Hugging Face raises when it can't connect and has nothing saved yet
            raise FileNotFoundError("cannot reach huggingface.co and the files are not in the cache")
        if quantization in module.missing:
            raise ModelFileNotFoundError(f"encoder-model.{quantization}.onnx")
        return Model()

    module.load_model = load_model
    module.asked = asked
    monkeypatch.setitem(sys.modules, "onnx_asr", module)
    monkeypatch.setitem(sys.modules, "onnx_asr.utils", utils)
    return module


def test_parakeet_is_a_pc_choice_with_its_own_model():
    assert service_choices() == ["Moonshine", "Whisper", "Parakeet", "Online Service", "Custom Service"]
    choice = chosen(settings())
    assert choice.preset.name == "Parakeet" and choice.model == "nemo-parakeet-tdt-0.6b-v3"
    assert not choice.preset.online and not choice.preset.sends_prompt
    assert chosen(settings(stt_models={"stt_model_parakeet": "nemo-parakeet-tdt-0.6b-v2"})).model == "nemo-parakeet-tdt-0.6b-v2"
    assert isinstance(build_backend(choice, settings()), OnnxAsrProgram)


def test_parakeet_languages():
    choice = chosen(settings())
    assert language_problem(choice, "fr") == "" and language_problem(choice, "ru") == ""
    problem = language_problem(choice, "ja")
    assert problem.startswith("Parakeet only understands") and "Pick Whisper or Online Service" in problem
    # Moonshine now points European speakers to Parakeet too
    assert "Pick Whisper, Parakeet or Online Service" in language_problem(chosen(settings(stt_service="Moonshine")), "fr")
    assert "Pick Whisper or Online Service" in language_problem(chosen(settings(stt_service="Moonshine")), "ja")


def test_loads_the_small_copy_and_ends_the_sentence(fake_onnx_asr):
    program = OnnxAsrProgram("nemo-parakeet-tdt-0.6b-v3")
    text = program.transcribe(np.zeros(16000, dtype=np.float32), "Names: Balgruuf.", ["Balgruuf"], "fr")
    assert text == "Balgruuf, avez-vous vu Proventus."
    assert fake_onnx_asr.asked == [("nemo-parakeet-tdt-0.6b-v3", "int8")]
    assert not program.takes_hint


def test_a_typed_model_without_a_small_copy_loads_in_full(fake_onnx_asr):
    fake_onnx_asr.missing.add("int8")
    OnnxAsrProgram("whisper-base").load()
    assert fake_onnx_asr.asked == [("whisper-base", "int8"), ("whisper-base", None)]


def test_no_connection_never_falls_back_to_the_full_model(fake_onnx_asr):
    with pytest.raises(SpeechError):
        OnnxAsrProgram("unreachable").load()
    assert fake_onnx_asr.asked == [("unreachable", "int8")] # not the 2.5 GB copy


def test_a_failed_download_is_reported_once_and_tried_again_from_test(fake_onnx_asr):
    program = OnnxAsrProgram("offline")
    with pytest.raises(SpeechError) as first:
        program.transcribe(np.zeros(16000, dtype=np.float32), "", [], "en")
    assert first.value.kind == NOT_DOWNLOADED and "Model setting under Parakeet" in first.value.message
    with pytest.raises(SpeechError):
        program.transcribe(np.zeros(16000, dtype=np.float32), "", [], "en")
    assert len(fake_onnx_asr.asked) == 1 # not tried again on every line
    program.forget_failed_load()
    with pytest.raises(SpeechError):
        program.load()
    assert len(fake_onnx_asr.asked) == 2
