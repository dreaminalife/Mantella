"""Hands saved voices to Pocket TTS (Kyutai's program won't read a voice from a file on this PC, only from a web address), and voice
clips to the play buttons on the Voice Clips page"""
import os
import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from src.config.config_loader import ConfigLoader
from src.http.routes.routeable import routeable
from src.tts.pocket_tts import voices_folder
from src.tts.voice_folders import voice_clips_folder
from src.voice_clips.clip_store import CANDIDATES, NPCS, VOICE_TYPES, shared_clip_store

SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.safetensors$")
SAFE_LANGUAGE = re.compile(r"^[a-z_]+$")
SAFE_KEY = re.compile(r"^[a-z0-9]+$")


class voice_files_route(routeable):
    def __init__(self, config: ConfigLoader) -> None:
        super().__init__(config)

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

        @app.get("/voice_clips/{kind}/{key}")
        async def voice_clip(kind: str, key: str):
            if kind not in (VOICE_TYPES, NPCS) or not SAFE_KEY.match(key):
                raise HTTPException(status_code=404)
            path = shared_clip_store(voice_clips_folder(self._config.save_folder)).clip(kind, key)
            if not path or not os.path.isfile(path):
                raise HTTPException(status_code=404)
            return FileResponse(path, media_type="audio/wav")

        @app.get("/voice_clips/{kind}/{key}/{name}")
        async def voice_clip_line(kind: str, key: str, name: str):
            if kind not in (VOICE_TYPES, NPCS) or not SAFE_KEY.match(key) or not SAFE_KEY.match(name):
                raise HTTPException(status_code=404)
            path = os.path.join(voice_clips_folder(self._config.save_folder), CANDIDATES, kind, key, name + ".wav")
            if not os.path.isfile(path):
                raise HTTPException(status_code=404)
            return FileResponse(path, media_type="audio/wav")
