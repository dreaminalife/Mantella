"""Parakeet on this PC (the onnx-asr program): fast on a normal processor, 25 European languages, works out the language itself. It can't take names"""
from __future__ import annotations


import numpy as np

from src.stt_backends.base import FAILED, NOT_DOWNLOADED, SAMPLING_RATE, SpeechBackend, SpeechError
from src.stt_backends.cleanup import clean, ensure_sentence_ending
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.MICROPHONE)

QUANTIZATION = "int8" # Parakeet's int8 copy is about 670 MB instead of 2.5 GB, with nearly the same accuracy


class OnnxAsrProgram(SpeechBackend):
    takes_hint = False

    def __init__(self, model: str) -> None:
        self.model = (model or "").strip()
        self._asr = None
        self._load_error: SpeechError | None = None

    def load(self) -> None:
        if self._load_error is not None: # failed before: not again on every line (slow, and the error each time)
            raise self._load_error
        log.debug( f"Loading {self.model} (the first time, it downloads about 670 MB)...")
        try:
            import onnx_asr
            from onnx_asr.utils import ModelFileNotFoundError
            try:
                self._asr = onnx_asr.load_model(self.model, quantization=QUANTIZATION)
            except ModelFileNotFoundError: # a model typed in that has no int8 copy (not a failed download, which would fetch the full 2.5 GB)
                self._asr = onnx_asr.load_model(self.model)
        except Exception as e:
            self._load_error = SpeechError(NOT_DOWNLOADED, f"Could not load the Parakeet model '{self.model}': {e}. "
                                           "Check the Model setting under Parakeet on the Speech-to-Text tab, and that you are online the first time "
                                           "a model is used, so it can download. Press Test on the Speech-to-Text tab to try again.")
            raise self._load_error from e

    def forget_failed_load(self) -> None:
        self._load_error = None

    def transcribe(self, audio: np.ndarray, hint: str, names: list[str], language: str) -> str:
        if self._asr is None:
            self.load()
        if len(audio) == 0:
            return ""
        try:
            text = self._asr.recognize(np.asarray(audio, dtype=np.float32), sample_rate=SAMPLING_RATE)
        except Exception as e:
            raise SpeechError(FAILED, f"Parakeet could not turn the recording into text: {e}") from e
        return ensure_sentence_ending(clean(text))

    def close(self) -> None:
        self._asr = None
