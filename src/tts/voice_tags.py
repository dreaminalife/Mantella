"""Voice tags: what the AI may write in square brackets inside a spoken line, for voice services that understand it.

- Audio tags: a sound where it happens, such as [laugh] or [sigh].
- Moods: one of a service's own short list, such as [angry] or [scared] (HeyGen and Boson AI, with their Audio tags on).
- Directions: how the words after it are said, such as [say angrily] or [quietly, trying not to laugh]. A mood or direction changes
  only the sentence it is in, since each sentence is its own request.
- Word styles: a few words wrapped in a style and closed, such as [whisper]just between us[/whisper] (Iodine, with its Audio tags on).

A service takes tags by giving its VoiceServiceInfo a voice_tags function, which says what it accepts with the current settings
(Inworld first). The prompt blocks {if audio_tags} and {if directions} unlock when an NPC in the conversation speaks with such a
service, {if moods} when it has moods, and {if word_styles} when it has word styles. Tags are only in what that service is sent: never in subtitles, what NPCs remember,
lip files or another service's line.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from src.character_manager import Character

AUDIO_TAGS = "audio_tags" # the prompt conditions
MOODS = "moods"
DIRECTIONS = "directions"
WORD_STYLES = "word_styles"
RESET = "reset" # not offered to the AI, but passed on when it writes one, so it is never taken for a direction

# Sounds a voice service may know. Text in brackets that is one of these is never taken for a direction, also when the speaker's
# service doesn't make that sound: it is left out instead
SOUND_WORDS = frozenset({
    "breathe", "sigh", "gasp", "pant", "huff", "grunt", "groan", "moan",
    "laugh", "chuckle", "giggle", "cackle", "snort", "scoff",
    "cry", "sob", "wail", "whimper", "whine", "sniffle", "sniff", "shriek", "squeal", "howl",
    "clear throat", "cough", "sneeze", "hiccup", "yawn", "burp", "snore", "choke", "gag", "swallow", "gulp", "spit",
    "tongue click", "mouth click", "mouth sound", "lip smack", "kiss", "shush", "raspberry", "whistle", "bleh",
    "chew", "slurp", "babble", "beatbox", "growl", "shaky breath", "pause", "long pause",
})

TAG_RE = re.compile(r"\[([^\[\]]*)\]")


@dataclass(frozen=True)
class VoiceTags:
    """What a voice service takes right now"""
    sounds: tuple[str, ...] = () # the sounds offered to the AI, in the order the prompt lists them. Empty: no audio tags
    directions: bool = False
    moods: tuple[str, ...] = () # the moods offered to the AI, such as "angry" (HeyGen, Boson AI, Iodine). Empty: none
    styles: tuple[str, ...] = () # the word styles offered to the AI, such as "whisper" for [whisper]...[/whisper] (Iodine). Empty: none

    @property
    def any(self) -> bool:
        return bool(self.sounds) or self.directions or bool(self.moods) or bool(self.styles)


NO_TAGS = VoiceTags()


def tag_key(text: str) -> str:
    """How tags are compared: case, spaces, dashes, underscores and end punctuation don't count ([Clear_Throat] is [clear throat])"""
    return re.sub(r"[\s_\-]+", " ", text.strip().lower()).strip(" .,!?;:")


def tags_for(config, character: "Character | None") -> VoiceTags:
    """What this character's voice service takes: their own service from the character file, otherwise the TTS Service"""
    from src.tts.registry import voice_services
    service = voice_services.find(getattr(character, "tts_service", None)) if character is not None else None
    service = service or voice_services.find(getattr(config, "tts_service_name", None))
    if service is None or service.voice_tags is None:
        return NO_TAGS
    try:
        return service.voice_tags(config)
    except Exception:
        return NO_TAGS


def _npcs(characters: Iterable["Character"]) -> list["Character"]:
    return [c for c in characters if not c.is_player_character]


def conditions_for(config, characters: Iterable["Character"]) -> set[str]:
    """The prompt conditions that are true for these NPCs: audio_tags when one of them can make sounds, moods when one can change
    their mood, directions when one can take directions, word_styles when one can say a few words in a style"""
    conditions: set[str] = set()
    for character in _npcs(characters):
        tags = tags_for(config, character)
        if tags.sounds:
            conditions.add(AUDIO_TAGS)
        if tags.moods:
            conditions.add(MOODS)
        if tags.directions:
            conditions.add(DIRECTIONS)
        if tags.styles:
            conditions.add(WORD_STYLES)
    return conditions


