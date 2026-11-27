"""The Voice service list in the Bio Editor: one NPC's tts_service column, saved in their character override"""
from __future__ import annotations

from src.tts.registry import voice_services

SERVICE_COLUMN = "tts_service"
DEFAULT = "" # the TTS Service on the Text-to-Speech tab


def service_choices(current: str = "") -> list[tuple[str, str]]:
    """The TTS Service setting, then every voice service and voice setup by the name shown in the TTS Service list.
    A name written by hand that no service has is kept, so Save doesn't lose it"""
    choices = [("Default (the TTS Service on the Text-to-Speech tab)", DEFAULT)]
    choices += [(name, name) for name in voice_services.names()]
    current = (current or "").strip()
    if current and voice_services.find(current) is None and current.lower() not in {value.lower() for _, value in choices}:
        choices.append((f"{current} (no voice service by that name)", current))
    return choices


def picked_service(written: str) -> str:
    """The list's value for what the column says: 'xva' and 'xvasynth' both pick xVASynth"""
    written = (written or "").strip()
    if not written:
        return DEFAULT
    found = voice_services.find(written)
    return found.name if found else written
