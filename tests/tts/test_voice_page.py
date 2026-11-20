"""The voice parts of the Text-to-Speech page are built and shown on that page"""
import gradio as gr

from src.config.config_loader import ConfigLoader
from src.config.definitions.game_definitions import GameEnum
from src.config.definitions.tts_definitions import NPC_VOICES_PART
from src.tts.model_catalog import ServiceModels
from src.ui.start_ui import StartUI
from tests.ui.test_tts_page import PART_NAMES, SOUND_NAMES, part_of, service_of


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
    assert "Your voice setups" not in tabs and "Fix Pronunciation" not in tabs and NPC_VOICES_PART not in tabs
    labels = {c.label: c for c in blocks.blocks.values() if getattr(c, "label", None)}
    for label in ("Settings for", "Voice type", "Setup to edit", "Ready-made setups", "Request style", "Clip field name", "Say it as (e.g. Doh-vah-keen)",
                  "Voice to change"):
        assert tab_of(labels[label]) == "Text-to-Speech", label
    assert accordion_of(labels["Setup to edit"]) == "Your voice setups" and accordion_of(labels["Voice to change"]) == NPC_VOICES_PART
    accordions = [c.label for c in blocks.blocks.values() if isinstance(c, gr.Accordion)]
    assert {"Speed and lip sync", "Voice Effects", "Fix Pronunciation"} <= set(accordions)
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


def test_change_a_voice_shows_the_clips_the_tts_service_uses(tmp_path, monkeypatch):
    import os
    from src.ui import voice_clips_ui
    from src.ui.voice_clips_ui import CLONED_ROWS, OTHER_ROWS
    from src.voice_clips import fuz
    from src.voice_clips.clip_store import VOICE_TYPES, ClipInfo, shared_clip_store
    sound = str(tmp_path / "line.wav")
    fuz.to_wav(os.path.join(os.path.dirname(__file__), "..", "voice_clips", "data", "dialoguesolitude__000dd60f_1.fuz"), sound) # about 4.6 s
    clips = shared_clip_store(str(tmp_path / "voice_clips"))
    clips.set_clip(VOICE_TYPES, "femaleeventoned", sound, ClipInfo(name="FemaleEvenToned", path=r"sound\voice\skyrim.esm\femaleeventoned\a__000dd60f_1.fuz"))
    for n in range(1, 5):
        clips.add_candidate(VOICE_TYPES, "femaleeventoned", sound, ClipInfo(path=rf"sound\voice\skyrim.esm\femaleeventoned\a__000dd60{n}_1.fuz",
                                                                            order=n if n < 4 else 0))
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    handlers = {f.fn.__name__: f.fn for f in blocks.fns if "render_voice_clips_editor" in getattr(f.fn, "__qualname__", "")}
    show = handlers["show"]
    voice = f"{VOICE_TYPES}/femaleeventoned"
    def parts(out):
        cloned_head = out[2]
        cloned = out[3:3 + 3 * CLONED_ROWS]
        other_head = out[3 + 3 * CLONED_ROWS]
        other = out[4 + 3 * CLONED_ROWS:4 + 3 * CLONED_ROWS + 3 * OTHER_ROWS]
        return out[0], cloned_head, cloned, other_head, other, out[-2], out[-1]

    # Pocket TTS: one clip, the others to swap in
    note, head, cloned, other_head, other, cloned_paths, other_paths = parts(show(voice, "Pocket TTS"))
    assert head == "**Cloned from**" and len(cloned_paths) == 1 and len(other_paths) == 4
    assert "a__000dd60f_1.fuz" in cloned[1] and 'src="/voice_clips/voice_types/femaleeventoned?v=' in cloned[1] and not cloned[2]["visible"]
    assert other[2]["value"] == "Use this one"

    # Inworld: the clips that fit in 30 seconds, and Add for the others
    note, head, cloned, other_head, other, cloned_paths, other_paths = parts(show(voice, "Inworld"))
    assert len(cloned_paths) == 4 and "Inworld takes up to 30 s" in head and cloned[2]["value"] == "Take out" and cloned[2]["interactive"]
    assert other[2]["value"] == "Add" and other[2]["interactive"]
    out = next(f.fn for f in blocks.fns if getattr(f.fn, "__name__", "") == "<lambda>" and "on_other" in f.fn.__code__.co_freevars)(voice, "Inworld", other_paths)
    note, head, cloned, other_head, other, cloned_paths, other_paths = parts(out)
    assert len(cloned_paths) == 5 and other_paths == [] and "none yet" in other_head
    assert clips.is_hand_picked(VOICE_TYPES, "femaleeventoned")
    out = next(f.fn for f in blocks.fns if getattr(f.fn, "__name__", "") == "<lambda>" and "on_take_out" in f.fn.__code__.co_freevars)(voice, "Inworld", cloned_paths)
    assert len(parts(out)[5]) == 4 and "Taken out" in out[-3]

    # A clip swapped out by Find more clips while the list still showed it
    gone = str(tmp_path / "gone.wav")
    out = next(f.fn for f in blocks.fns if getattr(f.fn, "__name__", "") == "<lambda>" and "on_other" in f.fn.__code__.co_freevars)(voice, "Pocket TTS", [gone])
    assert "That clip is gone" in out[-3]

    # Clips sent by the game redraw the list, but leave the Hear the voice player alone
    drawn = handlers["on_seen"](voice, "Inworld")
    assert drawn[2 + len(out) - 4] == gr.update() and drawn[2 + len(out) - 3] == gr.update() # the player and the message

    # Piper doesn't use clips, and the backup is Piper too: nothing to change here
    note, head, cloned, other_head, other, cloned_paths, other_paths = parts(show(voice, "Piper"))
    assert note["visible"] and "Piper doesn't use clips" in note["value"] and cloned_paths == []

    # Hear the voice: with the service shown
    spoken = []
    monkeypatch.setattr(voice_clips_ui, "speak_with_clip", lambda config, getter, service, pick, text: spoken.append((service, pick)) or sound)
    audio, message = handlers["on_speak"](voice, "Inworld", "Hello.")
    assert spoken[-1][0] == "Inworld" and spoken[-1][1].clip_path == clips.clip(VOICE_TYPES, "femaleeventoned") and "clips above" in message

    # Find more clips with Pocket TTS: every other clip is swapped, as the list shows them (no service in use takes them)
    assert "4 new clips" in handlers["on_more"](voice, "Pocket TTS")
    assert len(clips.voice_lines(VOICE_TYPES, "femaleeventoned")) == 1
    assert "16 new clips" in handlers["on_more"](voice, "Pocket TTS", 50) # How many: 16 at most
    assert "1 new clip takes" in handlers["on_more"](voice, "Pocket TTS", 1)


