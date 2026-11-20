"""A stand-in speech-to-text service for tests: answers transcription requests (OpenAI's answer, or another service's with *reply*)
and records what it was sent. No internet, no cost"""
import json
import threading
import time
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src import utils


@pytest.fixture
def key_folders(tmp_path, monkeypatch):
    """Key files in a folder of their own, never the real ones: the 'mod folder' is tmp/mod, Mantella's folder tmp/mod/a/b/c"""
    mod = tmp_path / "mod"
    mantella = mod / "a" / "b" / "c"
    mantella.mkdir(parents=True)
    monkeypatch.setattr(utils, "resolve_path", lambda: str(mantella))
    monkeypatch.chdir(tmp_path)
    return mod, mantella


def form_fields(content_type: str, body: bytes) -> list[tuple[str, bytes]]:
    """The fields of a multipart form, in order, repeated ones included"""
    if not content_type.startswith("multipart/"):
        return []
    message = BytesParser(policy=default).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    return [(part.get_param("name", header="content-disposition"), part.get_payload(decode=True)) for part in message.iter_parts()]


class FakeSTTServer:
    def __init__(self, answers: list[tuple[int, dict]] | None = None, text: str = "Balgruuf, have you seen Proventus?", delay: float = 0.0,
                 reply=None, models: dict | None = None):
        """*answers*: (status, JSON body) for each request in turn; after them, 200 with *text*. *delay*: seconds before each answer.
        *reply*: makes the answer's JSON from *text* (OpenAI's {"text"} if not given). *models*: what a GET for the model list returns"""
        self.answers = list(answers or [])
        self.text = text
        self.delay = delay
        self.reply = reply or (lambda text: {"text": text})
        self.models = models
        self.requests: list[dict] = [] # path, headers, fields
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send_json(self, code: int, body: dict):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                server.requests.append({"path": self.path, "headers": dict(self.headers), "fields": {}, "all_fields": []})
                self.send_json(200, server.models or {"data": [{"id": "whisper-large-v3"}, {"id": "llama-3"}, {"id": "gpt-transcribe"}]})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                fields = form_fields(self.headers.get("Content-Type", ""), body)
                server.requests.append({"path": self.path, "headers": dict(self.headers), "fields": dict(fields), "all_fields": fields, "body": body})
                if server.delay:
                    time.sleep(server.delay)
                if server.answers:
                    code, answer = server.answers.pop(0)
                    return self.send_json(code, answer)
                self.send_json(200, server.reply(server.text))

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._httpd.shutdown()
        self._httpd.server_close()
