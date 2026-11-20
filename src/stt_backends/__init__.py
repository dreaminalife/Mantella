"""Speech-to-text: speech programs that run on this PC, and formats for sending the recording somewhere, picked through a table of ready-made choices (presets.py)"""
from __future__ import annotations

from src.stt_backends.base import SpeechBackend, SpeechError
from src.stt_backends.keys import CHAT_KEY_FILE, key_for
from src.stt_backends.presets import (DEEPGRAM_STYLE, ELEVENLABS_STYLE, FASTER_WHISPER, MOONSHINE, ONLINE_CHOICE, ONNX_ASR, OPENAI_STYLE,
                                      PC_PRESETS, STTChoice, chosen)


def build_backend(choice: STTChoice, config, chat_key_file: str = CHAT_KEY_FILE) -> SpeechBackend:
    """The backend for a choice, not loaded yet"""
    preset = choice.preset
    if preset.kind == FASTER_WHISPER:
        from src.stt_backends.faster_whisper_program import FasterWhisperProgram
        return FasterWhisperProgram(choice.model, choice.settings.get("device", "cpu"), choice.settings.get("translate", False))
    if preset.kind == MOONSHINE:
        from src.stt_backends.moonshine_program import MoonshineProgram
        return MoonshineProgram(choice.model, choice.settings.get("folder", ""))
    if preset.kind == ONNX_ASR:
        from src.stt_backends.onnx_asr_program import OnnxAsrProgram
        return OnnxAsrProgram(choice.model)
    if preset.kind in (OPENAI_STYLE, DEEPGRAM_STYLE, ELEVENLABS_STYLE):
        if preset.kind == DEEPGRAM_STYLE:
            from src.stt_backends.deepgram_style import DeepgramStyleFormat as Format
        elif preset.kind == ELEVENLABS_STYLE:
            from src.stt_backends.elevenlabs_style import ElevenLabsStyleFormat as Format
        else:
            from src.stt_backends.openai_style import OpenAIStyleFormat as Format
        key, _ = key_for(preset, getattr(config, "llm_api", ""), chat_key_file)
        return Format(preset.name, preset.base_address, choice.model, key, preset.key_file, needs_key=preset.online,
                      sends_prompt=preset.sends_prompt, default_model=preset.default_model, timeout=preset.timeout,
                      names_field=preset.names_field)
    raise ValueError(f"Unknown speech-to-text kind '{preset.kind}'")


def language_problem(choice: STTChoice, language: str) -> str:
    """A message when the chosen option doesn't understand the Spoken Language, naming one that does. Empty when it does"""
    languages = choice.preset.languages
    if not language or languages is None or language in languages:
        return ""
    others = [p.name for p in PC_PRESETS if p is not choice.preset and (p.languages is None or language in p.languages)] + [ONLINE_CHOICE]
    return (f"{choice.preset.name} only understands {', '.join(languages)}, but the Spoken Language is '{language}'. "
            f"Pick {', '.join(others[:-1])} or {others[-1]} in Speech-to-Text Service on the Speech-to-Text tab, which understand '{language}'.")


__all__ = ["SpeechBackend", "SpeechError", "STTChoice", "build_backend", "chosen", "key_for", "language_problem"]
