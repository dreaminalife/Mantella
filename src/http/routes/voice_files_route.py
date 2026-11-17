"""Hands saved voices to Pocket TTS. Kyutai's program won't read a voice from a file on this PC, only from a web address"""
import os
import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from src.config.config_loader import ConfigLoader
from src.http.routes.routeable import routeable
from src.tts.pocket_tts import voices_folder

SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.safetensors$")
SAFE_LANGUAGE = re.compile(r"^[a-z_]+$")


class voice_files_route(routeable):
    def __init__(self, config: ConfigLoader, show_debug_messages: bool = False) -> None:
        super().__init__(config, show_debug_messages)

    def _setup_route(self):
        pass

    def add_route_to_server(self, app: FastAPI):
        @app.get("/voices/{language}/{file_name}")
        async def voice_file(language: str, file_name: str):
            if not SAFE_LANGUAGE.match(language) or not SAFE_NAME.match(file_name):
                raise HTTPException(status_code=404)
            path = os.path.join(voices_folder(self._config.save_folder, language), file_name)
            if not os.path.isfile(path):
                raise HTTPException(status_code=404)
            return FileResponse(path, media_type="application/octet-stream")
