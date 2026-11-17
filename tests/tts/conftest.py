from types import SimpleNamespace

import pytest

from src.config.definitions.game_definitions import GameEnum


@pytest.fixture
def voice_config(tmp_path):
    """The settings a voice service reads, with its files in a folder of its own (no config.ini is read or written)"""
    return SimpleNamespace(
        lipgen_path="", facefx_path="", tts_print=False, save_folder=str(tmp_path), language="en", lip_generation="disabled",
        game=GameEnum.SKYRIM, tts_service=None, tts_service_name="", tts_backup_service_name="Piper", narration_handling=None, narrator_voice="",
    )
