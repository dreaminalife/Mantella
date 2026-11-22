import json
import os

import numpy as np
import pytest
import soundfile as sf

from src.config.definitions.tts_definitions import TTSEnum
from src.tts import voice_setups as vs
from src.tts.address_check import check_address
from src.tts.openai_style import OpenAIStyleService
from src.tts.registry import VoiceServiceRegistry, normalize_service_name, voice_services
from src.tts.voice_picker import VoiceSpeaker
from src.tts.xtts_style import XTTSStyleService
from tests.tts.fake_voice_server import FakeVoiceServer


@pytest.fixture(autouse=True)
def no_setups_left_over():
    yield
    voice_services.set_setups([])


def a_setup(**changes):
    setup = {**vs.empty_setup(), "name": "My Pocket", "url": "http://127.0.0.1:8080", "clip_field": "speaker_ref_b64", "clip_mode": vs.CLIP_BASE64}
    setup.update(changes)
    return setup


def write_clip(folder, voice_type: str, seconds: float = 0.3):
    path = os.path.join(folder, "voice_clips", "voice_types")
    os.makedirs(path, exist_ok=True)
    sf.write(os.path.join(path, f"{voice_type}.wav"), np.full(int(24000 * seconds), 0.2, dtype=np.float32), 24000, subtype="PCM_16")
    with open(os.path.join(path, f"{voice_type}.json"), "w", encoding="utf-8") as file:
        file.write("{}") # what Mantella writes next to a clip
    return os.path.join(path, f"{voice_type}.wav")


# ---- the list of services ----

def test_names_are_found_by_name_or_alias_whatever_the_spelling():
    registry = VoiceServiceRegistry()
    assert registry.find("xva").legacy_enum == TTSEnum.XVASYNTH
    assert registry.find(" Piper ").legacy_enum == TTSEnum.PIPER
    assert registry.find("x-tts").legacy_enum == TTSEnum.XTTS
    assert registry.find("nan") is None and registry.find("") is None and registry.find(None) is None
    assert normalize_service_name("Pocket TTS (graphics card)") == "pocketttsgraphicscard"


def test_a_setup_with_a_taken_name_is_left_out():
    registry = VoiceServiceRegistry()
    registry.set_setups([vs.to_service_info(vs.normalized(a_setup(name="piper"))), vs.to_service_info(vs.normalized(a_setup()))])
    assert registry.names() == ["Piper", "XTTS", "xVASynth", "Pocket TTS", "ElevenLabs", "Inworld", "HeyGen", "Qwen", "Boson AI", "Iodine", "My Pocket"]


def test_setups_are_saved_listed_renamed_and_removed(tmp_path):
    setups = vs.VoiceSetups(str(tmp_path))
    ok, _ = setups.save(a_setup())
    assert ok and voice_services.find("my pocket").is_setup
    with open(tmp_path / "voice_setups.json", encoding="utf-8") as f:
        assert json.load(f)["setups"][0]["clip_field"] == "speaker_ref_b64"

    ok, _ = setups.save(a_setup(name="Their Pocket"), old_name="My Pocket")
    assert ok and setups.names() == ["Their Pocket"] and voice_services.find("My Pocket") is None

    ok, message = setups.delete("Their Pocket", in_use_by=["TTS Service"])
    assert not ok and "TTS Service" in message
    ok, _ = setups.delete("Their Pocket", in_use_by=[])
    assert ok and setups.names() == [] and voice_services.find("Their Pocket") is None
    assert vs.VoiceSetups(str(tmp_path)).names() == [] # the file was written too


