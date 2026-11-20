"""The Test buttons on the Text-to-Speech page: hear a voice service, a voice setup or a word before you play"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Callable

from src.tts.clip_service import ClipVoiceService, NoSoundSymbols, service_volume, voice_pitch
from src.tts.registry import VoiceServiceInfo, voice_services
from src.tts.voice_folders import voice_clips_folder
from src.tts.voice_picker import FEMALE_VOICE_TYPES, MALE_VOICE_TYPES, VoicePick, VoicePicker, VoiceSpeaker
from src.voice_clips.clip_store import ClipStore, shared_clip_store

TEST_LINE = "Hail, traveler. The roads have been dangerous of late, so keep your blade close."
WORD_LINES = {
    "en": "Have you seen {word}?",
    "de": "Hast du {word} gesehen?",
    "fr": "As-tu vu {word} ?",
    "es": "¿Has visto a {word}?",
    "it": "Hai visto {word}?",
    "pt": "Você viu {word}?",
    "nl": "Heb je {word} gezien?",
}


def word_line(word: str, language: str) -> str:
    return WORD_LINES.get((language or "en").lower(), WORD_LINES["en"]).format(word=word)


def voice_type_choices(save_folder: str) -> list[str]:
    """The voice types to try a voice with: the usual ones, plus every voice type that has a clip"""
    names = sorted(set(MALE_VOICE_TYPES.values()) | set(FEMALE_VOICE_TYPES.values()))
    clips = ClipStore(voice_clips_folder(save_folder))
    known = {n.lower() for n in names}
    for key in clips.voice_type_keys():
        if key not in known:
            names.append(key)
    return names


def as_in_game(config, service_name: str, voice_type: str) -> tuple[float, float]:
    """The service's Volume and the voice type's Pitch, as a line in game gets them"""
    info = voice_services.find(service_name)
    volume = service_volume(config, info) if info is not None else 100.0
    clips = shared_clip_store(voice_clips_folder(config.save_folder))
    gender = 1 if voice_type.lower().startswith("female") else 0
    pick = VoicePicker(clips, lambda g: "", lambda name: False).pick(VoiceSpeaker(gender=gender, voice_type=voice_type))
    return volume, voice_pitch(clips, pick)


def speak_with_clip(config, chat_manager_getter: Callable[[], Any], service_name: str, pick: VoicePick, text: str = TEST_LINE) -> str:
    """Hear it speak, under Voice Clips: a line in the voice a service makes from one clip, using the running service when Mantella has started it"""
    chat_manager = chat_manager_getter()
    if chat_manager is not None:
        return chat_manager.speak_clip_test_line(service_name, pick, text)
    info = voice_services.find(service_name)
    if info is None:
        raise ValueError(f"There is no voice service called '{service_name}'.")
    if info.is_legacy:
        raise ValueError(doesnt_copy_voices(service_name))
    service = voice_services.create(info, config, None)
    try:
        return voice_from_clip(service, service_name, pick, text)
    finally:
        stop(service)


def voice_from_clip(service: Any, service_name: str, pick: VoicePick, text: str) -> str:
    if not isinstance(service, ClipVoiceService):
        raise ValueError(doesnt_copy_voices(service_name))
    if not getattr(service, "can_copy_voices", True) and getattr(service, "why_not", ""):
        raise ValueError(service.why_not) # an online service: no key, or it won't clone another voice right now
    if not getattr(service, "can_copy_voices", True):
        raise ValueError(f"{service_name} can't clone voices right now, so it would only use its built-in voices. See {service_name} on this page.")
    out_path = os.path.join(tempfile.gettempdir(), "voicelines", "test_clip.wav")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    service.voice_into(text, pick, out_path)
    if not getattr(service, "can_copy_voices", True) and getattr(service, "why_not", ""):
        # Refused just now: the line was spoken in a built-in voice, not the cloned one this button promises
        raise ValueError(service.why_not)
    service.finish_line(out_path, pitch=service.pitch_for(pick))
    return out_path


def doesnt_copy_voices(service_name: str) -> str:
    return (f"{service_name} doesn't clone voices from clips. To hear a clip speak, pick Pocket TTS, ElevenLabs, Inworld or one "
            "of your voice setups as the TTS Service.")


def speak_with_setup(config, info: VoiceServiceInfo, voice_type: str, text: str = TEST_LINE) -> str:
    """Speaks a line with a setup that may not be saved yet. Returns the sound file"""
    service = voice_services.create(info, config, None)
    try:
        if not isinstance(service, ClipVoiceService):
            raise ValueError("Only voice setups can be tried before they are saved.")
        check = getattr(service, "check", None)
        if check is not None:
            answers, message = check()
            if not answers:
                raise ConnectionError(message)
        out_path = os.path.join(tempfile.gettempdir(), "voicelines", "test_setup.wav")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        gender = 1 if voice_type.lower().startswith("female") else 0
        pick = service.pick_voice(VoiceSpeaker(gender=gender, voice_type=voice_type))
        service.voice_into(text, pick, out_path)
        service.finish_line(out_path, volume=service_volume(config, info), pitch=service.pitch_for(pick)) # the setup's Volume as typed, saved or not
        return out_path
    finally:
        stop(service)


def speak_with_service(config, chat_manager_getter: Callable[[], Any], service_name: str, voice_type: str, text: str = TEST_LINE,
                       use_word_lists: bool = True, as_heard: bool = True, ipa_word: tuple[str, str] | None = None) -> str:
    """Speaks a line with a voice service from the list, using the running one when Mantella has started it.
    *as_heard*: with the voice's Pitch and the service's Volume, as in game (the newer services). False for a line that gets them afterwards.
    *ipa_word*: (word, sound symbols) to say that word by, for Hear it fixed. Raises NoSoundSymbols when the voice doesn't read them"""
    chat_manager = chat_manager_getter()
    if chat_manager is not None:
        return chat_manager.speak_test_line(service_name, voice_type, text, use_word_lists, as_heard, ipa_word)
    info = voice_services.find(service_name)
    if info is None:
        raise ValueError(f"There is no voice service called '{service_name}'.")
    if info.is_legacy:
        raise RuntimeError("Mantella is still starting. Try again in a moment.")
    service = voice_services.create(info, config, None)
    try:
        out_path = os.path.join(tempfile.gettempdir(), "voicelines", "test_line.wav")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        gender = 1 if voice_type.lower().startswith("female") else 0
        pick = service.pick_voice(VoiceSpeaker(gender=gender, voice_type=voice_type))
        if ipa_word is not None and not isinstance(service, ClipVoiceService):
            raise NoSoundSymbols(f"{service_name} doesn't take sound symbols.")
        service.voice_into(text, pick, out_path, use_word_lists, ipa_word)
        if as_heard:
            service.finish_line(out_path, pitch=service.pitch_for(pick))
        return out_path
    finally:
        stop(service)


def stop(service: Any) -> None:
    """A service made for one Test press is stopped after it, so Pocket TTS's program doesn't stay loaded for nothing"""
    if hasattr(service, "stop"):
        service.stop()
