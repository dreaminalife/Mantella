"""What every format for sending the recording somewhere shares: the address, the time limit with one more try, the key, and a plain
message for each problem. Each format (openai_style.py, deepgram_style.py, elevenlabs_style.py) only fills in its own request and reads its answer"""
from __future__ import annotations

import io
import json
import logging
import re
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import numpy as np
import requests
from scipy.io import wavfile

from src.stt_backends.base import (FAILED, KEY_REFUSED, NO_KEY, SAMPLING_RATE, UNKNOWN_MODEL, UNREACHABLE, SpeechBackend,
                                   SpeechError)
from src.stt_backends.cleanup import clean
from src.stt_backends.presets import DEFAULT_TIMEOUT_SECONDS

# A refused key as services repeat it ("sk-proj-****1234")
_MASKED_KEY = re.compile(r"\S*\*{3,}\S*")
_KEY_RUN = re.compile(r"[A-Za-z0-9]{20,}") # keys have long runs of letters and digits; model names and words don't
_VERSION = re.compile(r"^v\d+[a-z0-9]*$")
MIN_RETRY_SECONDS = 0.5 # no second try when less time than this is left


def endpoint(address: str, ending: str) -> str:
    """The full address to send to. A base address gets the format's *ending* added (and /v1 before it when it has no path at all);
    a full path such as whisper.cpp's /inference is kept as it is"""
    address = (address or "").strip()
    if not address:
        return ""
    if "://" not in address:
        address = "http://" + address
    parts = urlsplit(address)
    path = parts.path.rstrip("/")
    if path.endswith(ending):
        pass
    elif not path:
        path = "/v1" + ending
    elif _VERSION.match(path.rsplit("/", 1)[-1]):
        path = path + ending
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def wav_bytes(audio: np.ndarray) -> bytes:
    """16-bit WAV: the form every service reads, and half the size of the microphone's 32-bit float sound"""
    samples = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    wavfile.write(buffer, SAMPLING_RATE, samples)
    return buffer.getvalue()


def hide_keys(text: str, key: str = "") -> str:
    """The text with every key, or part of one, replaced, so it can go in the log players share when asking for help"""
    if key:
        text = text.replace(key, "(key hidden)")
        if len(key) >= 8: # services often repeat only the start and the last few characters
            text = text.replace(key[-4:], "****")
    text = _MASKED_KEY.sub("(key hidden)", text)
    words = text.split(" ")
    for i, word in enumerate(words):
        if any(any(c.isdigit() for c in run) and any(c.isalpha() for c in run) for run in _KEY_RUN.findall(word)):
            words[i] = "(hidden)"
    return " ".join(words)


def _message_in(body: Any) -> str:
    """The service's own words in an error answer: OpenAI's {"error": {"message"}}, Deepgram's {"err_msg"}, ElevenLabs' {"detail": {"message"}}
    or {"detail": [{"msg"}]}"""
    if isinstance(body, dict):
        for key in ("error", "detail"):
            if key in body:
                return _message_in(body[key]) or json.dumps(body[key])
        for key in ("message", "err_msg", "msg"):
            if isinstance(body.get(key), str):
                return body[key]
    if isinstance(body, list) and body:
        return _message_in(body[0])
    if isinstance(body, str):
        return body
    return ""


def _excerpt(response: requests.Response, key: str = "") -> str:
    """What the service said, kept short, with any key hidden (OpenAI repeats the start and end of a refused key)"""
    try:
        body = response.json()
        text = _message_in(body) or json.dumps(body)
    except Exception:
        text = response.text or ""
    return hide_keys(text.strip().replace("\n", " "), key)[:300]


