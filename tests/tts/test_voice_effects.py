"""Voice effects and Voice Volume on the lines of the newer voice services"""
import io
import json
import logging
import os

import numpy as np
import pytest
import soundfile as sf

from src.character_manager import Character
from src.games.equipment import Equipment
from src.tts import voice_effects
from src.tts.clip_service import ClipVoiceService
from src.tts.registry import VoiceServiceInfo
from src.tts.synthesization_options import SynthesizationOptions
from src.tts.voice_effects import (GAME_LOUDNESS_DB, LIMIT, REVERB_TAIL_MOST, SLIDERS, VoiceEffects, finish_line, race_ids, race_key,
                                   race_names, speech_level_db)
from src.tts.voice_picker import VoiceSpeaker
from tests.tts.fake_voice_server import tone_wav


def speech(level: float = 0.5, seconds: float = 1.5, rate: int = 24000) -> np.ndarray:
    """A voice-like sound: a tone that rises and falls, with a pause in the middle"""
    t = np.arange(int(rate * seconds)) / rate
    sound = level * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    sound[int(rate * 0.6):int(rate * 0.8)] = 0
    return sound.astype(np.float32)


def write(path, samples, rate=24000):
    sf.write(str(path), samples, rate, subtype="PCM_16")
    return str(path)


@pytest.fixture
def effects(tmp_path):
    return VoiceEffects(str(tmp_path / "voice_effects"))