def test_add_is_offered_only_for_a_clip_the_service_really_takes(tmp_path):
    """The gaps between the clips count, as when the voice is cloned: near the limit, a clip that fits without them doesn't"""
    import numpy as np
    import soundfile as sf
    from src.ui.voice_clips_ui import CLONED_ROWS
    from src.voice_clips.clip_store import VOICE_TYPES, ClipInfo, clip_seconds, shared_clip_store
    def silence(name: str, seconds: float) -> str:
        path = str(tmp_path / name)
        sf.write(path, np.zeros(int(16000 * seconds), dtype="float32"), 16000)
        return path
    clips = shared_clip_store(str(tmp_path / "voice_clips"))
    game = r"sound\voice\skyrim.esm\femaleeventoned\a__000dd60{}_1.fuz"
    clips.set_clip(VOICE_TYPES, "femaleeventoned", silence("top.wav", 6), ClipInfo(name="FemaleEvenToned", path=game.format("f")))
    for n in range(1, 4): # 4 clips of 6 s and 3 gaps: 25.5 s of Inworld's 29.5
        clips.add_candidate(VOICE_TYPES, "femaleeventoned", silence(f"{n}.wav", 6), ClipInfo(path=game.format(n), order=n))
    clips.add_candidate(VOICE_TYPES, "femaleeventoned", silence("long.wav", 4.2), ClipInfo(path=game.format(4))) # 30.2 s with its gap
    clips.add_candidate(VOICE_TYPES, "femaleeventoned", silence("short.wav", 3), ClipInfo(path=game.format(5))) # 29 s
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    handlers = {f.fn.__name__: f.fn for f in blocks.fns if "render_voice_clips_editor" in getattr(f.fn, "__qualname__", "")}
    on_other = next(f.fn for f in blocks.fns if getattr(f.fn, "__name__", "") == "<lambda>" and "on_other" in f.fn.__code__.co_freevars)
    voice = f"{VOICE_TYPES}/femaleeventoned"
    out = handlers["show"](voice, "Inworld")
    other, other_paths = out[4 + 3 * CLONED_ROWS:], out[-1]
    offered = {round(clip_seconds(path), 1): other[3 * i + 2]["interactive"] for i, path in enumerate(other_paths)}
    assert offered == {4.2: False, 3.0: True}
    long_path = next(p for p in other_paths if round(clip_seconds(p), 1) == 4.2)
    out = on_other(voice, "Inworld", [long_path]) # pressed anyway, from a list drawn before
    assert "full" in out[-3] and len(out[-2]) == 4
    out = on_other(voice, "Inworld", [next(p for p in other_paths if p != long_path)])
    assert "Added" in out[-3] and len(out[-2]) == 5 # added, and Inworld takes it


