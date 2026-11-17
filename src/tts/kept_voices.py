"""What the Voice Clips page's buttons do to the voices every service keeps: Pocket TTS's saved voices on this PC, and the voices
ElevenLabs and Inworld cloned on the player's account. Each button acts on all of them, since the player may have tried a clip with one
service and play with another"""
from __future__ import annotations

import logging
from typing import Any


def _keepers() -> list[Any]:
    from src.tts import pocket_tts
    from src.tts.elevenlabs import ElevenLabs
    from src.tts.inworld import Inworld
    return [pocket_tts, ElevenLabs.keeper(), Inworld.keeper()]


def _each(action: str, *args, **kwargs) -> None:
    for keeper in _keepers():
        try:
            getattr(keeper, action)(*args, **kwargs)
        except Exception as e:
            logging.warning(f"Could not tidy up the voices kept for {getattr(keeper, 'service_name', 'Pocket TTS')}: {e}")


def use_trial_voice(save_folder: str, key: str, clip_path: str) -> None:
    """Clone from this clip: the voice made to hear it becomes the voice NPCs use"""
    _each("use_trial_voice", save_folder, key, clip_path)


def move_saved_voice(save_folder: str, from_key: str, to_key: str, clip_path: str) -> None:
    """An NPC's own voice given to their whole voice type"""
    _each("move_saved_voice", save_folder, from_key, to_key, clip_path)


def remove_saved_voices(save_folder: str, key: str) -> None:
    """A voice that is gone"""
    _each("remove_saved_voices", save_folder, key)


def remove_trial_voices(save_folder: str, only: str | None = None, keep: str | None = None) -> None:
    """The voices made to hear clips from the list"""
    _each("remove_trial_voices", save_folder, only=only, keep=keep)
