"""The voice parts of the Text-to-Speech page are built and shown on that page"""
import gradio as gr

from src.config.config_loader import ConfigLoader
from src.config.definitions.game_definitions import GameEnum
from src.ui.start_ui import StartUI


def tab_of(component) -> str | None:
    parent = getattr(component, "parent", None)
    while parent is not None:
        if isinstance(parent, gr.Tab):
            return parent.label
        parent = getattr(parent, "parent", None)
    return None


def test_the_voice_sections_are_on_the_text_to_speech_page(tmp_path):
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    tabs = [c.label for c in blocks.blocks.values() if isinstance(c, gr.Tab)]
    assert "Voice Setups" not in tabs and "Fix Pronunciation" not in tabs and "Try A Voice" not in tabs
    labels = {c.label: c for c in blocks.blocks.values() if getattr(c, "label", None)}
    for label in ("Voice service", "Setup to edit", "Ready-made setups", "Request style", "Clip field name", "Say it as (e.g. Doh-vah-keen)", "Voice to change", "Other clips you can clone from"):
        assert tab_of(labels[label]) == "Text-to-Speech", label
    accordions = [c.label for c in blocks.blocks.values() if isinstance(c, gr.Accordion)]
    assert {"Try A Voice", "Voice Setups", "Fix Pronunciation", "Voice Clips", "Pocket TTS"} <= set(accordions)
    assert (tmp_path / "pronunciation" / "my_words.csv").exists()
    blocks.get_api_info() # the page the browser asks for when it opens; an Audio player in it used to break it


def test_saving_a_setup_makes_the_next_line_use_it(tmp_path):
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    handlers = {f.fn.__name__: f.fn for f in blocks.fns if "render_voice_setups_editor" in getattr(f.fn, "__qualname__", "")}
    config.update_config_loader_with_changed_config_values()
    assert not config.has_any_config_value_changed
    filled = handlers["on_use_ready"]("Pocket TTS (graphics card)")
    message = handlers["on_save"]("(new setup)", *filled[1:-1])[0]
    assert "Saved" in message and config.has_any_config_value_changed # the running voice service is started again with it
    config.update_config_loader_with_changed_config_values()
    assert "Removed" in handlers["on_delete"]("Pocket TTS (graphics card)")[0] and config.has_any_config_value_changed


def test_xtts_style_hides_the_boxes_it_does_not_use(tmp_path):
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    handlers = {f.fn.__name__: f.fn for f in blocks.fns if "render_voice_setups_editor" in getattr(f.fn, "__qualname__", "")}
    key, model, clip_field, clip_mode = handlers["on_style"]("xtts", "base64")
    assert not key["visible"] and not model["visible"] and not clip_field["visible"]
    assert clip_mode["value"] == "name" and "base64" not in [value for _, value in clip_mode["choices"]] # XTTS-style can't take the clip itself
    key, model, clip_field, clip_mode = handlers["on_style"]("openai", "name")
    assert key["visible"] and model["visible"] and clip_field["visible"] and clip_mode["value"] == "name"
    assert "base64" in [value for _, value in clip_mode["choices"]]