def test_the_online_services_have_parts_of_their_own_and_never_show_the_key(tmp_path, monkeypatch):
    from src.ui import online_voices_ui
    from src.tts.elevenlabs import ElevenLabs
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    picker = next(c for c in blocks.blocks.values() if isinstance(c, gr.Dropdown) and c.label == "Settings for")
    assert [value for _, value in picker.choices][:6] == ["Pocket TTS", "ElevenLabs", "Inworld", "HeyGen", "Qwen", "Boson AI"]
    for name in ("ElevenLabs", "HeyGen", "Qwen", "Boson AI"):
        assert tab_of(next(c for c in blocks.blocks.values() if getattr(c, "label", None) == f"{name} key")) == "Text-to-Speech"
    online = ("ElevenLabs", "Inworld", "HeyGen", "Qwen", "Boson AI")
    labels = [(c.label, service_of(c)) for c in blocks.blocks.values()
              if getattr(c, "label", None) in ("Audio tags", "Directions") and service_of(c) in online and accordion_of(c) == f"Global settings for {service_of(c)}"]
    assert sorted(labels) == sorted([("Audio tags", "Inworld"), ("Directions", "Inworld"), ("Audio tags", "HeyGen"), ("Audio tags", "Qwen"),
                                     ("Directions", "Qwen"), ("Audio tags", "Boson AI")])
    texts = [(c.value, service_of(c), accordion_of(c)) for c in blocks.blocks.values() if isinstance(getattr(c, "value", None), str)]
    assert any("The model HeyGen speaks with" in text and part is None for text, part, _ in texts) # HeyGen's Model, next to TTS Service
    assert any("Workspace ID" in text and part == "Qwen" and fold == "Your key" for text, part, fold in texts) # with the key
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
        assert sum("won't clone another voice" in text for text, *_ in shown) == 1 # on ElevenLabs' part only
        assert all(fold.get("open") for text, *folds in shown if "won't clone another voice" in text for fold in folds) # Your key opens to say so
    finally:
        online_service.set_problem("ElevenLabs", None)
    # Qwen's Workspace ID is known once the page opens, so its voices can be deleted there before Qwen has spoken
    from src.tts import qwen
    qwen.use_workspace("")
    config.definitions.get_config_value_definition("qwen_workspace_id").value = "ws-test"
    for on_open in on_opens:
        on_open()
    assert qwen.has_workspace()
    qwen.use_workspace("")


def test_hear_the_cloned_voice_uses_the_tts_service_picked_on_the_page_before_the_game_asks(tmp_path, monkeypatch):
    from src.tts.voice_picker import VOICE_TYPE_CLIP, VoicePick
    from src.ui import voice_clips_ui
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    on_speak = next(f.fn for f in blocks.fns if getattr(f.fn, "__name__", "") == "on_speak")
    used = []
    monkeypatch.setattr(voice_clips_ui, "clip_voice", lambda store, kind, key: VoicePick(VOICE_TYPE_CLIP, key, str(tmp_path / "a.wav")))
    monkeypatch.setattr(voice_clips_ui, "speak_with_clip", lambda config, getter, service, pick, text: used.append(service) or str(tmp_path / "a.wav"))
    config.definitions.get_config_value_definition("tts_service").value = "Inworld" # picked on the page; the game hasn't sent a request yet
    _, message = on_speak("voice_types/femaleeventoned", None, "Hello.")
    assert used == ["Inworld"] and "Inworld cloned" in message


def accordion_of(component) -> str | None:
    """The fold or the service's part of the Text-to-Speech page a component is in"""
    return part_of(component)


def test_voice_effects_and_each_services_volume_are_on_the_text_to_speech_page(tmp_path, monkeypatch):
    import soundfile as sf
    from src.tts.voice_effects import speech_level_db
    from src.ui import voice_effects_ui
    from tests.tts.fake_voice_server import tone_wav
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    labels = {c.label: c for c in blocks.blocks.values() if getattr(c, "label", None)}
    assert tab_of(labels["Effect"]) == "Text-to-Speech"
    volumes = [service_of(c) for c in blocks.blocks.values() if isinstance(getattr(c, "value", None), str) and "How loud NPCs speak with" in c.value]
    assert sorted(volumes) == ["Boson AI", "ElevenLabs", "HeyGen", "Inworld", "Pocket TTS", "Qwen"] # each in its own part, not in a part of its own named the same
    accordions = [c.label for c in blocks.blocks.values() if isinstance(c, gr.Accordion)]
    assert "Pocket TTS" not in accordions # its part is shown with the Settings for list, not folded
    assert accordions.index("Voice Effects") < accordions.index("Fix Pronunciation") # at the end of the page
    assert (tmp_path / "voice_effects").is_dir() # ready for the player's own effects
    handlers = {f.fn.__name__: f.fn for f in blocks.fns if "render_voice_effects_editor" in getattr(f.fn, "__qualname__", "")}

    config.definitions.get_config_value_definition("tts_service").value = "Piper"
    audio, message = handlers["on_with"]("draugr", "MaleNord", "Hello.")
    assert not audio["visible"] and "doesn't use voice effects" in message

    spoken = []
    def speak(config, getter, service, voice_type, text, as_heard=True):
        spoken.append((service, as_heard))
        path = str(tmp_path / "spoken.wav")
        open(path, "wb").write(tone_wav(seconds=1.0, silence=0.1))
        return path
    monkeypatch.setattr(voice_effects_ui, "speak_with_service", speak)
    config.definitions.get_config_value_definition("tts_service").value = "Inworld"
    config.definitions.get_config_value_definition("inworld_volume").value = 50
    with_effect, message = handlers["on_with"]("draugr", "MaleNord", "Hello.")
    assert with_effect["visible"] and message == "Inworld · Draugr · Strength 100"
    without, message = handlers["on_without"]("draugr", "MaleNord", "Hello.")
    assert without["visible"] and message == "Inworld · No effect"
    assert spoken == [("Inworld", False)] # spoken once, both buttons play it
    plain, rate = sf.read(without["value"], dtype="float32")
    assert speech_level_db(plain, rate) < -22 # Inworld's Volume at half, set on the page before the game asks
    sliders = {"pitch": 0, "echo": 0, "space": 0, "doubled": 0, "muffled": 0, "thin": 0, "growl": 0, "waver": 0}
    import numpy as np
    as_set, _ = handlers["on_with"]("draugr", "MaleNord", "Hello.", 100, *sliders.values()) # every slider at 0 on the page, not saved
    assert np.allclose(sf.read(as_set["value"], dtype="float32")[0], plain, atol=1e-3)


