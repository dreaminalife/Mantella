"""The Model dropdown of an online choice: the speech models the service lists, or the ready-made suggestions when it can't be asked"""
from __future__ import annotations


import requests

from src.stt_backends.keys import key_for
from src.stt_backends.presets import DEEPGRAM_MODEL_LIST, OPENAI_MODEL_LIST, OPENROUTER_MODEL_LIST, STTPreset
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.MICROPHONE)

LIST_TIMEOUT_SECONDS = 5


def _listed(preset: STTPreset, key: str) -> list[str]:
    """Every speech model the service lists"""
    models_address = preset.base_address.rstrip("/") + "/models"
    if preset.model_list == OPENROUTER_MODEL_LIST: # public: OpenRouter lists its speech models without a key
        response = requests.get(models_address, params={"output_modalities": "transcription"}, timeout=LIST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return [str(m.get("id", "")) for m in response.json().get("data", []) if isinstance(m, dict)]
    if preset.model_list == DEEPGRAM_MODEL_LIST:
        response = requests.get(models_address, headers={"Authorization": f"Token {key}"}, timeout=LIST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return [str(m.get("canonical_name") or m.get("name") or "") for m in response.json().get("stt", []) if isinstance(m, dict)]
    response = requests.get(models_address, headers={"Authorization": f"Bearer {key}"}, timeout=LIST_TIMEOUT_SECONDS)
    response.raise_for_status()
    listed = [str(m.get("id", "")) for m in response.json().get("data", []) if isinstance(m, dict)]
    words = tuple(w.lower() for w in preset.model_words)
    return [m for m in listed if any(w in m.lower() for w in words)]


def speech_models(preset: STTPreset, llm_api: str = "") -> list[str]:
    """The suggestions first, then the service's other speech models"""
    suggestions = list(preset.suggestions)
    if preset.model_list not in (OPENAI_MODEL_LIST, OPENROUTER_MODEL_LIST, DEEPGRAM_MODEL_LIST) or not preset.base_address:
        return suggestions
    key, _ = key_for(preset, llm_api)
    if not key and preset.model_list != OPENROUTER_MODEL_LIST:
        return suggestions
    try:
        listed = _listed(preset, key)
    except Exception as e:
        log.warning(f"Could not get {preset.name}'s list of models ({type(e).__name__}). The Model list shows the recommended ones.")
        return suggestions
    return suggestions + sorted({m for m in listed if m and m not in suggestions})