def _tags_text(config, characters: Iterable["Character"], kind: str) -> str:
    words: list[str] = []
    for character in _npcs(characters):
        for word in getattr(tags_for(config, character), kind):
            if word not in words:
                words.append(word)
    return ", ".join(f"[{word}]" for word in words)


def sounds_text(config, characters: Iterable["Character"]) -> str:
    """The sounds these NPCs' voices can make, for {voice_sounds}: "[laugh], [sigh], ..." """
    return _tags_text(config, characters, "sounds")


def moods_text(config, characters: Iterable["Character"]) -> str:
    """The moods these NPCs' voices can take, for {voice_moods}: "[angry], [sad], ..." """
    return _tags_text(config, characters, "moods")


def styles_text(config, characters: Iterable["Character"]) -> str:
    """The word styles these NPCs' voices can take, for {voice_styles}: "[whisper], [soft], ..." """
    return _tags_text(config, characters, "styles")


def tidy(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r" +([,.!?;:])", r"\1", text).strip()


def split(text: str, tags: VoiceTags) -> tuple[str, str]:
    """One spoken sentence, as written by the AI

    Args:
        tags (VoiceTags): what the speaker's voice service takes

    Returns:
        tuple[str, str]: the sentence without any tags (subtitles, memory, lip files), and the sentence as the voice service gets it,
            with only the tags it takes. Each sentence is its own request, so a direction changes only the sentence it is in.
            A word style's closing tag ([/whisper]) is kept only for a service that takes that style
    """
    if "[" not in text:
        return text, text
    clean = tidy(TAG_RE.sub(" ", text))
    if not tags.any:
        return clean, clean
    sounds = {tag_key(sound) for sound in tags.sounds}
    moods = {tag_key(mood) for mood in tags.moods}
    styles = {tag_key(style) for style in tags.styles}
    parts: list[str] = []
    position = 0
    for match in TAG_RE.finditer(text):
        parts.append(text[position:match.start()])
        position = match.end()
        written = match.group(1).strip()
        key = tag_key(written)
        if not key:
            continue
        if key == RESET:
            parts.append("[reset]")
        elif key.startswith("/"):
            if key[1:].strip() in styles:
                parts.append(f"[/{key[1:].strip()}]")
        elif key in styles:
            parts.append(f"[{written}]")
        elif key in SOUND_WORDS or key in sounds:
            if key in sounds:
                parts.append(f"[{written}]")
        elif key in moods or tags.directions:
            parts.append(f"[{written}]")
    parts.append(text[position:])
    return clean, tidy("".join(parts))


def rewrite_tags(text: str, sound_names: dict[str, str]) -> tuple[str, str]:
    """A line as split() gives it, for a service that writes sounds its own way (HeyGen, Qwen, Boson AI)

    Args:
        sound_names (dict[str, str]): Mantella's word for each sound the service makes -> how the service writes it, e.g. "laugh" -> "[laughs]"

    Returns:
        tuple[str, str]: the line with each sound written the service's way and every other bracket taken out ([reset] too: none of
            these services knows it), and the first direction in it, for a service that takes a direction outside the text (Qwen).
            Where two tags sit side by side, only the first is kept
    """
    if "[" not in text:
        return text, ""
    names = {tag_key(word): written for word, written in sound_names.items()}
    parts: list[str] = []
    position = 0
    direction = ""
    tag_before = False # the last thing written is a sound, with only spaces after it
    for match in TAG_RE.finditer(text):
        between = text[position:match.start()]
        if between.strip():
            tag_before = False
        parts.append(between)
        position = match.end()
        written = match.group(1).strip()
        key = tag_key(written)
        if not key or key == RESET:
            continue
        if key in names:
            if not tag_before:
                parts.append(names[key])
                tag_before = True
        elif key not in SOUND_WORDS and not direction:
            direction = written
    parts.append(text[position:])
    return tidy("".join(parts)), direction


class ServiceCues(ABC):
    """How one voice service wants the tags of a line written, for a service that writes them its own way (Iodine)"""

    @abstractmethod
    def text(self, line: str) -> str:
        """A line as split() gives it, with each tag written the service's way and every tag it doesn't take removed"""