@pytest.mark.parametrize("changes, expected", [
    ({"name": ""}, "Give the setup a name"),
    ({"name": "XTTS"}, "already taken"),
    ({"url": ""}, "Fill in the address"),
    ({"url": "127.0.0.1:8080"}, "must start with http"),
    ({"style": "other"}, "Pick a request style"),
    ({"clip_field": ""}, "box that carries the clip"),
    ({"style": "xtts", "clip_mode": "base64"}, "can't take the clip itself"),
    ({"extra_fields": "{seed: 1"}, "must be written as JSON"),
])
def test_mistakes_are_named_in_plain_words(changes, expected):
    found = vs.problems(a_setup(**changes), voice_services.names())
    assert any(expected in p for p in found), found


def test_a_good_setup_has_no_problems():
    assert vs.problems(a_setup(extra_fields='{"seed": 42}'), voice_services.names()) == []


def test_the_ready_made_list_has_llama_cpp():
    ready = {s["name"]: s for s in vs.ready_made_setups()}
    llama = ready["Pocket TTS (graphics card)"]
    assert llama["style"] == vs.OPENAI and llama["clip_field"] == "speaker_ref_b64" and llama["clip_mode"] == vs.CLIP_BASE64
    assert llama["url"] == "http://127.0.0.1:8080" and llama["about"]
    assert vs.problems(llama, voice_services.names()) == []


# ---- OpenAI-style ----

def test_openai_style_sends_the_line_the_clip_and_the_key(voice_config, tmp_path, monkeypatch):
    clip = write_clip(tmp_path, "FemaleEvenToned")
    monkeypatch.setattr(vs, "read_key_file", lambda name: "secret" if name else "")
    import src.tts.openai_style as openai_style
    monkeypatch.setattr(openai_style, "read_key_file", lambda name: "secret" if name else "")
    with FakeVoiceServer("openai") as server:
        info = vs.to_service_info(vs.normalized(a_setup(url=server.url, key_file="KEY.txt", model="tts", extra_fields={"seed": 7})))
        service = OpenAIStyleService(voice_config, info)
        out = str(tmp_path / "line.wav")
        service.voice_into("Hello there.", service.pick_voice(VoiceSpeaker(gender=1, voice_type="FemaleEvenToned")), out)

    body = server.requests[0]
    assert body["input"] == "Hello there." and body["model"] == "tts" and body["seed"] == 7 and body["response_format"] == "wav"
    with open(clip, "rb") as f:
        import base64
        assert body["speaker_ref_b64"] == base64.b64encode(f.read()).decode()
    assert server.headers[0]["Authorization"] == "Bearer secret"
    data, rate = sf.read(out)
    assert rate == 24000 and data.ndim == 1
    assert 0.5 <= len(data) / rate < 0.6 # the silence before and after was cut, a little kept


def test_openai_style_hands_over_the_clips_place_on_this_pc(voice_config, tmp_path):
    clip = write_clip(tmp_path, "MaleNord")
    with FakeVoiceServer("openai") as server:
        info = vs.to_service_info(vs.normalized(a_setup(url=server.url, clip_field="voice_ref", clip_mode=vs.CLIP_PATH)))
        service = OpenAIStyleService(voice_config, info)
        service.voice_into("Hi.", service.pick_voice(VoiceSpeaker(voice_type="MaleNord")), str(tmp_path / "a.wav"))
    assert server.requests[0]["voice_ref"] == os.path.abspath(clip)


def test_without_a_clip_the_setups_own_voice_speaks(voice_config, tmp_path):
    with FakeVoiceServer("openai") as server:
        info = vs.to_service_info(vs.normalized(a_setup(url=server.url, male_voice="george", female_voice="alba")))
        service = OpenAIStyleService(voice_config, info)
        service.voice_into("Hi.", service.pick_voice(VoiceSpeaker(gender=1, voice_type="FemaleOrc")), str(tmp_path / "a.wav"))
    assert server.requests[0]["voice"] == "alba" and "speaker_ref_b64" not in server.requests[0]