def test_effects_are_ticked_changed_copied_and_deleted_on_the_page(tmp_path):
    from src.tts import voice_effects
    from src.tts.voice_effects import SLIDER_KEYS
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    labels = {c.label: c for c in blocks.blocks.values() if getattr(c, "label", None)}
    assert labels["Effects in use"].value == [] # nothing is used until the player ticks it
    assert accordion_of(labels["Echo"]) == "Fine-tune" and accordion_of(labels["Strength"]) == "Voice Effects"
    handlers = {f.fn.__name__: f.fn for f in blocks.fns if "render_voice_effects_editor" in getattr(f.fn, "__qualname__", "")}
    effects = voice_effects.shared_voice_effects(config.save_folder)

    _, message = handlers["on_tick"](["ghost"])
    assert effects.find("ghost").on and "Ghost (ghosts)" in message

    values = [0.0] * len(SLIDER_KEYS)
    values[SLIDER_KEYS.index("echo")] = 70
    assert handlers["on_edit"]("ghost", ["Ghosts"], 100, *[effects.find("ghost").sliders[k] for k in SLIDER_KEYS]) == "" # as saved
    assert "Not saved yet" in handlers["on_edit"]("ghost", ["Ghosts", "Nord"], 60, *values)
    page = handlers["on_save"]("ghost", ["Ghosts", "Nord"], 60, *values)
    ghost = effects.find("ghost")
    assert ghost.strength == 60 and ghost.sliders["echo"] == 70 and "NordRaceVampire" in ghost.races and ghost.ghosts and ghost.on
    assert "next line" in page[-1]
    page = handlers["on_undo"]("ghost")
    assert effects.find("ghost").strength == 100 and effects.find("ghost").on and "as Mantella made it" in page[-1]

    page = handlers["on_save_new"]("Echoing Hall", "ghost", 80, *values)
    assert effects.find("echoing_hall") is not None and "Effects in use" in page[-1] and page[0]["value"] == "echoing_hall"
    page = handlers["on_delete"]("echoing_hall", "")
    assert effects.find("echoing_hall") is not None and "Press again" in page[-5]["value"] # the first press only asks
    page = handlers["on_delete"]("echoing_hall", "echoing_hall")
    assert effects.find("echoing_hall") is None and "Deleted Echoing Hall" in page[-1]


def test_try_it_starts_on_a_voice_type_named_after_the_effect_when_there_is_one(tmp_path):
    from src.tts import voice_effects
    from src.ui.voice_effects_ui import fitting_voice_type
    effects = voice_effects.VoiceEffects(str(tmp_path))
    assert fitting_voice_type(effects.find("draugr"), ["FemaleDraugr", "MaleNord", "maledraugr"]) == "maledraugr" # male first
    assert fitting_voice_type(effects.find("ghost"), ["FemaleNord", "MaleNord"]) == "MaleNord" # nothing named after ghosts
    assert fitting_voice_type(None, ["MaleNord"]) == "MaleNord"


def test_opening_the_page_again_shows_the_ticks_and_sliders_as_saved_now(tmp_path):
    from src.tts import voice_effects
    from src.tts.voice_effects import SLIDER_KEYS
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block() # built when Mantella starts: nothing ticked
    [on_open] = [f.fn for f in blocks.fns if "render_voice_effects_editor" in getattr(f.fn, "__qualname__", "")
                 and any(event == "load" for _, event in f.targets)]
    effects = voice_effects.shared_voice_effects(config.save_folder)
    effects.set_on(["draugr"]) # ticked on the page, which is then closed and opened again
    effects.save_changes("draugr", ["DraugrRace", "DraugrMagicRace"], False, 50, {"echo": 80})
    page = on_open("draugr")
    assert page[-2]["value"] == ["draugr"] # Effects in use: so ticking another one keeps Draugr
    assert page[3] == 50 and page[4 + SLIDER_KEYS.index("echo")] == 80 # Strength and Echo: so Save changes keeps them


def test_pitch_on_voice_clips_is_saved_for_the_picked_voice(tmp_path):
    from src.voice_clips.clip_store import VOICE_TYPES, shared_clip_store
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    on_pitch = next(f.fn for f in blocks.fns if getattr(f.fn, "__name__", "") == "on_pitch")
    assert "Pick a voice first" in on_pitch(None, 2)
    message = on_pitch("voice_types/femaleeventoned", -2)
    assert "2 steps deeper" in message
    assert shared_clip_store(str(tmp_path / "voice_clips")).pitch(VOICE_TYPES, "femaleeventoned") == -2
    labels = {c.label: c for c in blocks.blocks.values() if getattr(c, "label", None)}
    assert accordion_of(labels["Pitch"]) == NPC_VOICES_PART and accordion_of(labels["Volume (%)"]) == "Your voice setups"


