"""The ready-made speech-to-text choices: a speech program or a format with every detail filled in. Adding a choice is one more row in PRESETS"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Kinds: a speech program on this PC, or a format for sending the recording somewhere
FASTER_WHISPER = "faster_whisper"
MOONSHINE = "moonshine"
ONNX_ASR = "onnx_asr"
OPENAI_STYLE = "openai_style"
DEEPGRAM_STYLE = "deepgram_style"
ELEVENLABS_STYLE = "elevenlabs_style"

# Choices in the Speech-to-Text Service list
MOONSHINE_CHOICE = "Moonshine"
WHISPER_CHOICE = "Whisper"
PARAKEET_CHOICE = "Parakeet"
ONLINE_CHOICE = "Online Service"
CUSTOM_SERVICE = "Custom Service"

# Formats Custom Service can send, by the name the player sees
OPENAI_STYLE_FORMAT = "OpenAI-style"
FORMATS = {OPENAI_STYLE_FORMAT: OPENAI_STYLE, "Deepgram-style": DEEPGRAM_STYLE, "ElevenLabs-style": ELEVENLABS_STYLE}

# Where the Update button finds a service's speech models
OPENAI_MODEL_LIST = "openai" # GET <base>/models, keeping names with one of model_words
OPENROUTER_MODEL_LIST = "openrouter" # GET <base>/models?output_modalities=transcription, no key needed
DEEPGRAM_MODEL_LIST = "deepgram" # GET <base>/models, its speech-to-text part

DEFAULT_TIMEOUT_SECONDS = 8.0 # In total, both tries: the recording becomes text on the microphone's own thread, so a longer wait holds it up too


@dataclass(frozen=True)
class STTPreset:
    name: str # as shown in the lists
    kind: str
    model_setting: str # the identifier of its Model setting
    default_model: str
    suggestions: tuple[str, ...]
    base_address: str = ""
    key_file: str = "" # its own key file, next to GPT_SECRET_KEY.txt
    chat_service: str = "" # the chat AI service (llm_api) whose GPT_SECRET_KEY.txt it may use too
    languages: tuple[str, ...] | None = None # None: many languages
    online: bool = False # in the Online Service list: billed per request
    sends_prompt: bool = True # sends the hint sentence as `prompt`
    names_field: str = "" # sends the names as words to listen for instead, one field of this name per name
    model_list: str = "" # where Update finds the service's speech models; "" keeps the suggestions
    model_words: tuple[str, ...] = () # the service's model list keeps only names with one of these
    timeout: float = DEFAULT_TIMEOUT_SECONDS


PC_PRESETS: tuple[STTPreset, ...] = (
    STTPreset(MOONSHINE_CHOICE, MOONSHINE, "moonshine_model_size", "moonshine/tiny/quantized",
              ("moonshine/tiny/quantized_4bit", "moonshine/tiny/quantized", "moonshine/tiny/float",
               "moonshine/base/quantized_4bit", "moonshine/base/quantized", "moonshine/base/float"),
              languages=("en",)),
    STTPreset(WHISPER_CHOICE, FASTER_WHISPER, "whisper_model_size", "base",
              ("tiny", "tiny.en", "base", "base.en", "small", "small.en", "distil-small.en", "medium", "medium.en", "distil-medium.en",
               "large-v1", "large-v2", "large-v3", "large-v3-turbo", "distil-large-v2", "distil-large-v3")),
    # Parakeet works out the language itself and can't take names
    STTPreset(PARAKEET_CHOICE, ONNX_ASR, "stt_model_parakeet", "nemo-parakeet-tdt-0.6b-v3", ("nemo-parakeet-tdt-0.6b-v3",),
              languages=("bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it", "lv", "lt", "mt", "pl", "pt", "ro",
                         "sk", "sl", "es", "sv", "ru", "uk"),
              sends_prompt=False),
)

ONLINE_PRESETS: tuple[STTPreset, ...] = (
    STTPreset("OpenAI", OPENAI_STYLE, "stt_model_openai", "gpt-transcribe", ("gpt-transcribe",),
              base_address="https://api.openai.com/v1", key_file="OPENAI_SECRET_KEY.txt", chat_service="OpenAI", online=True,
              model_list=OPENAI_MODEL_LIST, model_words=("transcribe", "whisper")),
    STTPreset("Groq", OPENAI_STYLE, "stt_model_groq", "whisper-large-v3-turbo", ("whisper-large-v3-turbo", "whisper-large-v3"),
              base_address="https://api.groq.com/openai/v1", key_file="GROQ_SECRET_KEY.txt", online=True,
              model_list=OPENAI_MODEL_LIST, model_words=("whisper",)),
    # Mistral copies OpenAI's form (file, model, language, Bearer key, `text` back), but has no `prompt`: it takes each name as its own `context_bias`
    STTPreset("Mistral", OPENAI_STYLE, "stt_model_mistral", "voxtral-mini-transcribe-3", ("voxtral-mini-transcribe-3", "voxtral-small-transcribe-3"),
              base_address="https://api.mistral.ai/v1", key_file="MISTRAL_SECRET_KEY.txt", online=True, sends_prompt=False,
              names_field="context_bias", model_list=OPENAI_MODEL_LIST, model_words=("transcribe", "voxtral")),
    # Deepgram: the recording as the body, each name as its own keyterm (Nova-3 only)
    STTPreset("Deepgram", DEEPGRAM_STYLE, "stt_model_deepgram", "nova-3", ("nova-3", "nova-2"),
              base_address="https://api.deepgram.com/v1", key_file="DEEPGRAM_SECRET_KEY.txt", online=True, sends_prompt=False,
              names_field="keyterm", model_list=DEEPGRAM_MODEL_LIST),
    # ElevenLabs: its key file is shared with the ElevenLabs voices, so the player enters it once
    STTPreset("ElevenLabs", ELEVENLABS_STYLE, "stt_model_elevenlabs", "scribe_v3", ("scribe_v3", "scribe_v2"),
              base_address="https://api.elevenlabs.io/v1", key_file="ELEVENLABS_SECRET_KEY.txt", online=True, sends_prompt=False,
              names_field="keyterms"),
    # OpenRouter takes OpenAI's form too, but has no `prompt` and no shared field for names, so it gets none.
    # gpt-transcribe, not gpt-4o-mini-transcribe: OpenAI retires that on 26 February 2027, also when reached through OpenRouter
    STTPreset("OpenRouter", OPENAI_STYLE, "stt_model_openrouter", "openai/gpt-transcribe",
              ("openai/gpt-transcribe", "openai/whisper-large-v3-turbo", "deepgram/nova-3", "mistralai/voxtral-mini-transcribe-3-0"),
              base_address="https://openrouter.ai/api/v1", key_file="OPENROUTER_SECRET_KEY.txt", chat_service="OpenRouter", online=True,
              sends_prompt=False, model_list=OPENROUTER_MODEL_LIST),
)

PRESETS: tuple[STTPreset, ...] = PC_PRESETS + ONLINE_PRESETS
CUSTOM_KEY_FILE = "CUSTOM_STT_SECRET_KEY.txt"

# OpenAI models that stop working on 26 February 2027, and what replaces them
RETIRED_OPENAI_MODELS = {"whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"}


def find(name: str) -> STTPreset | None:
    wanted = (name or "").strip().lower()
    for preset in PRESETS:
        if preset.name.lower() == wanted:
            return preset
    return None


def service_choices() -> list[str]:
    return [p.name for p in PC_PRESETS] + [ONLINE_CHOICE, CUSTOM_SERVICE]


def online_choices() -> list[str]:
    return [p.name for p in ONLINE_PRESETS]


def takes_name_list(service: str, online_service: str, custom_format: str) -> bool:
    """Whether the choice these settings pick sends the names as separate words to listen for (where Catch First Names works)"""
    if (service or "").lower() == CUSTOM_SERVICE.lower():
        return FORMATS.get(custom_format, OPENAI_STYLE) in (DEEPGRAM_STYLE, ELEVENLABS_STYLE)
    if (service or "").lower() != ONLINE_CHOICE.lower():
        return False
    preset = find(online_service)
    return preset is not None and (bool(preset.names_field) or preset.kind in (DEEPGRAM_STYLE, ELEVENLABS_STYLE))


def custom_preset(format_name: str, address: str, model: str, timeout: float) -> STTPreset:
    """Custom Service: the player's own format, address and model"""
    kind = FORMATS.get(format_name, OPENAI_STYLE)
    return STTPreset(CUSTOM_SERVICE, kind, "stt_custom_model", model, (model,) if model else (), base_address=address,
                     key_file=CUSTOM_KEY_FILE, timeout=timeout or DEFAULT_TIMEOUT_SECONDS)