def own_file(effects, name, data):
    os.makedirs(effects.own_folder, exist_ok=True)
    with open(os.path.join(effects.own_folder, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)


def test_mantellas_effects_all_read_and_none_is_ticked(effects):
    names = {e.name for e in effects.all()}
    assert {"draugr", "ghost", "dremora", "werewolf", "vampire_lord"} <= names
    assert effects.problems() == []
    assert not any(e.on for e in effects.all())
    assert effects.choose(VoiceSpeaker(race="DraugrRace")) is None # nobody gets an effect until it's ticked
    assert effects.choose(VoiceSpeaker(race="NordRace", is_ghost=True)) is None


def test_the_effect_comes_from_the_column_then_ghosts_then_race(effects):
    effects.set_on(["draugr", "ghost", "dremora", "vampire_lord"])
    assert effects.choose(VoiceSpeaker(race="[Race <DraugrRace (00000D53)>]")).name == "draugr"
    assert effects.choose(VoiceSpeaker(race="<Race: DraugrMagicRace (000F71DC)>")).name == "draugr"
    assert effects.choose(VoiceSpeaker(race="[Race <DLC1VampireBeastRace (0200283A)>]")).name == "vampire_lord"
    assert effects.choose(VoiceSpeaker(race="[Race <NordRace (00013746)>]")) is None
    assert effects.choose(VoiceSpeaker(race="[Race <NordRace (00013746)>]", is_ghost=True)).name == "ghost"
    assert effects.choose(VoiceSpeaker(race="NordRace", voice_effect="Dremora")).name == "dremora"
    assert effects.choose(VoiceSpeaker(race="NordRace", voice_effect="vampire lord")).name == "vampire_lord" # the name shown on the page works too
    assert effects.choose(VoiceSpeaker(race="DraugrRace", voice_effect="none")) is None
    assert effects.choose(None) is None


def test_an_effect_that_isnt_ticked_is_off_everywhere(effects):
    effects.set_on(["draugr"])
    assert effects.choose(VoiceSpeaker(race="NordRace", voice_effect="ghost")) is None # picked for this NPC, but Ghost isn't ticked
    assert effects.choose(VoiceSpeaker(race="DraugrRace", voice_effect="ghost")) is None # and their race's effect doesn't stand in
    assert effects.choose(VoiceSpeaker(race="WerewolfBeastRace")) is None
    effects.set_on(["draugr", "ghost"])
    assert effects.choose(VoiceSpeaker(race="NordRace", voice_effect="ghost")).name == "ghost"
    effects.set_on([])
    assert effects.choose(VoiceSpeaker(race="DraugrRace")) is None
    assert os.listdir(effects.own_folder) == [] # untouched Mantella effects leave no file behind


def test_an_unknown_effect_in_the_column_is_logged_and_the_usual_one_used(effects, caplog):
    effects.set_on(["draugr"])
    with caplog.at_level(logging.WARNING):
        assert effects.choose(VoiceSpeaker(name="Ulfric", race="DraugrRace", voice_effect="banshee")).name == "draugr"
    assert "banshee" in caplog.text


def test_race_names_from_the_game_and_the_files_match():
    assert race_key("[Race <DraugrRace (00000D53)>]") == race_key("DraugrRace") == race_key("Draugr") == "draugr"
    assert race_key("[Race <DLC1VampireBeastRace (0200283A)>]") == "dlc1vampirebeast"


def test_the_pages_race_names_cover_every_race_in_the_group():
    assert race_ids(["Nord", "FoxRace"]) == ["NordRace", "NordRaceVampire", "FoxRace"]
    assert race_names(["DraugrRace", "DraugrMagicRace", "FoxRace"]) == ["Draugr", "FoxRace"]
    assert race_names(["NordRaceVampire"]) == ["Nord"]


def test_your_changes_lie_over_mantellas_and_its_updates_still_reach_you(tmp_path):
    built_in = tmp_path / "mantella"
    built_in.mkdir()
    (built_in / "draugr.json").write_text(json.dumps({"name": "Draugr", "races": ["DraugrRace"], "sliders": {"pitch": -2, "echo": 20}}), encoding="utf-8")
    effects = VoiceEffects(str(tmp_path / "mine"), str(built_in))
    effects.save_changes("draugr", ["DraugrRace"], False, 80, {"pitch": -2, "echo": 50})
    saved = json.loads((tmp_path / "mine" / "draugr.json").read_text(encoding="utf-8"))
    assert saved == {"strength": 80, "sliders": {"echo": 50}} # only what differs from Mantella's
    (built_in / "draugr.json").write_text(json.dumps({"name": "Draugr", "races": ["DraugrRace"], "sliders": {"pitch": -3, "echo": 20, "growl": 10}}),
                                          encoding="utf-8") # an update
    draugr = effects.find("draugr")
    assert draugr.sliders["pitch"] == -3 and draugr.sliders["growl"] == 10 and draugr.sliders["echo"] == 50 and draugr.strength == 80
    assert draugr.changed and draugr.from_mantella and not draugr.is_own


def test_undo_brings_back_mantellas_and_keeps_the_tick(effects):
    effects.set_on(["ghost"])
    effects.save_changes("ghost", [], True, 40, {"echo": 90})
    assert effects.find("ghost").changed and effects.find("ghost").strength == 40
    ghost = effects.undo_changes("ghost")
    assert ghost.on and not ghost.changed and ghost.strength == 100 and ghost.sliders["echo"] == 45


def test_a_new_effect_starts_unticked_and_for_nobody_and_can_be_deleted(effects):
    effects.set_on(["ghost"])
    hall = effects.save_as_new("Echoing Hall", "ghost", 70, {"echo": 80, "space": 90})
    assert hall.name == "echoing_hall" and hall.is_own and not hall.on and not hall.races and not hall.ghosts
    assert hall.strength == 70 and hall.sliders["echo"] == 80 and hall.sliders["thin"] == 0
    assert hall.who() == "only NPCs picked in the Bio Editor"
    with pytest.raises(ValueError, match="already an effect"):
        effects.save_as_new("echoing hall", "ghost", 70, {})
    with pytest.raises(ValueError, match="already an effect"):
        effects.save_as_new("Ghost", "ghost", 70, {})
    effects.set_on(["ghost", "echoing_hall"])
    assert effects.choose(VoiceSpeaker(race="NordRace", voice_effect="Echoing Hall")).name == "echoing_hall"
    effects.save_changes("echoing_hall", ["NordRace"], False, 70, {"echo": 80})
    assert effects.choose(VoiceSpeaker(race="NordRace")).name == "echoing_hall"
    with pytest.raises(ValueError, match="Untick it"):
        effects.delete("ghost")
    effects.delete("echoing_hall")
    assert effects.find("echoing_hall") is None and effects.find("ghost").on


def test_a_new_effect_cant_be_called_what_means_no_effect(effects):
    for name in ("None", "off", "No"):
        with pytest.raises(ValueError, match="no effect"):
            effects.save_as_new(name, "ghost", 100, {})
    assert not os.path.exists(effects.own_folder) or os.listdir(effects.own_folder) == []


def test_a_new_effect_doesnt_write_over_your_effect_with_a_mistake(effects):
    own_file(effects, "my_hall", {"name": "My Hall", "sliders": {"echo": "loud"}})
    assert effects.find("my_hall") is None and effects.problems()
    with pytest.raises(ValueError, match="has a mistake"):
        effects.save_as_new("My Hall", "ghost", 100, {"echo": 50})
    with open(os.path.join(effects.own_folder, "my_hall.json"), encoding="utf-8") as f:
        assert json.load(f)["sliders"] == {"echo": "loud"} # still there to fix


def test_your_own_effect_for_a_race_wins_over_mantellas(effects):
    own_file(effects, "barrow_king", {"name": "Barrow King", "races": ["DraugrRace"], "on": True, "sliders": {"pitch": -5}})
    effects.set_on(["draugr", "barrow_king"])
    assert effects.choose(VoiceSpeaker(race="DraugrRace")).name == "barrow_king"


def test_a_broken_effect_is_listed_and_not_used(effects):
    own_file(effects, "broken", {"name": "Broken", "on": True, "extra": [{"no_such_filter": {}}]})
    own_file(effects, "typo", {"name": "Typo", "on": True, "races": ["NordRace"], "extra": [{"lowpass": {"freq": 3000}}]})
    own_file(effects, "loud", {"name": "Loud", "on": True, "sliders": {"volume": 10}})
    problems = effects.problems()
    assert any("broken.json" in p and "no_such_filter" in p for p in problems)
    assert any("typo.json" in p and "effect 1 (lowpass)" in p for p in problems)
    assert any("loud.json" in p and "no slider called 'volume'" in p for p in problems)
    assert effects.find("broken") is None and effects.choose(VoiceSpeaker(race="NordRace")) is None


def test_each_slider_changes_the_sound_and_none_leaves_it_as_it_was(effects, tmp_path):
    rate = 24000
    plain_path = write(tmp_path / "plain.wav", speech(), rate)
    finish_line(plain_path, None)
    plain, _ = sf.read(plain_path, dtype="float32")
    silent = effects.find("ghost").with_values(100, {})
    path = write(tmp_path / "silent.wav", speech(), rate)
    finish_line(path, silent)
    assert np.allclose(sf.read(path, dtype="float32")[0], plain, atol=1e-3) # every slider at 0: as without an effect
    for slider in SLIDERS:
        effect = effects.find("ghost").with_values(100, {slider.key: slider.high, "echo": 50} if slider.key == "space" else {slider.key: slider.high})
        path = write(tmp_path / f"{slider.key}.wav", speech(), rate)
        finish_line(path, effect)
        out, _ = sf.read(path, dtype="float32")
        assert out.size != plain.size or not np.allclose(out, plain, atol=0.01), slider.key
    no_strength = effects.find("ghost").with_values(0, {"echo": 100, "pitch": -5})
    path = write(tmp_path / "no_strength.wav", speech(), rate)
    finish_line(path, no_strength)
    assert np.allclose(sf.read(path, dtype="float32")[0], plain, atol=1e-3) # Strength 0: nothing of it is heard


def test_every_effect_keeps_the_line_about_as_long(effects, tmp_path):
    rate = 24000
    for effect in effects.all():
        loudest = effect.with_values(100, {**effect.sliders, "echo": 100, "space": 100})
        for each in (effect, loudest):
            path = write(tmp_path / f"{effect.name}.wav", speech(), rate)
            finish_line(path, each)
            out, out_rate = sf.read(path)
            assert out_rate == rate and out.ndim == 1, effect.name
            assert 1.5 * rate * 0.98 <= len(out) <= 1.5 * rate + rate * (REVERB_TAIL_MOST + 0.05), effect.name # an echo's tail at most adds a little


def test_the_voices_pitch_and_the_effects_are_one_pass(effects, tmp_path, monkeypatch):
    passes = []
    real = voice_effects.run_filters
    def counting(samples, rate, steps):
        passes.extend(s for s in steps if s[0] == "pitch")
        return real(samples, rate, steps)
    monkeypatch.setattr(voice_effects, "run_filters", counting)
    path = write(tmp_path / "line.wav", speech())
    finish_line(path, effects.find("draugr"), 100, 2)
    assert passes == [("pitch", str(voice_effects.pitch_factor(-0.5)))] # +2 for the voice, -2.5 for Draugr


def test_a_quiet_line_comes_out_as_loud_as_the_games_lines(tmp_path):
    path = write(tmp_path / "quiet.wav", speech(level=0.06))
    finish_line(path, None, 100)
    out, rate = sf.read(path, dtype="float32")
    assert abs(speech_level_db(out, rate) - GAME_LOUDNESS_DB) < 1.0


def test_twice_the_volume_is_louder_without_clipping(tmp_path):
    loud = write(tmp_path / "loud.wav", speech(level=0.9))
    finish_line(loud, None, 200)
    out, rate = sf.read(loud, dtype="float32")
    assert np.abs(out).max() <= LIMIT + 0.001
    assert speech_level_db(out, rate) > GAME_LOUDNESS_DB + 3


def test_half_the_volume_is_quieter(tmp_path):
    path = write(tmp_path / "half.wav", speech())
    finish_line(path, None, 50)
    out, rate = sf.read(path, dtype="float32")
    assert abs(speech_level_db(out, rate) - (GAME_LOUDNESS_DB - 6)) < 1.0


class DraugrService(ClipVoiceService):
    def built_in_voice(self, gender):
        return "george"

    def speak(self, text, voice, out_path):
        with open(out_path, "wb") as f:
            f.write(tone_wav(seconds=1.0, silence=0.1))


def draugr() -> Character:
    return Character("0001D4B5", "0001D4B5", "Draugr Deathlord", 0, "[Race <DraugrRace (00000D53)>]", False, "", False, False, 0, True,
                     "MaleDraugr", "MaleDraugr", "MaleDraugr", "", "", Equipment({}), {})


def test_the_lip_file_is_made_before_the_effect(voice_config, monkeypatch):
    voice_config.lip_generation = "enabled"
    voice_effects.shared_voice_effects(voice_config.save_folder).set_on(["draugr"])

    service = DraugrService(voice_config, VoiceServiceInfo("Fake", factory=None))
    heard_by_lip_file = []
    monkeypatch.setattr(service, "_generate_voiceline_files", lambda wav, line, skip_lip_generation=False: heard_by_lip_file.append(open(wav, "rb").read()))
    path = service.synthesize("MaleDraugr", "None shall pass.", "MaleDraugr", "MaleDraugr", "", SynthesizationOptions(False, False),
                              speaker=VoiceSpeaker.from_character(draugr()))
    [before] = heard_by_lip_file
    plain, rate = sf.read(io.BytesIO(before), dtype="float32")
    played, _ = sf.read(path, dtype="float32")
    assert len(played) >= len(plain) and not np.allclose(played[:len(plain)], plain, atol=0.01) # the effect came after


def test_a_ghost_flag_from_the_game_reaches_the_voice_choice():
    character = Character("1", "1", "Ghost", 0, "NordRace", False, "", False, False, 0, False, "MaleNord", "", "", "", "", Equipment({}), {},
                          is_ghost=True, voice_effect="")
    speaker = VoiceSpeaker.from_character(character)
    assert speaker.is_ghost and speaker.voice_effect == ""


def test_none_in_the_column_turns_the_effect_off_for_that_npc(effects):
    effects.set_on(["draugr", "ghost"])
    assert effects.choose(VoiceSpeaker.from_character(draugr())).name == "draugr"
    quiet_draugr = draugr()
    quiet_draugr.voice_effect = "none"
    assert effects.choose(VoiceSpeaker.from_character(quiet_draugr)) is None
    ghost = draugr()
    ghost.voice_effect = "Ghost"
    assert effects.choose(VoiceSpeaker.from_character(ghost)).name == "ghost"


def test_an_effect_that_fails_on_a_line_still_leaves_voice_volume_and_warns_once(effects, tmp_path, monkeypatch, caplog):
    draugr_effect = effects.find("draugr").with_values(100, {"echo": 30, "growl": 20}) # no pitch, so the line itself still plays
    monkeypatch.setattr(voice_effects, "run_filters", lambda *a: (_ for _ in ()).throw(RuntimeError("filter broke")))
    voice_effects._warned.clear()
    with caplog.at_level(logging.WARNING):
        for i in range(2):
            path = write(tmp_path / f"line{i}.wav", speech(level=0.06))
            finish_line(path, draugr_effect, 100)
            out, rate = sf.read(path, dtype="float32")
            assert abs(speech_level_db(out, rate) - GAME_LOUDNESS_DB) < 1.0
    assert caplog.text.count("filter broke") == 1


def test_a_line_that_cant_be_changed_plays_as_it_is(voice_config, tmp_path, caplog):
    service = DraugrService(voice_config, VoiceServiceInfo("Fake", factory=None))
    path = str(tmp_path / "line.wav")
    open(path, "wb").write(b"not a sound file")
    with caplog.at_level(logging.WARNING):
        service.finish_line(path, None)
    assert open(path, "rb").read() == b"not a sound file" and "plays as it is" in caplog.text


# ---- each service's Volume ----

def test_each_service_has_its_own_volume():
    from types import SimpleNamespace
    from src.tts.clip_service import service_volume
    from src.tts.registry import voice_services
    from src.tts import voice_setups as vs
    volumes = {"pocket_tts_volume": 80, "elevenlabs_volume": 120, "inworld_volume": 150}
    config = SimpleNamespace(chosen_voice_volume=lambda identifier: volumes[identifier])
    assert [service_volume(config, voice_services.find(n)) for n in ("Pocket TTS", "ElevenLabs", "Inworld")] == [80, 120, 150]
    setup = vs.to_service_info(vs.normalized({"name": "Kokoro", "volume": 60}))
    assert service_volume(config, setup) == 60


def test_a_setups_volume_is_kept_in_range_and_checked():
    from src.tts import voice_setups as vs
    assert vs.normalized({"name": "a"})["volume"] == 100
    assert vs.normalized({"name": "a", "volume": 500})["volume"] == 300
    assert any("Volume" in p for p in vs.problems({"name": "a", "style": "openai", "url": "http://x", "volume": "loud"}, []))
    assert any("Volume" in p for p in vs.problems({"name": "a", "style": "openai", "url": "http://x", "volume": 5}, []))
    assert not any("Volume" in p for p in vs.problems({"name": "a", "style": "openai", "url": "http://x", "volume": 150}, []))


# ---- each voice's Pitch ----

def clip_store_with(tmp_path):
    from src.voice_clips.clip_store import NPCS, VOICE_TYPES, ClipInfo, ClipStore
    os.makedirs(tmp_path, exist_ok=True)
    store = ClipStore(str(tmp_path / "voice_clips"))
    sound = write(tmp_path / "clip.wav", speech())
    store.set_clip(VOICE_TYPES, "femaleeventoned", sound, ClipInfo(name="FemaleEvenToned"))
    store.set_clip(NPCS, "skyrimesm000a2c94", sound, ClipInfo(name="Lydia", voice_type="FemaleEvenToned"))
    return store


def test_a_voices_pitch_is_kept_moved_and_removed_with_it(tmp_path):
    from src.voice_clips.clip_store import NPCS, VOICE_TYPES
    store = clip_store_with(tmp_path)
    store.set_pitch(VOICE_TYPES, "femaleeventoned", 2)
    store.set_pitch(NPCS, "skyrimesm000a2c94", -1.5)
    assert store.pitch(VOICE_TYPES, "femaleeventoned") == 2 and store.pitch(NPCS, "skyrimesm000a2c94") == -1.5
    store.set_clip(VOICE_TYPES, "femaleeventoned", write(tmp_path / "other.wav", speech(level=0.3)), store.info(VOICE_TYPES, "femaleeventoned"))
    assert store.pitch(VOICE_TYPES, "femaleeventoned") == 2 # swapping the clip keeps it
    store.use_npc_clip_for_voice_type("skyrimesm000a2c94")
    assert store.pitch(VOICE_TYPES, "femaleeventoned") == -1.5 # the voice type now sounds just like Lydia did
    assert store.pitch(NPCS, "skyrimesm000a2c94") == 0
    store.set_pitch(VOICE_TYPES, "femaleeventoned", 0)
    assert store.pitch(VOICE_TYPES, "femaleeventoned") == 0


def test_going_back_to_the_voice_types_voice_drops_the_npcs_pitch(tmp_path):
    from src.voice_clips.clip_store import NPCS
    store = clip_store_with(tmp_path)
    store.set_pitch(NPCS, "skyrimesm000a2c94", 3)
    store.remove(NPCS, "skyrimesm000a2c94")
    assert store.pitch(NPCS, "skyrimesm000a2c94") == 0


def test_a_line_gets_the_pitch_of_the_voice_it_is_spoken_in(tmp_path):
    from src.tts.clip_service import voice_pitch
    from src.tts.voice_picker import BUILT_IN, OWN_CLIP, VOICE_TYPE_CLIP, VoicePick
    from src.voice_clips.clip_store import NPCS, VOICE_TYPES
    store = clip_store_with(tmp_path)
    store.set_pitch(VOICE_TYPES, "femaleeventoned", 2)
    store.set_pitch(NPCS, "skyrimesm000a2c94", -1)
    assert voice_pitch(store, VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", "a.wav")) == 2
    assert voice_pitch(store, VoicePick(OWN_CLIP, "skyrimesm000a2c94", "a.wav")) == -1
    assert voice_pitch(store, VoicePick(BUILT_IN, "alba", None)) == 0


def test_pitch_changes_the_voice_but_not_how_long_the_line_is(tmp_path):
    plain = write(tmp_path / "plain.wav", speech())
    deeper = write(tmp_path / "deeper.wav", speech())
    finish_line(plain, None, 100, 0)
    finish_line(deeper, None, 100, -3)
    a, rate = sf.read(plain, dtype="float32")
    b, _ = sf.read(deeper, dtype="float32")
    assert abs(len(a) - len(b)) < rate * 0.02
    def main_frequency(samples):
        spectrum = np.abs(np.fft.rfft(samples))
        return np.argmax(spectrum) * rate / len(samples)
    assert main_frequency(b) < main_frequency(a) * 0.9 # 3 steps deeper is about 16% lower


def test_a_shared_voices_file_carries_the_pitch(tmp_path):
    from src.voice_clips.clip_store import NPCS, VOICE_TYPES, ClipInfo
    from src.voice_clips.picks import KEEP_MINE, TAKE_THEIRS, export_picks, import_picks
    store = clip_store_with(tmp_path)
    info = store.info(VOICE_TYPES, "femaleeventoned")
    info.path = r"sound\voice\skyrim.esm\femaleeventoned\a.fuz"
    store.save_info(VOICE_TYPES, "femaleeventoned", info)
    store.set_pitch(VOICE_TYPES, "femaleeventoned", 1.5)
    picks = export_picks(store)
    assert picks[VOICE_TYPES]["femaleeventoned"]["pitch"] == 1.5

    class Requests:
        def ask_for_lines(self, *args, **kwargs):
            pass
    friend = clip_store_with(tmp_path / "friend")
    friend_info = friend.info(VOICE_TYPES, "femaleeventoned")
    friend_info.path = info.path # the same line: only the pitch differs
    friend.save_info(VOICE_TYPES, "femaleeventoned", friend_info)
    import_picks(friend, Requests(), picks, KEEP_MINE)
    assert friend.pitch(VOICE_TYPES, "femaleeventoned") == 0
    import_picks(friend, Requests(), picks, TAKE_THEIRS)
    assert friend.pitch(VOICE_TYPES, "femaleeventoned") == 1.5
    picks[VOICE_TYPES]["maleorc"] = {"name": "MaleOrc", "line": r"sound\voice\skyrim.esm\maleorc\b.fuz", "pitch": -2}
    import_picks(friend, Requests(), picks, KEEP_MINE)
    assert friend.pitch(VOICE_TYPES, "maleorc") == -2 # a voice they didn't have arrives with it


def test_a_voices_style_settings_are_kept_moved_and_removed_with_it(tmp_path):
    from src.voice_clips.clip_store import NPCS, VOICE_TYPES
    store = clip_store_with(tmp_path)
    store.set_voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness", 0.3)
    store.set_voice_setting(NPCS, "skyrimesm000a2c94", "heygen_similarity", "Medium")
    store.set_pitch(NPCS, "skyrimesm000a2c94", -1)
    assert store.voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness") == 0.3
    store.set_voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness", None) # Same as the Text-to-Speech page
    assert store.voice_settings(VOICE_TYPES, "femaleeventoned") == {}
    store.use_npc_clip_for_voice_type("skyrimesm000a2c94") # the voice type now sounds just like Lydia did, settings and all
    assert store.voice_settings(VOICE_TYPES, "femaleeventoned") == {"heygen_similarity": "Medium", "pitch": -1}
    assert store.voice_settings(NPCS, "skyrimesm000a2c94") == {}
    store.set_voice_setting(NPCS, "skyrimesm000a2c94", "boson_expressiveness", "Flat")
    store.remove(NPCS, "skyrimesm000a2c94") # Go back to their voice type's voice
    assert store.voice_settings(NPCS, "skyrimesm000a2c94") == {}


def test_a_line_gets_the_style_of_the_voice_it_is_spoken_in(tmp_path):
    from types import SimpleNamespace
    from src.tts.voice_picker import BUILT_IN, FILE_CLIP, OWN_CLIP, VOICE_TYPE_CLIP, VoicePick
    from src.tts.voice_style import HEYGEN_EXPRESSIVENESS, HEYGEN_SIMILARITY, ServiceStyle
    from src.voice_clips.clip_store import NPCS, VOICE_TYPES
    store = clip_store_with(tmp_path)
    config = SimpleNamespace(heygen_expressiveness=0.7, heygen_similarity="Very high") # the Text-to-Speech page
    style = ServiceStyle(config, "HeyGen")
    store.set_voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness", 0.2)
    store.set_voice_setting(NPCS, "skyrimesm000a2c94", "heygen_expressiveness", 0.9)
    store.set_voice_setting(VOICE_TYPES, "malenord", "heygen_similarity", "not a choice") # a hand-edited file: the page's is used
    assert style.voice_value(store, VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", "a.wav"), HEYGEN_EXPRESSIVENESS) == 0.2
    assert style.voice_value(store, VoicePick(FILE_CLIP, "femaleeventoned", "a.wav"), HEYGEN_EXPRESSIVENESS) == 0.2 # a stand-in shares it
    assert style.voice_value(store, VoicePick(OWN_CLIP, "skyrimesm000a2c94", "a.wav"), HEYGEN_EXPRESSIVENESS) == 0.9
    assert style.voice_value(store, VoicePick(BUILT_IN, "alba", None), HEYGEN_EXPRESSIVENESS) == 0.7
    assert style.voice_value(store, VoicePick(VOICE_TYPE_CLIP, "malenord", "a.wav"), HEYGEN_SIMILARITY) == "Very high"
    assert ServiceStyle(SimpleNamespace(), "HeyGen").voice_value(store, None, HEYGEN_SIMILARITY) == "Highest" # the page's default


def test_a_shared_voices_file_carries_the_style_settings(tmp_path):
    from src.voice_clips.clip_store import VOICE_TYPES
    from src.voice_clips.picks import KEEP_MINE, TAKE_THEIRS, export_picks, import_picks
    store = clip_store_with(tmp_path)
    info = store.info(VOICE_TYPES, "femaleeventoned")
    info.path = r"sound\voice\skyrim.esm\femaleeventoned\a.fuz"
    store.save_info(VOICE_TYPES, "femaleeventoned", info)
    store.set_voice_setting(VOICE_TYPES, "femaleeventoned", "heygen_expressiveness", 0.3)
    picks = export_picks(store)
    assert picks[VOICE_TYPES]["femaleeventoned"]["style"] == {"heygen_expressiveness": 0.3}

    class Requests:
        def ask_for_lines(self, *args, **kwargs):
            pass
    friend = clip_store_with(tmp_path / "friend")
    friend_info = friend.info(VOICE_TYPES, "femaleeventoned")
    friend_info.path = info.path
    friend.save_info(VOICE_TYPES, "femaleeventoned", friend_info)
    friend.set_voice_setting(VOICE_TYPES, "femaleeventoned", "boson_expressiveness", "Flat")
    import_picks(friend, Requests(), picks, KEEP_MINE)
    assert friend.voice_settings(VOICE_TYPES, "femaleeventoned") == {"boson_expressiveness": "Flat"}
    picks[VOICE_TYPES]["femaleeventoned"]["style"]["unknown_setting"] = 1 # from a newer Mantella: left out
    picks[VOICE_TYPES]["femaleeventoned"]["style"]["heygen_similarity"] = "Way too high" # not a choice: left out
    import_picks(friend, Requests(), picks, TAKE_THEIRS)
    assert friend.voice_settings(VOICE_TYPES, "femaleeventoned") == {"heygen_expressiveness": 0.3}


# ---- Talking speed (Step 13) ----

class PlainService(DraugrService):
    pass


def test_the_lip_file_is_made_from_the_faster_line(voice_config, monkeypatch):
    voice_config.lip_generation = "enabled"
    heard_by_lip_file = []
    def lip_lengths(speed):
        voice_config.pocket_tts_speed = speed
        service = PlainService(voice_config, VoiceServiceInfo("Pocket TTS", factory=None))
        monkeypatch.setattr(service, "_generate_voiceline_files", lambda wav, line, skip_lip_generation=False: heard_by_lip_file.append(open(wav, "rb").read()))
        service.synthesize("MaleNord", "Hello there.", "MaleNord", "MaleNord", "", SynthesizationOptions(False, False))
        samples, rate = sf.read(io.BytesIO(heard_by_lip_file[-1]), dtype="float32")
        return len(samples) / rate
    plain, faster = lip_lengths(1.0), lip_lengths(1.25)
    assert abs(faster - plain / 1.25) < 0.05


def test_a_voices_own_talking_speed_wins_over_the_pages_for_its_model(voice_config, tmp_path):
    from src.tts.voice_picker import VOICE_TYPE_CLIP, VoicePick
    from src.tts.voice_style import SPEEDS, SetupStyle, ServiceStyle
    from src.voice_clips.clip_store import VOICE_TYPES, ClipStore
    store = ClipStore(str(tmp_path / "clips"))
    pick = VoicePick(VOICE_TYPE_CLIP, "femaleeventoned", str(tmp_path / "clip.wav"))
    voice_config.iodine_speed = 1.2
    style = ServiceStyle(voice_config, "Iodine")
    setting = style.speed_setting()
    assert setting is SPEEDS["Iodine"] and style.voice_value(store, pick, setting) == 1.2 # the page's
    style.set_voice_own(store, VOICE_TYPES, "femaleeventoned", setting, 0.8)
    assert style.voice_value(store, pick, setting) == 0.8
    voice_config.iodine_model = "iodine-v3" # another model: the voice's own value was for v2
    assert style.voice_value(store, pick, setting) == 1.2
    setup = VoiceServiceInfo("My XTTS", factory=None, is_setup=True, setup={"speed": 1.3})
    setup_style = SetupStyle(voice_config, setup)
    assert setup_style.voice_value(store, pick, setup_style.speed_setting()) == 1.3 # kept in the setup
    setup_style.set_voice_own(store, VOICE_TYPES, "femaleeventoned", setup_style.speed_setting(), 0.9)
    assert setup_style.voice_value(store, pick, setup_style.speed_setting()) == 0.9
    other = SetupStyle(voice_config, VoiceServiceInfo("Other setup", factory=None, is_setup=True, setup={}))
    assert other.voice_value(store, pick, other.speed_setting()) == 1.0 # a voice's own value belongs to one setup
