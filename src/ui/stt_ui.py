"""The speech-to-text parts of the Speech-to-Text page: the Key box of each online service, and the Test button"""
from __future__ import annotations

import os
import time
from types import SimpleNamespace
from typing import Any, Callable

import gradio as gr
import numpy as np

from src import utils
from src.config.config_loader import ConfigLoader
from src.stt_backends import build_backend, chosen, language_problem
from src.stt_backends.base import SAMPLING_RATE, SpeechError
from src.stt_backends.keys import CHAT_KEY_FILE, key_for, read_key, save_key
from src.stt_backends.presets import CUSTOM_KEY_FILE, PRESETS, find
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.SETTINGS_PAGE)
microphone_log = get_topic_logger(LogTopic.MICROPHONE)

TEST_SAMPLE = os.path.join("data", "stt_test_sample.wav")
TEST_HINT = "Names: Balgruuf, Proventus, Dragonsreach." # the form every hint has (stt_backends/names.py)
TEST_NAMES = ("Balgruuf", "Proventus", "Dragonsreach")
SAVED_PLACEHOLDER = "A key is saved. Paste a new one to replace it."
CHAT_KEY_PLACEHOLDER = f"Your chat key from {CHAT_KEY_FILE} is used. Paste a key to use another one."
EMPTY_PLACEHOLDER = "Paste your key here"
KEY_HELP = ("Each service keeps its own key, in a text file named after it next to GPT_SECRET_KEY.txt, never in config.ini. "
            "Once saved, the key is never shown again: the box only says a key is saved.")
TEST_INTRO = "Check the service above with a short recording, without starting the game."
TEST_HOW = ("- The recording says \"Balgruuf, have you seen Proventus in Dragonsreach?\"\n"
            "- You see the text that came back, how long it took and whether the names came out right, or what went wrong.\n"
            "- The first test of a model on your PC can take a while: it downloads the model.")


def settings_now(config: ConfigLoader) -> SimpleNamespace:
    """The speech-to-text settings as they are on the page now (the page saves each change at once, before Mantella reloads them)"""
    definitions = config.definitions

    def get(identifier: str, fallback: Any = None) -> Any:
        try:
            return definitions.get_config_value_definition(identifier).value
        except Exception:
            return fallback

    models = {}
    for preset in PRESETS:
        models[preset.model_setting] = get(preset.model_setting, preset.default_model)
    language = get("stt_language", "default")
    if language == "default":
        language = get("language", getattr(config, "language", "en"))
    return SimpleNamespace(
        stt_service=get("stt_service", "Moonshine"), stt_online_service=get("stt_online_service", "OpenAI"), stt_models=models,
        stt_custom_format=get("stt_custom_format", "OpenAI-style"),
        stt_custom_address=get("stt_custom_address", ""), stt_custom_model=get("stt_custom_model", ""),
        stt_custom_timeout=get("stt_custom_timeout", 8.0), whisper_process_device=get("process_device", "cpu"),
        stt_translate=get("stt_translate", False), moonshine_folder=getattr(config, "moonshine_folder", ""),
        llm_api=get("llm_api", ""), stt_language=language,
    )


def key_file_for(online_service: str | None, custom: bool) -> str:
    if custom:
        return CUSTOM_KEY_FILE
    preset = find(online_service or "")
    return preset.key_file if preset is not None else ""


def placeholder_for(online_service: str | None, custom: bool, llm_api: str = "") -> str:
    """What the empty Key box says: a key of its own is saved, the chat key is used (OpenAI with OpenAI), or nothing yet"""
    key_file = key_file_for(online_service, custom)
    if key_file and read_key(key_file):
        return SAVED_PLACEHOLDER
    preset = None if custom else find(online_service or "")
    if preset is not None and key_for(preset, llm_api)[1] == CHAT_KEY_FILE:
        return CHAT_KEY_PLACEHOLDER
    return EMPTY_PLACEHOLDER


def save_from_box(file_name: str, key: str) -> tuple[bool, str]:
    """The Save key button: whether a key was saved, and what to tell the player. An empty box never deletes the saved key,
    since the box is empty again after every save"""
    if not file_name:
        return False, "Pick an online service first."
    if not (key or "").strip():
        return False, "Paste a key first."
    saved = save_key(file_name, key)
    if saved is None:
        return False, f"Could not save the key in {file_name}. See the Mantella window."
    return True, f"Saved in {saved}. Press Test at the end of this page to check it."