def test_a_setup_with_no_clip_box_speaks_with_its_own_voices_and_asks_the_game_for_no_clips(voice_config, tmp_path, monkeypatch):
    from src.tts.synthesization_options import SynthesizationOptions
    from src.tts.ttsable import TTSable
    write_clip(tmp_path, "FemaleEvenToned") # a clip another service asked for
    monkeypatch.setattr(TTSable, "synthesize", lambda *args, **kwargs: None)
    with FakeVoiceServer("openai") as server:
        info = vs.to_service_info(vs.normalized(a_setup(url=server.url, clip_field="", clip_mode="", male_voice="george", female_voice="alba")))
        service = OpenAIStyleService(voice_config, info)
        pick = service.pick_voice(VoiceSpeaker(gender=1, voice_type="FemaleEvenToned"))
        service.voice_into("Hi.", pick, str(tmp_path / "a.wav"))
        service.synthesize("", "Hi.", "FemaleNord", "FemaleNord", "", SynthesizationOptions(False, True),
                           speaker=VoiceSpeaker(gender=1, voice_type="FemaleNord")) # no clip yet
    assert server.requests[0]["voice"] == "alba" and "speaker_ref_b64" not in server.requests[0]
    assert service._clip_requests.requests_for_reply() == []


def test_a_clip_sent_as_base64_is_not_kept_in_memory(voice_config, tmp_path):
    write_clip(tmp_path, "FemaleEvenToned")
    with FakeVoiceServer("openai") as server:
        service = OpenAIStyleService(voice_config, vs.to_service_info(vs.normalized(a_setup(url=server.url))))
        pick = service.pick_voice(VoiceSpeaker(gender=1, voice_type="FemaleEvenToned"))
        service.voice_into("Hi.", pick, str(tmp_path / "a.wav"))
    assert server.requests[0]["speaker_ref_b64"]
    assert all(len(repr(voice)) < 1000 for _, voice in service._prepared.values())


def test_one_failed_try_is_tried_again(voice_config, tmp_path):
    with FakeVoiceServer("openai", fail_times=1) as server:
        service = OpenAIStyleService(voice_config, vs.to_service_info(vs.normalized(a_setup(url=server.url))))
        service.voice_into("Hi.", service.pick_voice(None), str(tmp_path / "a.wav"))
    assert len(server.requests) == 2 and os.path.exists(tmp_path / "a.wav")


def test_two_failed_tries_give_up(voice_config, tmp_path):
    from src.tts.clip_service import VoiceServiceFailure
    with FakeVoiceServer("openai", fail_times=5) as server:
        service = OpenAIStyleService(voice_config, vs.to_service_info(vs.normalized(a_setup(url=server.url))))
        with pytest.raises(VoiceServiceFailure):
            service.voice_into("Hi.", service.pick_voice(None), str(tmp_path / "a.wav"))
    assert len(server.requests) == 2


# ---- XTTS-style ----

def test_xtts_style_sends_the_same_request_as_mantellas_xtts(voice_config, tmp_path):
    with FakeVoiceServer("xtts", speakers=["malenord", "serana"]) as server:
        info = vs.to_service_info(vs.normalized(a_setup(style=vs.XTTS, url=server.url, clip_field="", clip_mode=vs.CLIP_NAME)))
        service = XTTSStyleService(voice_config, info)
        assert service.has_voice("Serana") and not service.has_voice("Lydia")
        service.voice_into("Hi.", service.pick_voice(VoiceSpeaker(voice_type="MaleNord"), named_voice="Serana"), str(tmp_path / "a.wav"))
    assert server.requests[0] == {"text": "Hi.", "speaker_wav": "Serana", "language": "en"}


def test_xtts_style_hands_over_the_clips_place_when_asked(voice_config, tmp_path):
    clip = write_clip(tmp_path, "MaleNord")
    with FakeVoiceServer("xtts") as server:
        info = vs.to_service_info(vs.normalized(a_setup(style=vs.XTTS, url=server.url, clip_field="", clip_mode=vs.CLIP_PATH)))
        service = XTTSStyleService(voice_config, info)
        service.voice_into("Hi.", service.pick_voice(VoiceSpeaker(voice_type="MaleNord")), str(tmp_path / "a.wav"))
    assert server.requests[0]["speaker_wav"] == os.path.abspath(clip)


