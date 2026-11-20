"""Moving a player's old speech-to-text setup over the first time the new version starts, so nobody loses voice input after updating.
External Whisper Service with OpenAI or Groq becomes Online Service; with whisper.cpp or a typed address it becomes Custom Service"""
from __future__ import annotations

from src.stt_backends.presets import (CUSTOM_KEY_FILE, CUSTOM_SERVICE, ONLINE_CHOICE, OPENAI_STYLE_FORMAT, RETIRED_OPENAI_MODELS, WHISPER_CHOICE,
                                      find)

OLD_STT_KEYS = ("external_whisper_service", "whisper_url")

# The old Whisper Service list, and the addresses its choices stood for
OLD_SERVICE_ADDRESSES = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "whisper.cpp": "http://127.0.0.1:8080/inference",
}
ONLINE_BY_ADDRESS = {"https://api.openai.com/v1": "OpenAI", "https://api.groq.com/openai/v1": "Groq"}


def runs_on_pc(model: str) -> bool:
    """Whether Whisper on the PC can run this model. The old shared Whisper Model list also held online names (whisper-1, gpt-4o-transcribe,
    whisper-large-v3-turbo), which it can't"""
    whisper = find(WHISPER_CHOICE)
    return model in whisper.suggestions or "/" in model or model == "turbo"


def carried_over_stt(old_stt: dict[str, str], stored: dict[str, str]) -> tuple[dict[str, object], str, str]:
    """The new settings for an old setup, a line telling the player what moved, and the key file the old STT_SECRET_KEY.txt goes to
    (empty when none). *old_stt*: the old keys found in config.ini. *stored*: every key in config.ini. Empty when nothing needs to change"""
    changes: dict[str, object] = {}
    key_file = ""
    moved: list[str] = []
    whisper_model = (stored.get("whisper_model_size") or "base").strip()
    external = (old_stt.get("external_whisper_service") or "").strip().lower() == "true"
    uses_whisper = (stored.get("stt_service") or "").strip().lower() == WHISPER_CHOICE.lower()

    if external and uses_whisper:
        service = (old_stt.get("whisper_url") or "OpenAI").strip()
        address = OLD_SERVICE_ADDRESSES.get(service.lower(), service)
        online = ONLINE_BY_ADDRESS.get(address.rstrip("/"))
        if online:
            preset = find(online)
            model = whisper_model
            if (online == "OpenAI" and model in RETIRED_OPENAI_MODELS) or runs_on_pc(model): # a PC model name (e.g. base) never worked online
                model = preset.default_model
            changes.update({"stt_service": ONLINE_CHOICE, "stt_online_service": online, preset.model_setting: model})
            key_file = preset.key_file
            moved.append(f"External Whisper Service ({online}) is now Online Service: {online}, model {model}")
        else:
            changes.update({"stt_service": CUSTOM_SERVICE, "stt_custom_format": OPENAI_STYLE_FORMAT, "stt_custom_address": address,
                            "stt_custom_model": whisper_model})
            if "openai" in address.lower(): # the old code only sent the key there; whisper.cpp and other addresses never got it
                key_file = CUSTOM_KEY_FILE
            moved.append(f"External Whisper Service ({address}) is now {CUSTOM_SERVICE}, OpenAI-style")

    # The old Whisper Model held the online model's name, which Whisper on your PC can't run. Only on the first start, while the old settings are there
    if (external and uses_whisper) or (old_stt and not runs_on_pc(whisper_model)):
        if whisper_model != "base":
            changes["whisper_model_size"] = "base"
            moved.append(f"Whisper on your PC goes back to its recommended model, base")

    openai = find("OpenAI")
    openai_model = (stored.get(openai.model_setting) or "").strip()
    if openai_model in RETIRED_OPENAI_MODELS:
        changes[openai.model_setting] = openai.default_model
        moved.append(f"OpenAI's {openai_model} stops working on 26 February 2027, so OpenAI uses {openai.default_model}")

    if not moved:
        return changes, "", key_file
    return changes, "Speech-to-text settings moved to the new Speech-to-Text tab: " + "; ".join(moved) + ".", key_file
