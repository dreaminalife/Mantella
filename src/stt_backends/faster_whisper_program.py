"""Whisper on this PC (the faster-whisper program). It runs any Whisper model saved in its format: the usual sizes, or a Hugging Face id"""
from __future__ import annotations

import logging

import numpy as np

from src import utils
from src.stt_backends.base import FAILED, NOT_DOWNLOADED, SpeechBackend, SpeechError
from src.stt_backends.cleanup import clean

# Names newer faster-whisper versions know but Mantella's (1.0.3) doesn't: asked for by their ready-made copy's id instead
MODEL_IDS = {
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}


def model_id(model: str) -> str:
    return MODEL_IDS.get((model or "").strip(), (model or "").strip())


class FasterWhisperProgram(SpeechBackend):
    takes_hint = True

    def __init__(self, model: str, device: str = "cpu", translate: bool = False) -> None:
        self.model = model
        self.device = device or "cpu"
        self.task = "translate" if translate else "transcribe"
        self._whisper = None
        self._load_error: SpeechError | None = None

    def load(self) -> None:
        if self._load_error is not None: # failed before: not again on every line (slow, and the error sound each time)
            raise self._load_error
        from faster_whisper import WhisperModel
        if self.device == 'cuda':
            logging.error('''Depending on your NVIDIA CUDA version, setting the Whisper process device to `cuda` may cause errors! For more information, see here: https://github.com/SYSTRAN/faster-whisper#gpu''')
        try:
            if self.device == 'cuda':
                self._whisper = WhisperModel(model_id(self.model), device=self.device)
            else:
                self._whisper = WhisperModel(model_id(self.model), device=self.device, compute_type="float32")
        except Exception as e:
            utils.play_error_sound()
            self._load_error = SpeechError(NOT_DOWNLOADED, f"Could not load the Whisper model '{self.model}': {e}. Check the Model setting under Whisper "
                                           "on the Speech-to-Text tab, and that you are online the first time a model is used, so it can download. "
                                           "Press Test on the Speech-to-Text tab to try again.")
            raise self._load_error from e

    def forget_failed_load(self) -> None:
        self._load_error = None

    def transcribe(self, audio: np.ndarray, hint: str, names: list[str], language: str) -> str:
        if self._whisper is None:
            self.load()
        try:
            segments, _ = self._whisper.transcribe(audio, task=self.task, language=language, beam_size=5, vad_filter=False, initial_prompt=hint or None)
            return clean(' '.join(segment.text for segment in segments))
        except Exception as e:
            raise SpeechError(FAILED, f"Whisper could not turn the recording into text: {e}") from e

    def close(self) -> None:
        self._whisper = None