# ---- the address check ----

def test_the_address_check_knows_llama_cpp():
    with FakeVoiceServer("openai", models_body={"data": [{"id": "pocket-tts.gguf", "owned_by": "llamacpp"}]}) as server:
        result = check_address(server.url)
    assert result.style == vs.OPENAI and result.filled["clip_field"] == "speaker_ref_b64" and result.filled["clip_mode"] == vs.CLIP_BASE64
    assert result.filled["model"] == "pocket-tts.gguf"


def test_the_address_check_knows_audio_cpp():
    with FakeVoiceServer("openai", models_body={"data": [{"id": "pocket-tts", "owned_by": "audio.cpp"}]}) as server:
        result = check_address(server.url)
    assert result.filled["clip_field"] == "voice_ref" and result.filled["clip_mode"] == vs.CLIP_PATH


def test_the_address_check_knows_xtts_style_and_kyutai():
    with FakeVoiceServer("xtts") as server:
        assert check_address(server.url).style == vs.XTTS
    with FakeVoiceServer("kyutai") as server:
        result = check_address(server.url)
    assert result.style is None and "Pocket TTS" in result.message


def test_the_address_check_says_when_nothing_answers():
    with FakeVoiceServer("openai") as server:
        url = server.url
    assert "Nothing answers" in check_address(url).message
    assert "http://" in check_address("localhost:8080").message


def test_a_clip_replaced_under_the_same_name_is_used_from_the_next_line(voice_config, tmp_path):
    write_clip(tmp_path, "MaleNord", seconds=0.3)
    with FakeVoiceServer("openai") as server:
        service = OpenAIStyleService(voice_config, vs.to_service_info(vs.normalized(a_setup(url=server.url))))
        pick = service.pick_voice(VoiceSpeaker(voice_type="MaleNord"))
        service.voice_into("Hi.", pick, str(tmp_path / "a.wav"))
        service.voice_into("Hi.", pick, str(tmp_path / "a.wav"))
        write_clip(tmp_path, "MaleNord", seconds=0.6)
        service.voice_into("Hi.", service.pick_voice(VoiceSpeaker(voice_type="MaleNord")), str(tmp_path / "a.wav"))
    first, again, replaced = (r["speaker_ref_b64"] for r in server.requests)
    assert first == again and replaced != first


def test_a_setup_not_set_up_for_the_language_is_named_once(voice_config, caplog):
    import logging
    info = vs.to_service_info(vs.normalized(a_setup(name="German Only", languages="de")))
    assert info.speaks("de") and not info.speaks("en") and vs.to_service_info(vs.normalized(a_setup(languages="zh-cn"))).speaks("zh")
    assert vs.to_service_info(vs.normalized(a_setup(languages=""))).speaks("en")
    with caplog.at_level(logging.DEBUG):
        OpenAIStyleService(voice_config, info)
        OpenAIStyleService(voice_config, info)
    assert caplog.text.count("German Only is set up for de, not for your Language setting (en)") == 1


def test_a_setups_talking_speed_is_kept_within_its_range():
    from src.tts.voice_setups import empty_setup, normalized, problems
    assert empty_setup()["speed"] == 1.0
    assert normalized({**empty_setup(), "name": "Mine", "speed": "1.25"})["speed"] == 1.25
    assert normalized({**empty_setup(), "name": "Mine", "speed": 3})["speed"] == 1.5
    assert normalized({**empty_setup(), "name": "Mine", "speed": "fast"})["speed"] == 1.0
    assert any("Talking speed" in p for p in problems({**empty_setup(), "name": "Mine", "speed": 2.0}, []))
    assert not any("Talking speed" in p for p in problems({**empty_setup(), "name": "Mine", "speed": 0.8}, []))
