"""Moonshine on this PC: small and fast on a normal processor, English only. It can't take names"""
from __future__ import annotations

import logging
import os

import numpy as np

from src.stt_backends.base import FAILED, NOT_DOWNLOADED, SpeechBackend, SpeechError
from src.stt_backends.cleanup import clean, ensure_sentence_ending

LOGLEVEL = 27


class MoonshineProgram(SpeechBackend):
    takes_hint = False

    def __init__(self, full_model: str, folder: str = "") -> None:
        self.full_model = full_model # e.g. moonshine/tiny/quantized
        self.model, self.precision = full_model.rsplit('/', 1)
        self.folder = folder or ""
        self._moonshine = None
        self._tokenizer = None
        self._load_error: SpeechError | None = None

    def load(self) -> None:
        if self._load_error is not None: # failed before: not again on every line (slow, and the error sound each time)
            raise self._load_error
        from moonshine_onnx import MoonshineOnnxModel, load_tokenizer
        if self.model == 'moonshine/tiny':
            logging.warning('Speech-to-text model set to Moonshine Tiny. If mic input is being transcribed incorrectly, try switching to a larger model in the `Speech-to-Text` tab of the Mantella UI')
        model_path = os.path.join(self.folder, self.full_model)
        try:
            if os.path.exists(f'{model_path}/encoder_model.onnx'):
                logging.log(LOGLEVEL, 'Loading local Moonshine model...')
                self._moonshine = MoonshineOnnxModel(models_dir=model_path, model_name=self.model)
            else:
                logging.log(LOGLEVEL, 'Loading Moonshine model from Hugging Face...')
                self._moonshine = MoonshineOnnxModel(model_name=self.model, model_precision=self.precision)
            self._tokenizer = load_tokenizer()
        except Exception as e:
            self._load_error = SpeechError(NOT_DOWNLOADED, f"Could not load the Moonshine model '{self.full_model}': {e}. "
                                           "Check the Model setting under Moonshine, and that you are online the first time a model is used. "
                                           "Press Test on the Speech-to-Text tab to try again.")
            raise self._load_error from e

    def forget_failed_load(self) -> None:
        self._load_error = None

    def transcribe(self, audio: np.ndarray, hint: str, names: list[str], language: str) -> str:
        if self._moonshine is None:
            self.load()
        try:
            tokens = self._moonshine.generate(audio[np.newaxis, :].astype(np.float32))
            text = self._tokenizer.decode_batch(tokens)[0]
        except Exception as e:
            raise SpeechError(FAILED, f"Moonshine could not turn the recording into text: {e}") from e
        return ensure_sentence_ending(clean(text))

    def close(self) -> None:
        self._moonshine = None
        self._tokenizer = None