def test_each_online_service_shows_its_key_then_how_npcs_sound(tmp_path):
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    config.definitions.get_config_value_definition("inworld_model").value = "TTS-2 Flash"
    blocks = StartUI(config).create_main_block()
    online = ("ElevenLabs", "Inworld", "HeyGen", "Qwen", "Boson AI")
    texts = [(c.value, accordion_of(c)) for c in blocks.blocks.values() if isinstance(getattr(c, "value", None), str)]
    folds = {SOUND_NAMES[c.elem_id]: c for c in blocks.blocks.values() if isinstance(c, gr.Column) and c.elem_id in SOUND_NAMES}
    for name in online:
        assert any(isinstance(c, gr.Accordion) and c.label == "Your key" and service_of(c) == name for c in blocks.blocks.values()), name
        assert name in folds, name # its Global settings, with the key in them
    def hidden(component) -> bool:
        """In a column hidden until another setting has a value (shown_when)"""
        parent = component.parent
        while parent is not None and not isinstance(parent, gr.Accordion) and getattr(parent, "elem_id", None) not in (*PART_NAMES, *SOUND_NAMES):
            if parent.visible is False:
                return True
            parent = parent.parent
        return False
    def setting_in(part: str, kind, value):
        return [c for c in blocks.blocks.values() if isinstance(c, kind) and service_of(c) == part and c.value == value]
    expressiveness = setting_in("HeyGen", gr.Slider, 0.5)
    assert len(expressiveness) == 1 and not hidden(expressiveness[0]) # HeyGen's Expressiveness, a slider
    assert setting_in("HeyGen", gr.Dropdown, "Highest") and setting_in("Boson AI", gr.Dropdown, "Lively") # Similarity, Boson's Expressiveness
    # TTS-2 Flash has no Delivery and no Directions, so they are hidden; they show with TTS-2
    delivery = setting_in("Inworld", gr.Dropdown, "Balanced")
    directions = [c for c in blocks.blocks.values() if getattr(c, "label", None) == "Directions" and service_of(c) in ("Inworld", "Qwen")]
    assert len(delivery) == 1 and hidden(delivery[0])
    assert {service_of(c): hidden(c) for c in directions} == {"Inworld": True, "Qwen": False}


def test_voice_clips_shows_the_services_style_settings_for_the_picked_voice(tmp_path):
    import os
    from src.voice_clips import fuz
    from src.voice_clips.clip_store import VOICE_TYPES, ClipInfo, shared_clip_store
    sound = str(tmp_path / "line.wav")
    fuz.to_wav(os.path.join(os.path.dirname(__file__), "..", "voice_clips", "data", "dialoguesolitude__000dd60f_1.fuz"), sound)
    clips = shared_clip_store(str(tmp_path / "voice_clips"))
    clips.set_clip(VOICE_TYPES, "femaleeventoned", sound, ClipInfo(name="FemaleEvenToned", path=r"sound\voice\skyrim.esm\femaleeventoned\a__000dd60f_1.fuz"))
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    show_fn = next(f for f in blocks.fns if getattr(f.fn, "__name__", "") == "show" and "render_voice_clips_editor" in f.fn.__qualname__)
    voice = f"{VOICE_TYPES}/femaleeventoned"
    def drawn(service: str) -> dict:
        """The style settings under Pitch, as drawn for the voice with this service shown"""
        out = dict(zip(show_fn.outputs, show_fn.fn(voice, service)))
        def find(kind, label):
            return next(update for c, update in out.items() if isinstance(c, kind) and c.label == label)
        back = next(update for c, update in out.items() if isinstance(c, gr.Button) and c.value == "") # Use the Text-to-Speech page's setting
        return {"Similarity": find(gr.Dropdown, "Similarity"), "Expressiveness": find(gr.Slider, "Expressiveness"), "back": back,
                "Delivery": find(gr.Dropdown, "Delivery"), "Boson": find(gr.Dropdown, "Expressiveness")}
    shown = drawn("HeyGen")
    assert shown["Similarity"]["visible"] and shown["Similarity"]["value"] == "" and shown["Similarity"]["choices"][0][0] == "Same as the Text-to-Speech page (Highest)"
    assert shown["Expressiveness"]["visible"] and shown["Expressiveness"]["value"] == 0.5 and not shown["back"]["visible"]
    assert not shown["Delivery"]["visible"] and not shown["Boson"]["visible"]
    assert not any(update["visible"] for update in drawn("Pocket TTS").values()) # Pocket TTS has none
    assert drawn("Inworld")["Delivery"]["visible"] and drawn("Boson AI")["Boson"]["value"] == "" # TTS-2, the page's Model

    lambdas = [f.fn for f in blocks.fns if getattr(f.fn, "__name__", "") == "<lambda>" and "render_voice_clips_editor" in f.fn.__qualname__]
    slider = next(fn for fn in lambdas if "on_style_slider" in fn.__code__.co_freevars)
    back = next(fn for fn in lambdas if "on_style_back" in fn.__code__.co_freevars)
    similarity = next(fn for fn in lambdas if "set_style" in fn.__code__.co_freevars and fn.__defaults__[0].name == "heygen_similarity")
    message, back_button = slider(voice, 0.2)
    assert "has Expressiveness 0.2" in message and back_button["visible"]
    assert clips.voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness") == {"heygen-voice-1-turbo": 0.2} # kept for the picked model
    assert drawn("HeyGen")["Expressiveness"]["value"] == 0.2
    message, slider_update, back_button = back(voice)
    assert "uses the Text-to-Speech page's Expressiveness" in message and slider_update["value"] == 0.5 and not back_button["visible"]
    assert "HeyGen clones the Female Even Toned voice again" in similarity(voice, "Medium")
    assert clips.voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_similarity", "heygen-voice-1-turbo") == "Medium"
    similarity(voice, "") # Same as the Text-to-Speech page
    assert clips.voice_settings(VOICE_TYPES, "femaleeventoned") == {}


