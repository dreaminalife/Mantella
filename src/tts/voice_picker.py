"""Choosing an NPC's voice, in one order for every service that copies voices:

1. a voice named for the NPC in the character file (its advanced_voice_model column)
2. the NPC's own clip
3. their voice type's clip
4. the clip of the voice type in their character file (its skyrim_voice_folder or voice_model column), for an NPC whose own voice type
   has no lines, such as an unvoiced follower. An NPC not in the file gets the one Mantella guessed for them
5. the clip of the voice type their race and gender point to (an Imperial man gets Male Even Toned)
6. the service's built-in male or female voice
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from src.voice_clips.clip_store import ClipStore, voice_key

NAMED = "named"
OWN_CLIP = "own clip"
VOICE_TYPE_CLIP = "voice type clip"
FILE_CLIP = "character file clip"
RACE_CLIP = "race and gender clip"
BUILT_IN = "built-in voice"

# One race and gender table for every new service. Piper, XTTS and xVASynth keep their own tables in skyrim.py
MALE_VOICE_TYPES: dict[str, str] = {
    'Argonian': 'MaleArgonian',
    'Breton': 'MaleEvenToned',
    'DarkElf': 'MaleDarkElf',
    'HighElf': 'MaleElfHaughty',
    'Imperial': 'MaleEvenToned',
    'Khajiit': 'MaleKhajiit',
    'Nord': 'MaleNord',
    'Orc': 'MaleOrc',
    'Redguard': 'MaleEvenToned',
    'WoodElf': 'MaleYoungEager',
}
FEMALE_VOICE_TYPES: dict[str, str] = {
    'Argonian': 'FemaleArgonian',
    'Breton': 'FemaleEvenToned',
    'DarkElf': 'FemaleDarkElf',
    'HighElf': 'FemaleElfHaughty',
    'Imperial': 'FemaleEvenToned',
    'Khajiit': 'FemaleKhajiit',
    'Nord': 'FemaleNord',
    'Orc': 'FemaleOrc',
    'Redguard': 'FemaleSultry',
    'WoodElf': 'FemaleYoungEager',
}
FEMALE = 1


@dataclass
class VoiceSpeaker:
    """Who is speaking, as far as choosing a voice goes"""
    name: str = ""
    gender: int | None = None
    race: str | None = None
    voice_type: str | None = None # the voice type from the game, e.g. FemaleEvenToned
    plugin: str | None = None # where the NPC comes from, for a clip of their own
    local_id: str | None = None
    file_voice_type: str | None = None # the voice type in the character file, used when the game has no lines for theirs
    is_ghost: bool = False # for the voice effect: ghosts share their race with the living
    voice_effect: str = "" # the voice_effect column of the character file

    @staticmethod
    def from_character(character) -> "VoiceSpeaker":
        return VoiceSpeaker(
            name=getattr(character, "name", ""),
            gender=getattr(character, "gender", None),
            race=getattr(character, "race", None),
            voice_type=getattr(character, "in_game_voice_model", None) or getattr(character, "csv_in_game_voice_model", None),
            plugin=getattr(character, "plugin", None),
            local_id=getattr(character, "local_id", None),
            file_voice_type=file_value(getattr(character, "csv_in_game_voice_model", None)) or file_value(getattr(character, "tts_voice_model", None)),
            is_ghost=bool(getattr(character, "is_ghost", False)),
            voice_effect=effect_value(getattr(character, "voice_effect", "")),
        )

    def stand_ins(self) -> list[str]:
        """The voice types that stand in when theirs has no clip, in turn"""
        found = [self.file_voice_type or "", race_and_gender_voice_type(self.race, self.gender)]
        return [v for v in dict.fromkeys(found) if voice_key(v) and voice_key(v) != voice_key(self.voice_type)]


def file_value(value) -> str:
    """A character file cell, with an empty one as '' (pandas reads it as nan)"""
    text = str(value or "").strip()
    return "" if text.lower() in ("nan", "none") else text


def effect_value(value) -> str:
    """The voice_effect cell, keeping 'none' (no effect for this NPC), which file_value would read as empty"""
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


@dataclass(frozen=True)
class VoicePick:
    kind: str # NAMED, OWN_CLIP, VOICE_TYPE_CLIP, RACE_CLIP or BUILT_IN
    key: str # what the voice is called, also used to keep a prepared voice
    clip_path: str | None = None # the clip to copy the voice from; None for a voice the service has itself

    @property
    def label(self) -> str:
        return self.key


def race_name(race: str | None) -> str:
    """'<Race: NordRace (00013746)>' or 'NordRace' or 'Nord' -> 'Nord'"""
    if not race:
        return ""
    found = re.findall(r"([A-Za-z]+)Race\b", str(race))
    return found[0] if found else str(race).strip()


def race_and_gender_voice_type(race: str | None, gender: int | None) -> str:
    table = FEMALE_VOICE_TYPES if gender == FEMALE else MALE_VOICE_TYPES
    default = 'FemaleNord' if gender == FEMALE else 'MaleNord'
    return table.get(race_name(race), default)


class VoicePicker:
    def __init__(self, clips: ClipStore, built_in_voice: Callable[[int | None], str], has_voice: Callable[[str], bool] | None = None) -> None:
        """
        Args:
            built_in_voice: the service's own voice for a gender (0 male, 1 female)
            has_voice: whether the service itself has a voice of that name, for a voice named in the character file
        """
        self.__clips = clips
        self.__built_in_voice = built_in_voice
        self.__has_voice = has_voice or (lambda name: False)

    def pick(self, speaker: VoiceSpeaker | None, named_voice: str | None = None) -> VoicePick:
        speaker = speaker or VoiceSpeaker()
        if named_voice:
            clip = self.__clips.voice_type_clip(named_voice)
            if clip:
                return VoicePick(NAMED, voice_key(named_voice), clip)
            if self.__has_voice(named_voice):
                return VoicePick(NAMED, named_voice, None)
        own = self.__clips.npc_clip(speaker.plugin, speaker.local_id)
        if own:
            return VoicePick(OWN_CLIP, voice_key(f"{speaker.plugin}_{speaker.local_id}"), own)
        if speaker.voice_type:
            clip = self.__clips.voice_type_clip(speaker.voice_type)
            if clip:
                return VoicePick(VOICE_TYPE_CLIP, voice_key(speaker.voice_type), clip)
        for stand_in in speaker.stand_ins():
            clip = self.__clips.voice_type_clip(stand_in)
            if clip:
                kind = FILE_CLIP if voice_key(stand_in) == voice_key(speaker.file_voice_type) else RACE_CLIP
                return VoicePick(kind, voice_key(stand_in), clip)
        return VoicePick(BUILT_IN, self.__built_in_voice(speaker.gender), None)
