"""The Test buttons on the Text-to-Speech page: hear a voice service, a voice setup or a word before you play"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Callable

from src.tts.clip_service import ClipVoiceService
from src.tts.registry import VoiceServiceInfo, voice_services
from src.tts.voice_folders import voice_clips_folder
from src.tts.voice_picker import FEMALE_VOICE_TYPES, MALE_VOICE_TYPES, VoicePick, VoiceSpeaker
from src.voice_clips.clip_store import ClipStore

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
    return voice_from_clip(voice_services.create(info, config, None), service_name, pick, text)


def voice_from_clip(service: Any, service_name: str, pick: VoicePick, text: str) -> str:
    if not isinstance(service, ClipVoiceService):
        raise ValueError(doesnt_copy_voices(service_name))
    if not getattr(service, "can_copy_voices", True):
        raise ValueError(f"{service_name} can't copy voices right now, so it would only use its built-in voices. See {service_name} on this page.")
    out_path = os.path.join(tempfile.gettempdir(), "voicelines", "test_clip.wav")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    service.voice_into(text, pick, out_path)
    return out_path


def doesnt_copy_voices(service_name: str) -> str:
    return (f"{service_name} doesn't copy voices from clips. To hear a clip speak, pick Pocket TTS or one of your voice setups "
            "as the TTS Service.")


def speak_with_setup(config, info: VoiceServiceInfo, voice_type: str, text: str = TEST_LINE) -> str:
    """Speaks a line with a setup that may not be saved yet. Returns the sound file"""
    service = voice_services.create(info, config, None)
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
    service.voice_into(text, service.pick_voice(VoiceSpeaker(gender=gender, voice_type=voice_type)), out_path)
    return out_path


def speak_with_service(config, chat_manager_getter: Callable[[], Any], service_name: str, voice_type: str, text: str = TEST_LINE,
                       use_word_lists: bool = True) -> str:
    """Speaks a line with a voice service from the list, using the running one when Mantella has started it"""
    chat_manager = chat_manager_getter()
    if chat_manager is not None:
        return chat_manager.speak_test_line(service_name, voice_type, text, use_word_lists)
    info = voice_services.find(service_name)
    if info is None:
        raise ValueError(f"There is no voice service called '{service_name}'.")
    if info.is_legacy:
        raise RuntimeError("Mantella is still starting. Try again in a moment.")
    service = voice_services.create(info, config, None)
    out_path = os.path.join(tempfile.gettempdir(), "voicelines", "test_line.wav")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    gender = 1 if voice_type.lower().startswith("female") else 0
    service.voice_into(text, service.pick_voice(VoiceSpeaker(gender=gender, voice_type=voice_type)), out_path, use_word_lists)
    return out_path