def render_key_box(online_service_ui: Any | None, refresh_running_key: Callable[[], None], llm_api_getter: Callable[[], str],
                   custom: bool = False) -> None:
    """A Key box under the Online Service list, or under Custom Service's settings. The key is saved in the service's own text file,
    never in config.ini, and is never shown again: the box only says a key is saved"""
    first_service = online_service_ui.value if online_service_ui is not None else None
    with gr.Column(variant="panel", scale=1): # drawn like the settings around it: a name with its ?, then the box and its button
        gr.HTML(f"""
            <div style="display: flex; align-items: center;">
                <h3 style="margin: 0; font-size: 1.25em;">Key</h3>
                <div class="tooltip-container" role="tooltip" aria-label="Key help">
                    <span class="tooltip-icon">?</span>
                    <div class="tooltip-content-left"><p>{KEY_HELP}</p></div>
                </div>
            </div>""")
        with gr.Row(equal_height=True, elem_classes="setting-controls"):
            key_ui = gr.Text(value="", type="password", show_label=False, container=False, max_lines=1, scale=999,
                             placeholder=placeholder_for(first_service, custom, llm_api_getter()))
            with gr.Row(elem_classes="button-container"):
                save_btn = gr.Button("Save key", size="sm")
        status_ui = gr.Markdown("", elem_classes="stt-key-status") # style.css hides it while empty

    def on_service(service: str | None = None):
        return gr.update(value="", placeholder=placeholder_for(service, custom, llm_api_getter())), ""

    def on_save(key: str, service: str | None = None):
        saved, message = save_from_box(key_file_for(service, custom), key)
        if not saved:
            return gr.update(), message
        try:
            refresh_running_key()
        except Exception as e:
            log.warning(f"Could not hand the new key to speech-to-text: {e}")
        return gr.update(value="", placeholder=SAVED_PLACEHOLDER), message

    service_inputs = [online_service_ui] if online_service_ui is not None and hasattr(online_service_ui, "_id") else []
    inputs = [key_ui] + service_inputs
    save_btn.click(on_save, inputs=inputs, outputs=[key_ui, status_ui], show_progress="hidden")
    key_ui.submit(on_save, inputs=inputs, outputs=[key_ui, status_ui], show_progress="hidden")
    if service_inputs:
        online_service_ui.change(on_service, service_inputs, [key_ui, status_ui], show_progress="hidden")
    # The page is built once when Mantella starts: what the box says is looked up again each time the page opens
    root = gr.context.Context.root_block
    if root is not None:
        root.load(on_service, service_inputs, [key_ui, status_ui], show_progress="hidden")


def read_sample(path: str = TEST_SAMPLE) -> np.ndarray:
    """The shipped sample: 16 kHz mono, as the microphone records it"""
    from scipy.io import wavfile
    full_path = path if os.path.isabs(path) else os.path.join(utils.resolve_path(), path)
    rate, samples = wavfile.read(full_path)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if samples.dtype == np.int16:
        samples = samples.astype(np.float32) / 32768.0
    samples = samples.astype(np.float32)
    if rate != SAMPLING_RATE:
        from scipy.signal import resample_poly
        samples = resample_poly(samples, SAMPLING_RATE, rate).astype(np.float32)
    return samples


def run_test(settings: SimpleNamespace, running_transcriber: Any | None = None) -> str:
    """Sends the sample through the chosen option. Returns what to show: the text, how long it took, and whether the names came out right.
    First, when the option doesn't understand the Spoken Language, which one does"""
    choice = chosen(settings)
    result = _test_result(choice, settings, running_transcriber)
    problem = language_problem(choice, settings.stt_language)
    return f"**{problem}**\n\n{result}" if problem else result


def _test_result(choice: Any, settings: SimpleNamespace, running_transcriber: Any | None) -> str:
    try:
        audio = read_sample()
    except Exception as e:
        return f"Could not read the test recording ({TEST_SAMPLE}): {e}"
    try:
        if running_transcriber is not None and running_transcriber.choice.identity == choice.identity:
            # The one Mantella is using: its model is already loaded, so a large Whisper is never on the graphics card twice
            start = time.monotonic()
            text = running_transcriber.transcribe_once(audio, TEST_HINT, list(TEST_NAMES))
        else:
            backend = build_backend(choice, settings)
            try:
                load_start = time.monotonic()
                backend.load()
                load_seconds = time.monotonic() - load_start
                if load_seconds > 1:
                    microphone_log.debug(f"Speech-to-text test: loading {choice.preset.name} took {load_seconds:.1f} s")
                start = time.monotonic()
                text = backend.transcribe(audio, TEST_HINT if backend.takes_hint else "", list(TEST_NAMES), settings.stt_language)
            finally:
                backend.close() # a model loaded only for the test is let go again
        seconds = time.monotonic() - start
    except SpeechError as e:
        return f"**{choice.preset.name} did not work.** {e.message}"
    except Exception as e:
        return f"**{choice.preset.name} did not work.** {e}"
    if not text:
        return f"**{choice.preset.name} answered in {seconds:.2f} s, but heard nothing.** Check the model, or that the service understands {settings.stt_language}."
    heard = [name for name in TEST_NAMES if name.lower() in text.lower()]
    missed = [name for name in TEST_NAMES if name not in heard]
    names_line = "All names heard right." if not missed else f"Names heard right: {', '.join(heard) or 'none'}. Misheard: {', '.join(missed)}."
    model = f" ({choice.model})" if choice.model else ""
    return f"**{choice.preset.name}{model} answered in {seconds:.2f} s:** \"{text}\"\n\n{names_line}"


def render_stt_test(config: ConfigLoader, transcriber_getter: Callable[[], Any]) -> None:
    """Under the Service part of the Speech-to-Text page: one line, the fold that explains it, then the button and what came back"""
    gr.Markdown(f"### Test Speech-to-Text\n{TEST_INTRO}")
    with gr.Accordion("How the test works", open=False):
        gr.Markdown(TEST_HOW)
    with gr.Row():
        test_btn = gr.Button("Test", variant="primary", size="sm", scale=0, min_width=120)
    result_ui = gr.Markdown("")

    def on_test():
        return run_test(settings_now(config), transcriber_getter())

    test_btn.click(on_test, outputs=[result_ui])