def test_hear_it_speak_uses_the_clip_or_the_line_picked_in_the_list(tmp_path, monkeypatch):
    import os
    import pytest
    from src.tts.test_line import TEST_LINE, voice_from_clip
    from src.tts.voice_picker import VOICE_TYPE_CLIP, VoicePick
    from src.ui import voice_clips_ui
    from src.voice_clips import fuz
    from src.voice_clips.clip_store import VOICE_TYPES, ClipInfo, shared_clip_store
    sound = str(tmp_path / "line.wav")
    fuz.to_wav(os.path.join(os.path.dirname(__file__), "..", "voice_clips", "data", "dialoguesolitude__000dd60f_1.fuz"), sound)
    clips = shared_clip_store(str(tmp_path / "voice_clips"))
    clips.set_clip(VOICE_TYPES, "femaleeventoned", sound, ClipInfo(name="FemaleEvenToned", path=r"sound\voice\skyrim.esm\femaleeventoned\a__000dd60f_1.fuz"))
    clips.add_candidate(VOICE_TYPES, "femaleeventoned", sound, ClipInfo(path=r"sound\voice\skyrim.esm\femaleeventoned\a__000dd601_1.fuz"))
    [(candidate, _)] = clips.candidates(VOICE_TYPES, "femaleeventoned")
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    on_speak = next(f.fn for f in blocks.fns if getattr(f.fn, "__name__", "") == "on_speak")
    spoken = []
    monkeypatch.setattr(voice_clips_ui, "speak_with_clip", lambda config, getter, service, pick, text: spoken.append((pick, text)) or sound)

    audio, message = on_speak(f"{VOICE_TYPES}/femaleeventoned", None, "Hello there.")
    pick, text = spoken[-1]
    assert audio["value"] == sound and audio["visible"] and "the clip" in message
    assert pick == VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", clips.clip(VOICE_TYPES, "femaleeventoned")) and text == "Hello there." # the voice used in game
    on_speak(f"{VOICE_TYPES}/femaleeventoned", candidate, "  ")
    pick, text = spoken[-1]
    assert pick.key == "try_femaleeventoned" and pick.clip_path == candidate and text == TEST_LINE # never the name of the clip's own voice
    audio, message = on_speak(None, None, "")
    assert not audio["visible"] and message == "Pick a voice clip first."
    with pytest.raises(ValueError, match="doesn't clone voices"):
        voice_from_clip(object(), "Piper", pick, "Hello")


def test_the_online_services_have_parts_of_their_own_and_never_show_the_key(tmp_path, monkeypatch):
    from src.ui import online_voices_ui
    from src.tts.elevenlabs import ElevenLabs
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    accordions = [c.label for c in blocks.blocks.values() if isinstance(c, gr.Accordion)]
    assert accordions.index("Pocket TTS") < accordions.index("ElevenLabs") < accordions.index("Inworld")
    assert tab_of(next(c for c in blocks.blocks.values() if getattr(c, "label", None) == "ElevenLabs key")) == "Text-to-Speech"
    saved = {}
    monkeypatch.setattr(online_voices_ui, "save_key_file", lambda name, key: saved.update({name: key}) or tmp_path / name)
    monkeypatch.setattr(ElevenLabs, "check_key", classmethod(lambda cls, key, folder: "Your key works."))
    on_save = next(f.fn for f in blocks.fns if getattr(f.fn, "__name__", "") == "on_save" and "online_voices_ui" in f.fn.__module__)
    box, message = on_save("  sk_123  ")
    assert box == "" and "sk_123" not in message and saved == {"ELEVENLABS_SECRET_KEY.txt": "sk_123"} and "Your key works." in message
    assert config.has_any_config_value_changed # the next line uses the new key
    from src.tts import online_service
    on_opens = [f.fn for f in blocks.fns if getattr(f.fn, "__name__", "") == "on_open" and "online_voices_ui" in f.fn.__module__]
    online_service.set_problem("ElevenLabs", "ElevenLabs won't clone another voice.")
    try:
        shown = [on_open() for on_open in on_opens] # each time the page is opened
        assert sum("won't clone another voice" in text for text in shown) == 1 # on ElevenLabs' part only
    finally:
        online_service.set_problem("ElevenLabs", None)


def test_hear_the_cloned_voice_uses_the_tts_service_picked_on_the_page_before_the_game_asks(tmp_path, monkeypatch):
    from src.tts.voice_picker import VOICE_TYPE_CLIP, VoicePick
    from src.ui import voice_clips_ui
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    on_speak = next(f.fn for f in blocks.fns if getattr(f.fn, "__name__", "") == "on_speak")
    used = []
    monkeypatch.setattr(voice_clips_ui, "clip_voice", lambda store, kind, key, line=None: VoicePick(VOICE_TYPE_CLIP, key, str(tmp_path / "a.wav")))
    monkeypatch.setattr(voice_clips_ui, "speak_with_clip", lambda config, getter, service, pick, text: used.append(service) or str(tmp_path / "a.wav"))
    config.definitions.get_config_value_definition("tts_service").value = "Inworld" # picked on the page; the game hasn't sent a request yet
    _, message = on_speak("voice_types/femaleeventoned", None, "Hello.")
    assert used == ["Inworld"] and "Inworld cloned" in message
