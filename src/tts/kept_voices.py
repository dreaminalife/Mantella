"""What the Voice Clips page's buttons do to the voices every service keeps: Pocket TTS's saved voices on this PC, and the voices
the online services cloned on the player's account. Each button acts on all of them, since the player may have heard a voice with one
service and play with another"""
from __future__ import annotations

import logging
from typing import Any


def _keepers() -> list[Any]:
    from src.tts import pocket_tts
    from src.tts.boson import BosonAI
    from src.tts.elevenlabs import ElevenLabs
    from src.tts.heygen import HeyGen
    from src.tts.inworld import Inworld
    from src.tts.iodine import Iodine
    from src.tts.qwen import Qwen
    return [pocket_tts, ElevenLabs.keeper(), Inworld.keeper(), HeyGen.keeper(), Qwen.keeper(), BosonAI.keeper(), Iodine.keeper()]


def _each(action: str, *args, **kwargs) -> None:
    for keeper in _keepers():
        try:
            getattr(keeper, action)(*args, **kwargs)
        except Exception as e:
            logging.warning(f"Could not tidy up the voices kept for {getattr(keeper, 'service_name', 'Pocket TTS')}: {e}")


def move_saved_voice(save_folder: str, from_key: str, to_key: str, clip_path: str) -> None:
    """An NPC's own voice given to their whole voice type"""
    _each("move_saved_voice", save_folder, from_key, to_key, clip_path)


def remove_saved_voices(save_folder: str, key: str) -> None:
    """A voice that is gone"""
    _each("remove_saved_voices", save_folder, key)
