"""The words spoken in a voice clip, for a service that clones a voice only together with them (Boson AI), or better with them (Iodine).

Each clip is heard once with the option picked under Speech-to-Text, also when the player types instead of talking, and its words are kept
in voice_clips/clip_words.json by the clip's sound, so a clip is heard again only when its sound changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading

import numpy as np

from src.tts.voice_folders import voice_clips_folder

FILE = "clip_words.json"
_lock = threading.Lock()


def _sound_hash(path: str) -> str:
    with open(path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()[:20]


def _read(path: str) -> dict[str, str]:
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logging.warning(f"Could not read {path}, so the clips' words are heard again: {e}")
        return {}


def _write(path: str, data: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
    os.replace(temp, path)


def _as_heard(path: str) -> np.ndarray:
    """The clip as speech-to-text takes it: 16 kHz mono"""
    from scipy.signal import resample_poly

    from src.stt_backends.base import SAMPLING_RATE
    from src.tts.audio_cleanup import decode
    samples, rate = decode(path)
    if rate != SAMPLING_RATE:
        samples = resample_poly(samples, SAMPLING_RATE, rate)
    return samples.astype(np.float32)


def _hear(clip_paths: list[str], config) -> list[str]:
    """One load of the chosen speech-to-text option for all of them, let go again afterwards. A clip with no words heard gets an empty text"""
    from src.stt_backends import build_backend, chosen
    choice = chosen(config)
    backend = build_backend(choice, config)
    language = getattr(config, "stt_language", "") or getattr(config, "language", "")
    backend.load()
    try:
        return [(backend.transcribe(_as_heard(path), "", [], language) or "").strip() for path in clip_paths]
    finally:
        backend.close()


def words_for(clip_paths: list[str], config) -> list[str]:
    """The words of each clip, in order: "" for a clip with no words heard (a shout, a grunt), which isn't heard again.
    Raises when speech-to-text doesn't work at all"""
    path = os.path.join(voice_clips_folder(config.save_folder), FILE)
    hashes = [_sound_hash(clip) for clip in clip_paths]
    with _lock:
        known = _read(path)
    missing = [(clip, sound) for clip, sound in zip(clip_paths, hashes) if sound not in known]
    if missing:
        heard = _hear([clip for clip, _ in missing], config)
        with _lock:
            known = _read(path)
            known.update({sound: words for (_, sound), words in zip(missing, heard)})
            _write(path, known)
    return [known[sound] for sound in hashes]
