"""The shared clean-up for new voice services: turn whatever sound a service sends back into the kind of file the game plays
(mono, 16-bit WAV), and cut the silence at the start and end of the line, keeping a few hundredths of a second so the first sound isn't clipped.
Then the voice's Talking speed (voice_speed.py).
"""
from typing import BinaryIO

import av
import numpy as np
import soundfile as sf

from src.tts.voice_speed import SPEED_CHANGE, SpeedChange
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VOICES)

KEEP_SECONDS = 0.03 # silence kept before the first and after the last sound
WINDOW_SECONDS = 0.01
QUIETEST_LOUD_PART = 0.005 # below this a window counts as silence, however quiet the line is
SHARE_OF_PEAK = 0.03 # ...and below this share of the line's loudest moment


def decode(source: str | BinaryIO) -> tuple[np.ndarray, int]:
    """Reads any sound file (wav, mp3, ogg, flac, xWMA, ...) as mono samples between -1 and 1, at its own sample rate"""
    with av.open(source) as container:
        stream = container.streams.audio[0]
        rate = stream.codec_context.sample_rate or stream.rate
        resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
        chunks: list[np.ndarray] = []
        for frame in container.decode(stream):
            for converted in resampler.resample(frame):
                chunks.append(converted.to_ndarray().reshape(-1))
        for converted in resampler.resample(None):
            chunks.append(converted.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32), rate
    return np.concatenate(chunks).astype(np.float32), rate


def trim_silence(samples: np.ndarray, rate: int) -> np.ndarray:
    """Cuts the silence at the start and end, measured instead of a fixed amount"""
    if samples.size == 0:
        return samples
    window = max(1, int(rate * WINDOW_SECONDS))
    count = samples.size // window
    if count == 0:
        return samples
    loudness = np.abs(samples[:count * window]).reshape(count, window).max(axis=1)
    threshold = max(QUIETEST_LOUD_PART, float(loudness.max()) * SHARE_OF_PEAK)
    loud = np.nonzero(loudness > threshold)[0]
    if loud.size == 0:
        return samples # all quiet: leave it as it is
    keep = int(rate * KEEP_SECONDS)
    start = max(0, loud[0] * window - keep)
    end = min(samples.size, (loud[-1] + 1) * window + keep)
    return samples[start:end]


def clean_up(source: str, destination: str, trim: bool = True, speed: float = 1.0, speed_change: SpeedChange = SPEED_CHANGE) -> None:
    """Writes *source* to *destination* as the sound file the game plays, with the silence at both ends cut, at *speed* (Talking speed)"""
    samples, rate = decode(source)
    if samples.size == 0:
        raise ValueError(f"The voice service sent back no sound: {source}")
    before = samples.size
    if trim:
        samples = trim_silence(samples, rate)
    samples = speed_change.apply(samples, rate, speed)
    log.debug(f"Voice clean-up: {before / rate:.2f}s -> {samples.size / rate:.2f}s at {rate} Hz" + (f", Talking speed {speed:g}" if speed != 1.0 else ""))
    sf.write(destination, np.clip(samples, -1.0, 1.0), rate, subtype="PCM_16")


def write(samples: np.ndarray, rate: int, destination: str) -> None:
    """Writes samples as the plain sound file voice clips are kept in, with the silence at both ends cut"""
    if samples.size == 0:
        raise ValueError("The sound is empty")
    sf.write(destination, np.clip(trim_silence(samples, rate), -1.0, 1.0), rate, subtype="PCM_16")
