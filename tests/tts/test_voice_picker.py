import os

import numpy as np
import soundfile as sf

from src.tts.voice_picker import (BUILT_IN, FILE_CLIP, NAMED, OWN_CLIP, RACE_CLIP, VOICE_TYPE_CLIP, VoicePicker, VoiceSpeaker, file_value,
                                  race_and_gender_voice_type, race_name)
from src.voice_clips.clip_store import ClipStore


def clip(folder, sub_folder: str, name: str):
    """A clip as Mantella keeps it: the sound, and the .json saying where it came from"""
    path = os.path.join(folder, sub_folder)
    os.makedirs(path, exist_ok=True)
    sf.write(os.path.join(path, name + ".wav"), np.zeros(100, dtype=np.float32), 24000)
    with open(os.path.join(path, name + ".json"), "w", encoding="utf-8") as file:
        file.write("{}")
    return os.path.join(path, name + ".wav")


def picker(folder, own_voices=()):
    return VoicePicker(ClipStore(str(folder)), lambda gender: "alba" if gender == 1 else "george", lambda name: name in own_voices)


def test_the_order_is_named_own_voice_type_race_built_in(tmp_path):
    lydia = VoiceSpeaker("Lydia", 1, "<Race: NordRace (00013746)>", "FemaleEvenToned", "Skyrim.esm", "000A2C94")
    p = picker(tmp_path, own_voices={"alba"})
    assert p.pick(lydia) == p.pick(lydia) and p.pick(lydia).kind == BUILT_IN and p.pick(lydia).key == "alba"

    clip(tmp_path, "voice_types", "FemaleNord")
    assert p.pick(lydia).kind == RACE_CLIP and p.pick(lydia).key == "femalenord"

    clip(tmp_path, "voice_types", "FemaleEvenToned")
    assert p.pick(lydia).kind == VOICE_TYPE_CLIP and p.pick(lydia).key == "femaleeventoned"

    own = clip(tmp_path, "npcs", "Skyrim.esm_000A2C94")
    assert p.pick(lydia).kind == OWN_CLIP and p.pick(lydia).clip_path == own

    assert p.pick(lydia, named_voice="alba").kind == NAMED
    assert p.pick(lydia, named_voice="nobody").kind == OWN_CLIP # a name with no clip and no voice of the service is passed over


def test_a_named_voice_can_be_a_clip(tmp_path):
    clip(tmp_path, "voice_types", "FemaleSultry")
    pick = picker(tmp_path).pick(VoiceSpeaker(gender=1), named_voice="Female Sultry")
    assert pick.kind == NAMED and pick.clip_path.endswith("FemaleSultry.wav")


def test_an_npc_with_no_voice_lines_uses_the_voice_type_in_their_character_file(tmp_path):
    auri = VoiceSpeaker("Auri", 1, "WoodElfRace", "MyFollowerVoice", "MyFollower.esp", "00000D62", file_voice_type="FemaleSultry")
    assert auri.stand_ins() == ["FemaleSultry", "FemaleYoungEager"]
    p = picker(tmp_path)
    clip(tmp_path, "voice_types", "FemaleYoungEager")
    assert p.pick(auri).kind == RACE_CLIP
    clip(tmp_path, "voice_types", "FemaleSultry")
    assert p.pick(auri).kind == FILE_CLIP and p.pick(auri).key == "femalesultry"
    unlisted = VoiceSpeaker("Auri", 1, "WoodElfRace", "MyFollowerVoice", file_voice_type=file_value(float("nan")))
    assert unlisted.stand_ins() == ["FemaleYoungEager"] # an empty cell in the character file


def test_a_sound_file_mantella_did_not_copy_is_left_alone(tmp_path):
    os.makedirs(os.path.join(tmp_path, "voice_types"))
    sf.write(os.path.join(tmp_path, "voice_types", "MaleNord.wav"), np.zeros(100, dtype=np.float32), 24000)
    assert ClipStore(str(tmp_path)).voice_type_clip("MaleNord") is None


def test_race_and_gender_table():
    assert race_name("<Race: ImperialRace (00013744)>") == "Imperial"
    assert race_name("[Race <ImperialRace (00013744)>]") == "Imperial"
    assert race_name("DarkElf") == "DarkElf"
    assert race_and_gender_voice_type("<Race: ImperialRace (00013744)>", 0) == "MaleEvenToned"
    assert race_and_gender_voice_type("RedguardRace", 1) == "FemaleSultry"
    assert race_and_gender_voice_type("DremoraRace", 1) == "FemaleNord"
    assert race_and_gender_voice_type(None, None) == "MaleNord"


def test_a_new_clip_is_seen_without_starting_again(tmp_path):
    store = ClipStore(str(tmp_path))
    assert store.voice_type_clip("MaleNord") is None
    path = clip(tmp_path, "voice_types", "MaleNord")
    os.utime(os.path.join(tmp_path, "voice_types"), (1, 1)) # folders keep their time on some file systems; a changed time is what is watched
    assert store.voice_type_clip("Male Nord") == path


def test_an_empty_named_voice_cell_is_passed_over(tmp_path):
    """An empty advanced_voice_model cell comes as nan, a number. Pocket TTS's voice check lowercases the name and failed on it"""
    def has_voice(name):
        return name.lower() in {"alba"}
    p = VoicePicker(ClipStore(str(tmp_path)), lambda gender: "george", has_voice)
    guard = VoiceSpeaker("Redoran Guard", 0, "<Race: DarkElfRace (00013742)>", "DLC2MaleDarkElfCynical")
    assert p.pick(guard, named_voice=float("nan")).kind == BUILT_IN
