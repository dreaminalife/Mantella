"""Reading the game's voice files: .fuz (a lip file and an xWMA sound packed together), .xwm and .wav"""
import io
import struct

import numpy as np

from src.tts import audio_cleanup

FUZ_MAGIC = b"FUZE"


def sound_bytes(data: bytes) -> bytes:
    """The sound inside a game voice file: for a .fuz, the part after its lip data; anything else as it is"""
    if data[:4] == FUZ_MAGIC:
        if len(data) < 12:
            raise ValueError("The .fuz file is cut short")
        lip_size = struct.unpack("<I", data[8:12])[0]
        return data[12 + lip_size:]
    return data


def decode(data: bytes) -> tuple[np.ndarray, int]:
    """A game voice file as mono samples between -1 and 1, at its own sample rate"""
    return audio_cleanup.decode(io.BytesIO(sound_bytes(data)))


def to_wav(source: str, destination: str) -> None:
    """Writes a game voice file (.fuz, .xwm or .wav) as a plain sound file, with the silence at both ends cut"""
    with open(source, "rb") as file:
        samples, rate = decode(file.read())
    audio_cleanup.write(samples, rate, destination)
