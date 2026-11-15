"""A stand-in voice service for the in-game tests. It speaks enough of XTTS's API for MantellaSoftware, and voices every line with a moment
of silence. So an NPC's line takes no time to voice and is over at once, and the tests wait only for what they check.
The tests point MantellaSoftware's XTTS setting at it and turn lip files off (making one takes about a second per line) while they run"""
import csv
import io
import json
import os
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PORT = 8021 # Not XTTS's own 8020: for that address MantellaSoftware also wants the XTTS folder to exist
SILENCE_SECONDS = 0.3 # How long each line lasts: long enough to see the subtitle
CHARACTERS_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "data", "Skyrim", "skyrim_characters.csv")


def voice_settings(port: int = PORT) -> dict[str, str]:
    """The settings in config.ini that make MantellaSoftware use the silent voice"""
    return {"tts_service": "XTTS", "xtts_url": f"http://127.0.0.1:{port}", "lip_generation": "Disabled",
            "fast_response_mode": "False"} # Fast Response Mode plays the first line on this PC as well


def silence() -> bytes:
    rate = 22050
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(rate * SILENCE_SECONDS))
    return out.getvalue()


def voice_names() -> list[str]:
    """Every voice in MantellaSoftware's character list. XTTS only voices a line with a voice it lists, and logs an error for each other one"""
    names: set[str] = set()
    with open(CHARACTERS_CSV, encoding="utf-8-sig", errors="replace") as f:
        for row in csv.DictReader(f):
            for column in ("voice_model", "skyrim_voice_folder", "advanced_voice_model"):
                if row.get(column):
                    names.add(row[column].replace(" ", "").lower())
    return sorted(names)


class SilentVoice(BaseHTTPRequestHandler):
    speakers: list[str] = []
    wav = b""

    def log_message(self, format: str, *args: Any):
        pass

    def __send(self, body: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/speakers_list"):
            value: Any = {"en": {"speakers": self.speakers}} # MantellaSoftware falls back to English voices for another language
        elif self.path.startswith("/get_models_list"):
            value = ["v2.0.2"]
        else:
            value = {"status": "running"} # MantellaSoftware checks that the service is up
        self.__send(json.dumps(value).encode("utf-8"), "application/json")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.startswith("/tts_to_audio"):
            self.__send(self.wav, "audio/wav")
        else: # Settings and model changes
            self.__send(b"{}", "application/json")


def start_silent_voice(port: int = PORT) -> ThreadingHTTPServer:
    """Raises OSError when the port is taken"""
    SilentVoice.speakers = voice_names()
    SilentVoice.wav = silence()
    server = ThreadingHTTPServer(("127.0.0.1", port), SilentVoice)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
