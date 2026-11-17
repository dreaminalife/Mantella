"""A stand-in voice program for tests: answers OpenAI-style, XTTS-style or Kyutai-style requests with a short line of sound, and records what it was sent"""
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import soundfile as sf


def tone_wav(seconds: float = 0.5, rate: int = 24000, silence: float = 0.4) -> bytes:
    """A sound file with *silence* seconds of quiet before and after a tone, like many voice programs send"""
    t = np.arange(int(rate * seconds)) / rate
    quiet = np.zeros(int(rate * silence))
    samples = np.concatenate([quiet, 0.5 * np.sin(2 * np.pi * 220 * t), quiet]).astype(np.float32)
    buffer = io.BytesIO()
    sf.write(buffer, samples, rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


class FakeVoiceServer:
    def __init__(self, style: str = "openai", fail_times: int = 0, models_body: dict | None = None, speakers: list[str] | None = None):
        """*style*: openai, xtts or kyutai. *fail_times*: how many line requests fail first"""
        self.style = style
        self.fail_times = fail_times
        self.models_body = models_body or {"data": [{"id": "tts"}]}
        self.speakers = speakers or ["malenord", "femaleeventoned"]
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send_body(self, code: int, body: bytes, content_type: str = "application/json"):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if server.style == "openai" and self.path == "/v1/models":
                    return self.send_body(200, json.dumps(server.models_body).encode())
                if server.style == "xtts" and self.path == "/speakers_list":
                    return self.send_body(200, json.dumps(server.speakers).encode())
                if server.style == "kyutai" and self.path == "/health":
                    return self.send_body(200, b'{"status": "healthy"}')
                self.send_body(404, b"{}")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                server.requests.append(body)
                server.headers.append(dict(self.headers))
                expected = "/v1/audio/speech" if server.style == "openai" else "/tts_to_audio/"
                if self.path != expected:
                    return self.send_body(404, b"{}")
                if server.fail_times > 0:
                    server.fail_times -= 1
                    return self.send_body(500, b'{"error": "busy"}')
                self.send_body(200, tone_wav(), "audio/wav")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.httpd.shutdown()
        self.httpd.server_close()