@dataclass
class STTChoice:
    """What the settings pick: the preset and its model"""
    preset: STTPreset
    model: str
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> tuple:
        """Changes when a new backend is needed"""
        p = self.preset
        return (p.name, p.kind, p.base_address, self.model, tuple(sorted(self.settings.items())), p.timeout, p.sends_prompt, p.names_field)


def chosen(config) -> STTChoice:
    """The preset and model the settings pick"""
    service = getattr(config, "stt_service", MOONSHINE_CHOICE) or MOONSHINE_CHOICE
    if service.lower() == CUSTOM_SERVICE.lower():
        preset = custom_preset(config.stt_custom_format, config.stt_custom_address, config.stt_custom_model, config.stt_custom_timeout)
        return STTChoice(preset, config.stt_custom_model)
    preset = find(config.stt_online_service) if service.lower() == ONLINE_CHOICE.lower() else find(service)
    if preset is None:
        preset = PC_PRESETS[0]
    model = (getattr(config, "stt_models", {}) or {}).get(preset.model_setting) or preset.default_model
    settings: dict[str, Any] = {}
    if preset.kind == FASTER_WHISPER:
        settings = {"device": config.whisper_process_device, "translate": bool(getattr(config, "stt_translate", False))}
    elif preset.kind == MOONSHINE:
        settings = {"folder": getattr(config, "moonshine_folder", "")}
    return STTChoice(preset, model, settings)