def model_settings_ui(blocks, service: str):
    """A service's models on the page (ModelSettingsUI), found by its handler for the Model it speaks with"""
    return next(f.fn.__self__ for f in blocks.fns if getattr(f.fn, "__name__", "") == "on_use" and f.fn.__self__.name == service)


def test_picking_another_model_shows_its_own_settings(tmp_path):
    from src.tts.voice_style import HEYGEN_EXPRESSIVENESS
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    ui = model_settings_ui(blocks, "HeyGen")
    assert ui.used_ui.value == "heygen-voice-1-turbo" and ui.used_ui.allow_custom_value # names shown, IDs saved, any ID typed
    assert ("Turbo Free", "heygen-voice-1-turbo-free") in ui.used_ui.choices and service_of(ui.used_ui) is None # next to TTS Service
    assert ui.edit_ui.value == "heygen-voice-1-turbo" and ui.edit_ui.label == "Model" # next to Settings for
    texts = [c.value for c in blocks.blocks.values() if isinstance(c, gr.Markdown)]
    folds = [c for c in blocks.blocks.values() if isinstance(c, gr.Accordion) and service_of(c) == "HeyGen" and c.label == "Settings for Turbo"]
    assert len(folds) == 1 and not folds[0].open and any("Each model keeps its own settings" in t for t in texts if isinstance(t, str))
    def edited(value) -> dict:
        return dict(zip(ui.edit_outputs(), ui.on_edit(value, config.definitions.get_config_value_definition("heygen_model").value)))
    config.definitions.get_config_value_definition("heygen_expressiveness").value = 0.2 # set for Turbo
    out = edited("heygen-voice-1-turbo-free")
    assert out[ui.heading_ui]["label"] == "Settings for Turbo Free" and out[ui.values["heygen_expressiveness"]]["value"] == 0.5
    assert out[ui.use_btn]["visible"] and out[ui.use_btn]["value"] == "Use Turbo Free for HeyGen"
    config.definitions.get_config_value_definition("heygen_expressiveness").value = 0.9 # set for Turbo Free
    assert ui.style.page_value(HEYGEN_EXPRESSIVENESS) == 0.2 # HeyGen still speaks with Turbo, with Turbo's value
    out = edited("heygen-voice-1-turbo")
    assert out[ui.values["heygen_expressiveness"]]["value"] == 0.2 and not out[ui.use_btn]["visible"]
    # HeyGen speaks with Turbo Free: its settings show next to Settings for too, and the voices redraw for it
    used = dict(zip(ui.used_outputs(), ui.on_use("heygen-voice-1-turbo-free")))
    assert used[ui.edit_ui]["value"] == "heygen-voice-1-turbo-free" and not used[ui.use_btn]["visible"] and not used[ui.note_ui]["visible"]
    assert used[ui.shown_model_ui] == "HeyGen:heygen-voice-1-turbo-free"
    config.definitions.get_config_value_definition("heygen_model").value = "heygen-voice-1-turbo-free"
    assert ui.style.page_value(HEYGEN_EXPRESSIVENESS) == 0.9 # Turbo Free's, kept while Turbo showed
    edited("heygen-voice-1-turbo-free")
    assert ui.style.page_value(HEYGEN_EXPRESSIVENESS) == 0.9
    used = dict(zip(ui.used_outputs(), ui.on_use("heygen-voice-2")))
    assert used[ui.note_ui]["visible"] and "heygen-voice-2 isn't one of Mantella's models" in used[ui.note_ui]["value"]
    assert ("heygen-voice-2", "heygen-voice-2") in used[ui.edit_ui]["choices"]
    assert "Sent with every line on heygen-voice-2" in edited("heygen-voice-2")[ui.extra_intro_ui]
    # Inworld: TTS-2 Flash skips Delivery and Directions; a model typed in shows them
    ui = model_settings_ui(blocks, "Inworld")
    used = dict(zip(ui.used_outputs(), ui.on_use("inworld-tts-2-flash")))
    assert not used[ui.every_model_columns["inworld_directions"]]["visible"] and used[ui.every_model_columns["inworld_audio_tags"]]["visible"]
    assert used[ui.every_model_columns["inworld_volume"]]["visible"]
    assert not dict(zip(ui.edit_outputs(), ui.on_edit("inworld-tts-2-flash", "inworld-tts-2-flash")))[ui.columns["inworld_delivery"]]["visible"]
    assert dict(zip(ui.edit_outputs(), ui.on_edit("inworld-tts-3", "inworld-tts-3")))[ui.columns["inworld_delivery"]]["visible"]
    assert dict(zip(ui.used_outputs(), ui.on_use("inworld-tts-3")))[ui.every_model_columns["inworld_directions"]]["visible"]
    assert "inworld_volume" not in ui.values and "inworld_audio_tags" not in ui.values # for every model: never switched


