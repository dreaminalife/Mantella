"""Your keys, at the top of the Game page: a box for the OpenRouter key and one for the NanoGPT key.
Each key goes in the text file Mantella already reads it from (GPT_SECRET_KEY.txt, NANOGPT_SECRET_KEY.txt), never in config.ini,
and is never shown again: the box only says a key is saved"""
from __future__ import annotations

from dataclasses import dataclass

import gradio as gr

from src.config.config_loader import ConfigLoader
from src.stt_backends.keys import CHAT_KEY_FILE, read_key, save_key
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.SETTINGS_PAGE)


@dataclass(frozen=True)
class ChatKeyService:
    name: str
    key_file: str
    keys_page: str


CHAT_KEY_SERVICES = (
    ChatKeyService("OpenRouter", CHAT_KEY_FILE, "https://openrouter.ai/settings/keys"),
    ChatKeyService("NanoGPT", "NANOGPT_SECRET_KEY.txt", "https://nano-gpt.com/api"),
)

SAVED_PLACEHOLDER = "A key is saved. Paste a new one to replace it."
EMPTY_PLACEHOLDER = "Paste your key here"
HOW_TO = "\n".join(
    [f"- **{service.name}:** make a key on [your {service.name} keys page]({service.keys_page}), paste it in its box and press **Save key**."
     for service in CHAT_KEY_SERVICES]
    + ["- The key works from your next line in game. No restart needed.",
       "- Once saved, a key is never shown again: the box only says a key is saved.",
       f"- The OpenRouter key and an OpenAI key share one file ({CHAT_KEY_FILE}): saving one replaces the other.",
       f"- Until NanoGPT has its own key, it uses the one in {CHAT_KEY_FILE}."])


def has_key(service: ChatKeyService) -> bool:
    return bool(read_key(service.key_file))


def placeholder_for(service: ChatKeyService) -> str:
    return SAVED_PLACEHOLDER if has_key(service) else EMPTY_PLACEHOLDER


def save_from_box(service: ChatKeyService, key: str) -> tuple[bool, str]:
    """The Save key button: whether a key was saved, and what to tell the player. An empty box never deletes the saved key,
    since the box is empty again after every save"""
    if not (key or "").strip():
        return False, "Paste a key first."
    saved = save_key(service.key_file, key)
    if saved is None:
        return False, f"Could not save the key in {service.key_file}. See the Mantella window."
    return True, f"{service.name} key saved in {saved}. It works from your next line in game."


def render_chat_keys(config: ConfigLoader) -> None:
    """Your keys, folded, open while no key is saved yet"""
    with gr.Accordion("Your keys", open=not any(has_key(service) for service in CHAT_KEY_SERVICES), elem_classes="voice-clips-subsection"):
        gr.Markdown("OpenRouter and NanoGPT need a key. Paste it once and press **Save key**.")
        with gr.Accordion("How to get a key", open=False, elem_classes="help-fold"):
            gr.Markdown(HOW_TO)
        for service in CHAT_KEY_SERVICES:
            _render_key_row(config, service)


def _render_key_row(config: ConfigLoader, service: ChatKeyService) -> None:
    with gr.Row(equal_height=True):
        key_ui = gr.Textbox(value="", type="password", label=f"{service.name} key", max_lines=1, scale=999,
                            placeholder=placeholder_for(service))
        save_btn = gr.Button("Save key", size="sm", scale=0, min_width=110)
    status_ui = gr.Markdown("")

    def on_save(key: str):
        saved, message = save_from_box(service, key)
        if not saved:
            return gr.update(), message
        config.notify_keys_changed() # the next request reads the new key
        return gr.update(value="", placeholder=SAVED_PLACEHOLDER), message

    def on_open():
        return gr.update(value="", placeholder=placeholder_for(service)), ""

    # The box is emptied after saving, and never filled with the saved key, so it isn't shown on screen
    save_btn.click(on_save, inputs=[key_ui], outputs=[key_ui, status_ui], show_progress="hidden", show_api=False)
    key_ui.submit(on_save, inputs=[key_ui], outputs=[key_ui, status_ui], show_progress="hidden", show_api=False)
    # The page is built once when Mantella starts: what the box says is looked up again each time the page opens
    root = gr.context.Context.root_block
    if root is not None:
        root.load(on_open, outputs=[key_ui, status_ui], show_progress="hidden", show_api=False)