class ServiceFormat(SpeechBackend):
    """Sends the recording to an address and reads the text back. Subclasses set ENDING and fill in _request and _text"""

    ENDING = "" # added to a base address, e.g. /audio/transcriptions

    def __init__(self, service: str, address: str, model: str, key: str = "", key_file: str = "", needs_key: bool = False,
                 sends_prompt: bool = True, default_model: str = "", timeout: float = DEFAULT_TIMEOUT_SECONDS, names_field: str = "") -> None:
        """*service*: the name in messages. *needs_key*: an online service, where no key means no request at all.
        *sends_prompt*: sends the hint sentence. *names_field*: sends each name as its own field of this name instead"""
        self.service = service
        self.url = endpoint(address, self.ENDING)
        self.model = model
        self.key = key
        self.key_file = key_file
        self.needs_key = needs_key
        self.sends_prompt = sends_prompt
        self.default_model = default_model
        self.timeout = timeout or DEFAULT_TIMEOUT_SECONDS
        self.names_field = names_field
        self.takes_hint = sends_prompt or bool(names_field)

    def set_key(self, key: str) -> None:
        self.key = key

    def _request(self, sound: bytes, hint: str, names: list[str], language: str) -> dict[str, Any]:
        """The requests.post arguments besides the address and time limit: params, data, files, headers"""
        raise NotImplementedError

    def _text(self, body: Any) -> str:
        """The text in the service's answer"""
        raise NotImplementedError

    def _is_custom(self) -> bool:
        return self.service.lower().startswith("custom")

    def _where_key_goes(self) -> str:
        if self._is_custom():
            return "the Key box of the Custom Service section on the Speech-to-Text tab"
        return f"the Key box on the Speech-to-Text tab (it is saved in {self.key_file})" if self.key_file else "the Key box on the Speech-to-Text tab"

    def _sent_names(self, request: dict[str, Any]) -> str:
        """The names exactly as they go out in *request*: the hint line, or each name sent on its own. 'none' when nothing is sent"""
        fields = [pair for part in ("params", "data") if isinstance(request.get(part), list) for pair in request[part]]
        hint = next((value for key, value in fields if key == "prompt"), "")
        each = [value for key, value in fields if self.names_field and key == self.names_field]
        if each:
            return ", ".join(each)
        return hint or "none"

    def transcribe(self, audio: np.ndarray, hint: str, names: list[str], language: str) -> str:
        if not self.url:
            raise SpeechError(UNREACHABLE, f"{self.service} has no address. Type one into Address in the Custom Service section on the Speech-to-Text tab.")
        if self.needs_key and not self.key:
            raise SpeechError(NO_KEY, f"There is no key for {self.service}. Paste your {self.service} key into {self._where_key_goes()}. Typing still works.")
        if len(audio) == 0:
            return ""
        sound = wav_bytes(audio)
        request = self._request(sound, hint, names, language)
        logging.debug(f"Speech-to-text request to {self.service}: url={self.url}, model={self.model}, language={language}, names={len(names or [])}, "
                      f"hint_length={len(hint or '')}, audio_bytes={len(sound)}")
        logging.debug(f"Speech-to-text names sent to {self.service}: {self._sent_names(request)}")

        deadline = time.monotonic() + self.timeout
        response = None
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if attempt > 0 and remaining < MIN_RETRY_SECONDS:
                break
            try:
                response = requests.post(self.url, timeout=max(remaining, 0.1), **request)
            except requests.exceptions.Timeout:
                raise SpeechError(UNREACHABLE, f"{self.service} did not answer within {self.timeout:g} seconds. Say it again, or type it.")
            except requests.exceptions.RequestException as e:
                response = None
                if attempt == 0:
                    continue
                raise SpeechError(UNREACHABLE, f"Could not reach {self.service} at {self.url} ({type(e).__name__}). Check the internet connection, "
                                  "or that the program at that address is running.") from e
            if response.status_code >= 500 and attempt == 0:
                continue
            break
        if response is None:
            raise SpeechError(UNREACHABLE, f"Could not reach {self.service} at {self.url}. Check the internet connection, or that the program at that address is running.")
        return self._read(response, language)

    def _read(self, response: requests.Response, language: str = "") -> str:
        status = response.status_code
        if status == 200:
            try:
                body = response.json()
            except ValueError:
                return clean(response.text)
            try:
                return clean(str(self._text(body) or ""))
            except (KeyError, IndexError, TypeError, AttributeError):
                fix = "Check Format in the Custom Service section." if self._is_custom() else "Try another Online Service until Mantella is updated."
                raise SpeechError(FAILED, f"{self.service} answered in a form Mantella doesn't know. {fix}")
        said = _excerpt(response, self.key)
        lowered = said.lower()
        if status in (401, 403):
            raise SpeechError(KEY_REFUSED, f"{self.service} refused the key. Check the key in {self._where_key_goes()}. ({said})")
        if status == 402 or (status == 429 and ("quota" in lowered or "credit" in lowered or "billing" in lowered)):
            raise SpeechError(KEY_REFUSED, f"{self.service} says the account is out of credit. ({said})")
        about_model = ("model_not_found" in lowered or "no such model" in lowered or (status == 404 and "model" in lowered)
                       or (status == 400 and "model" in lowered and "not" in lowered))
        if about_model:
            suggestion = f" Try its recommended model, {self.default_model}." if self.default_model and self.default_model != self.model else ""
            if "language" in lowered and language: # e.g. Deepgram's "No such model/language/tier combination found."
                raise SpeechError(UNKNOWN_MODEL, f"{self.service} doesn't know the model '{self.model}', or doesn't understand the Spoken Language '{language}'. "
                                  f"Check Model, and Advanced → Spoken Language, on the Speech-to-Text tab.{suggestion} ({said})")
            raise SpeechError(UNKNOWN_MODEL, f"{self.service} doesn't know the model '{self.model}'.{suggestion} ({said})")
        if status == 404:
            raise SpeechError(UNREACHABLE, f"{self.service} has nothing at {self.url}. Check the address. ({said})")
        raise SpeechError(FAILED, f"{self.service} could not turn the recording into text (error {status}). ({said})")