def model_list_ui(blocks, service: str):
    """A service's Add / Remove buttons (ModelListUI), found by the handlers they're wired to"""
    from src.ui.online_voices_ui import ModelListUI
    for f in blocks.fns:
        if "ModelListUI.wire" in getattr(f.fn, "__qualname__", ""):
            found = [c.cell_contents for c in f.fn.__closure__ or () if isinstance(c.cell_contents, ModelListUI)]
            if found and found[0].name == service:
                return found[0]


def test_players_add_and_remove_models_next_to_settings_for(tmp_path):
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    ui, tools = model_settings_ui(blocks, "HeyGen"), model_list_ui(blocks, "HeyGen")
    assert not tools.put_back_btn.visible # nothing removed yet
    def pressed(out) -> dict:
        out = dict(zip(tools.outputs(ui), out))
        assert out[tools.status_ui]["visible"] # the message shows under the Model list
        return {**out, tools.status_ui: out[tools.status_ui]["value"]}
    assert "Type the model's name" in pressed(tools.on_add(ui, " "))[tools.status_ui]
    assert pressed(tools.on_add(ui, "Turbo"))[tools.status_ui] == "Turbo is already in the list."
    out = pressed(tools.on_add(ui, "heygen-voice-3"))
    assert out[tools.status_ui] == "Added heygen-voice-3 to both Model lists."
    assert ("heygen-voice-3", "heygen-voice-3") in out[ui.used_ui]["choices"] and ("heygen-voice-3", "heygen-voice-3") in out[ui.edit_ui]["choices"]
    out = pressed(tools.on_remove(ui, "heygen-voice-3", "heygen-voice-1-turbo"))
    assert out[tools.status_ui] == "Removed heygen-voice-3. Its settings are kept if you add it again."
    assert out[ui.edit_ui]["value"] == "heygen-voice-1-turbo" and "value" not in out[ui.used_ui] # HeyGen still speaks with Turbo
    assert all(value != "heygen-voice-3" for _, value in out[ui.edit_ui]["choices"]) and not out[tools.put_back_btn]["visible"]
    tools.on_add(ui, "heygen-voice-3")
    out = pressed(tools.on_remove(ui, "heygen-voice-1-turbo", "heygen-voice-1-turbo")) # the one HeyGen speaks with
    instead = out[ui.used_ui]["value"]
    assert instead != "heygen-voice-1-turbo" and out[ui.edit_ui]["value"] == instead
    assert out[tools.status_ui].startswith("Removed Turbo. HeyGen speaks with ") and out[tools.put_back_btn]["visible"]
    out = pressed(tools.on_put_back(ui))
    assert ("Turbo", "heygen-voice-1-turbo") in out[ui.used_ui]["choices"] and not out[tools.put_back_btn]["visible"]
    assert out[tools.status_ui] == "Mantella's models are back in the list."
    while len(ServiceModels.of("HeyGen").offered(config)) > 1:
        ServiceModels.of("HeyGen").remove(config, ServiceModels.of("HeyGen").offered(config)[0].id)
    last = ServiceModels.of("HeyGen").offered(config)[0].id
    assert pressed(tools.on_remove(ui, last, last))[tools.status_ui] == "Keep at least one model in the list."
    assert pressed(tools.on_remove(ui, "heygen-voice-9", last))[tools.status_ui] == "heygen-voice-9 isn't in the list."


