import av
import numpy as np
import pytest
import soundfile as sf

from src.tts.audio_cleanup import KEEP_SECONDS, clean_up, trim_silence

RATE = 24000


def line(silence_before: float, silence_after: float, seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return np.concatenate([np.zeros(int(RATE * silence_before)), 0.4 * np.sin(2 * np.pi * 200 * t), np.zeros(int(RATE * silence_after))]).astype(np.float32)


def test_the_silence_at_both_ends_is_cut_and_a_little_kept():
    trimmed = trim_silence(line(0.5, 0.3), RATE)
    assert abs(len(trimmed) / RATE - (1.0 + 2 * KEEP_SECONDS)) < 0.011


def test_a_quiet_line_is_left_as_it_is():
    quiet = np.zeros(RATE, dtype=np.float32)
    assert len(trim_silence(quiet, RATE)) == RATE


def test_stereo_float_becomes_mono_16_bit(tmp_path):
    samples = line(0.2, 0.2)
    sf.write(tmp_path / "in.wav", np.stack([samples, samples], axis=1), RATE, subtype="FLOAT")
    clean_up(str(tmp_path / "in.wav"), str(tmp_path / "out.wav"))
    info = sf.info(str(tmp_path / "out.wav"))
    assert info.channels == 1 and info.subtype == "PCM_16" and info.samplerate == RATE


def test_mp3_from_an_online_service_is_read(tmp_path):
    samples = line(0.2, 0.2)
    with av.open(str(tmp_path / "in.mp3"), "w") as container:
        stream = container.add_stream("mp3", rate=RATE)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="flt", layout="mono")
        frame.sample_rate = RATE
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    clean_up(str(tmp_path / "in.mp3"), str(tmp_path / "out.wav"))
    data, rate = sf.read(str(tmp_path / "out.wav"))
    assert rate == RATE and 0.95 < len(data) / rate < 1.2


def test_no_sound_is_an_error(tmp_path):
    sf.write(tmp_path / "empty.wav", np.zeros(0, dtype=np.float32), RATE)
    with pytest.raises(Exception):
        clean_up(str(tmp_path / "empty.wav"), str(tmp_path / "out.wav"))


# ---- Talking speed (Step 13) ----

def peak_hz(samples: np.ndarray, rate: int) -> float:
    return float(np.argmax(np.abs(np.fft.rfft(samples)))) * rate / samples.size


def tone(seconds: float = 2.0, hz: float = 220.0) -> np.ndarray:
    return (0.4 * np.sin(2 * np.pi * hz * np.arange(int(RATE * seconds)) / RATE)).astype(np.float32)


def test_talking_speed_changes_the_length_but_not_the_pitch(tmp_path):
    sf.write(tmp_path / "in.wav", tone(), RATE)
    clean_up(str(tmp_path / "in.wav"), str(tmp_path / "out.wav"), trim=False, speed=1.25)
    data, rate = sf.read(str(tmp_path / "out.wav"))
    assert abs(len(data) / rate - 2.0 / 1.25) < 0.05
    assert abs(peak_hz(data, rate) - 220.0) < 3


def test_talking_speed_1_leaves_the_line_untouched():
    from src.tts.voice_speed import SPEED_CHANGE
    samples = tone()
    assert SPEED_CHANGE.apply(samples, RATE, 1.0) is samples


def test_a_failed_speed_change_keeps_the_line(tmp_path):
    from src.tts.voice_speed import SpeedChange

    class Broken(SpeedChange):
        def changed(self, samples, rate, speed):
            raise RuntimeError("no filter")

    sf.write(tmp_path / "in.wav", tone(), RATE)
    clean_up(str(tmp_path / "in.wav"), str(tmp_path / "out.wav"), trim=False, speed=0.8, speed_change=Broken())
    data, rate = sf.read(str(tmp_path / "out.wav"))
    assert abs(len(data) / rate - 2.0) < 0.01
