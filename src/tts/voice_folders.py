"""Where the voice services keep their files: next to config.ini (Documents\\My Games\\Mantella), so they survive reinstalls"""
import os

VOICE_SETUPS_FILE = "voice_setups.json"
PRONUNCIATION_FOLDER = "pronunciation"
VOICE_CLIPS_FOLDER = "voice_clips"


def voice_setups_file(save_folder: str) -> str:
    return os.path.join(save_folder, VOICE_SETUPS_FILE)


def pronunciation_folder(save_folder: str) -> str:
    return os.path.join(save_folder, PRONUNCIATION_FOLDER)


def voice_clips_folder(save_folder: str) -> str:
    return os.path.join(save_folder, VOICE_CLIPS_FOLDER)