def test_npc_voices_follow_the_picked_model(tmp_path, monkeypatch):
    import json
    import os
    from src.tts import model_catalog
    from src.tts.model_catalog import ModelCatalog
    from src.tts.online_voices import cloned_voices
    from src.voice_clips import fuz
    from src.voice_clips.clip_store import VOICE_TYPES, ClipInfo, shared_clip_store
    sound = str(tmp_path / "line.wav")
    fuz.to_wav(os.path.join(os.path.dirname(__file__), "..", "voice_clips", "data", "dialoguesolitude__000dd60f_1.fuz"), sound)
    clips = shared_clip_store(str(tmp_path / "voice_clips"))
    clips.set_clip(VOICE_TYPES, "femaleeventoned", sound, ClipInfo(name="FemaleEvenToned", path=r"sound\voice\skyrim.esm\femaleeventoned\a__000dd60f_1.fuz"))
    shipped = json.loads(open(os.path.join(os.path.dirname(model_catalog.__file__), "..", "..", "data", "voice_models.json"), encoding="utf-8").read())
    shipped["services"]["Qwen"]["models"].append({"id": "qwen-audio-4-tts", "name": "Qwen Audio 4", "price": 20, "clip_seconds": 90, "fill_seconds": 80})
    (tmp_path / "models.json").write_text(json.dumps(shipped), encoding="utf-8")
    monkeypatch.setattr(ModelCatalog, "_shared", ModelCatalog(str(tmp_path / "models.json")))
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    blocks = StartUI(config).create_main_block()
    show_fn = next(f for f in blocks.fns if getattr(f.fn, "__name__", "") == "show" and "render_voice_clips_editor" in f.fn.__qualname__)
    model_ui = model_settings_ui(blocks, "HeyGen").shown_model_ui
    assert any(model_ui._id in [block for block, _ in f.targets] for f in blocks.fns
               if "render_voice_clips_editor" in getattr(f.fn, "__qualname__", "")) # a model picked: NPC voices redraws
    heading = next(c for c in show_fn.outputs if isinstance(c, gr.Markdown) and "How this voice sounds" in str(c.value))
    out = dict(zip(show_fn.outputs, show_fn.fn(f"{VOICE_TYPES}/femaleeventoned", "HeyGen")))
    assert "How this voice sounds with Turbo" in out[heading]["value"] and "make HeyGen speak with it first" in out[heading]["value"]
    out = dict(zip(show_fn.outputs, show_fn.fn(f"{VOICE_TYPES}/femaleeventoned", "Pocket TTS")))
    assert out[heading]["value"] == "#### How this voice sounds" # no models
    # Qwen: a voice cloned for another model, and a model that takes more speech
    cloned_voices(config.save_folder, "Qwen").set("femaleeventoned", "qwen-voice", "abc|qwen-audio-3.1-tts-plus")
    head = show_fn.outputs[2] # Cloned from
    out = dict(zip(show_fn.outputs, show_fn.fn(f"{VOICE_TYPES}/femaleeventoned", "Qwen")))
    assert "takes up to 30 s" in out[head] and "Cloned for" not in out[head]
    config.definitions.get_config_value_definition("qwen_model").value = "qwen-audio-4-tts" # Qwen speaks with it
    out = dict(zip(show_fn.outputs, show_fn.fn(f"{VOICE_TYPES}/femaleeventoned", "Qwen")))
    assert "takes up to 90 s" in out[head] and "best with 80 s or more" in out[head]
    assert "Cloned for Qwen Audio 3.1 TTS Plus. It's cloned again for Qwen Audio 4 the next time it speaks." in out[head]


def test_settings_for_shows_one_service_and_follows_the_tts_service(tmp_path):
    from src.config.definitions.tts_definitions import SERVICE_PARTS, service_part_id
    from src.ui.settings_ui_constructor import part_of_service
    from src.ui.voice_clips_ui import prepares
    config = ConfigLoader(mygame_folder_path=str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    config.auto_launch_ui = False
    config.definitions.get_config_value_definition("tts_service").value = "Inworld"
    blocks = StartUI(config).create_main_block()
    picker = next(c for c in blocks.blocks.values() if isinstance(c, gr.Dropdown) and c.label == "Settings for")
    assert picker.value == "Inworld" and [value for _, value in picker.choices] == list(SERVICE_PARTS)
    parts = {c.elem_id: c for c in blocks.blocks.values() if isinstance(c, gr.Column) and c.elem_id in PART_NAMES}
    assert [name for name in SERVICE_PARTS if parts[service_part_id(name)].visible] == ["Inworld"] # opens on the TTS Service

    pick = next(f for f in blocks.fns if f.inputs and f.inputs[0] is picker)
    def picked(*args) -> dict:
        return dict(zip(pick.outputs, pick.fn(*args)))
    shown_service_ui = pick.outputs[-1]
    hear_part = pick.outputs[-2]
    quickest_way = pick.outputs[0] # the only note in the Settings for box (Pocket TTS adds one when it doesn't speak the Language)
    assert not quickest_way.visible
    out = picked("HeyGen", "(new setup)")
    assert [name for name in SERVICE_PARTS if out[parts[service_part_id(name)]]["visible"]] == ["HeyGen"]
    assert not out[quickest_way]["visible"]
    assert out[hear_part]["visible"] and out[shown_service_ui] == "HeyGen" # Hear it and NPC voices use HeyGen
    out = picked("Your voice setups", "(new setup)")
    assert out[quickest_way]["visible"]
    assert not out[hear_part]["visible"] and out[shown_service_ui] == "" # a setup has its own Hear it; none saved yet

    shown = next(f for f in blocks.fns if f.inputs == [shown_service_ui] and len(f.outputs) == 2 and parts[service_part_id(NPC_VOICES_PART)] in f.outputs)
    note, clips_part = shown.fn("Piper")
    assert note["visible"] and "Piper doesn't clone" in note["value"] and not clips_part["visible"]
    note, clips_part = shown.fn("Inworld")
    assert not note["visible"] and clips_part["visible"]
    assert not prepares("Inworld") and prepares("Pocket TTS") # Prepare all voices: only for a service on this PC

    follow = next(f for f in blocks.fns if f.fn is part_of_service)
    assert follow.outputs == [picker] # picking another TTS Service shows its settings
    assert part_of_service("Pocket TTS") == "Pocket TTS" and part_of_service("xva") == "xVASynth" and part_of_service("a setup that's gone") == "Piper"
