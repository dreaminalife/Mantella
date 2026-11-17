"""The ElevenLabs and Inworld parts of the Text-to-Speech page: what it costs, how to turn on voice cloning, and the key"""
from __future__ import annotations

import gradio as gr

from src.config.config_loader import ConfigLoader
from src.tts import elevenlabs, inworld
from src.tts.online_service import cost_per_conversation, problem, set_problem
from src.tts.registry import voice_services
from src.tts.voice_setups import key_file_places, save_key_file

STEPS = {
    elevenlabs.NAME: (
        f"1. Make an account at [elevenlabs.io]({elevenlabs.SIGN_UP_PAGE}). Voice cloning comes with the paid plans.\n"
        f"2. Make a key on [your API keys page]({elevenlabs.KEYS_PAGE}) with access to Text to Speech, Voices (write) and User (read).\n"
        "3. Paste it below and press **Save key**."),
    inworld.NAME: (
        f"1. Make an account at [Inworld]({inworld.SIGN_UP_PAGE}). Voice cloning comes with every plan.\n"
        f"2. Copy an API key from [Inworld's portal]({inworld.KEYS_PAGE}): the Basic one, from a key with write access.\n"
        "3. Paste it below and press **Save key**."),
}
SERVICES = {elevenlabs.NAME: (elevenlabs, elevenlabs.ElevenLabs), inworld.NAME: (inworld, inworld.Inworld)}


def render_online_service_panel(config: ConfigLoader, name: str) -> None:
    module, service = SERVICES[name]
    info = voice_services.find(name)
    cost = cost_per_conversation(info.price_per_million_chars if info else None)
    cost_line = f"**What it costs:** {cost}, at {name}'s pay-as-you-go price. A plan with characters included costs less.\n\n" if cost else ""
    gr.Markdown(
        cost_line
        + f"**Turn on voice cloning.** {name} needs a key, once:\n" + STEPS[name] + "\n\n"
        "Without a key, the Backup Voice Service speaks instead.\n\n"
        f"**Voices on your account.** Each voice type, and each NPC with a voice of their own, is cloned once on your {name} account "
        "when they first speak. Prepare all voices skips online services. Hearing a clip under **Voice Clips** clones it for a moment: "
        f"Mantella deletes it again unless you clone from it.")
    with gr.Row():
        key_ui = gr.Textbox(value="", type="password", label=f"{name} key", max_lines=1)
        save_btn = gr.Button("Save key", size="sm")
        check_btn = gr.Button("Check my key", size="sm")
    status_ui = gr.Markdown("")

    def on_open():
        """Why the service can't clone voices right now, each time the page is opened"""
        found = problem(name)
        if found:
            return f"**Right now:** {found}"
        if not any(place.is_file() for place in key_file_places(module.KEY_FILE)):
            return "No key saved yet."
        return ""

    page = gr.context.Context.root_block
    if page is not None:
        page.load(on_open, outputs=[status_ui], show_progress="hidden", show_api=False)

    def on_check():
        from src.tts.voice_setups import read_key_file
        key = read_key_file(module.KEY_FILE)
        if not key:
            return "No key saved yet. Paste one above and press **Save key**."
        return service.check_key(key, config.save_folder)

    def on_save(key: str):
        key = (key or "").strip()
        if not key:
            return gr.update(), "Paste your key first."
        try:
            saved = save_key_file(module.KEY_FILE, key)
        except OSError as e:
            return gr.update(), f"Could not save the key: {e}"
        config.notify_voice_services_changed() # the next line uses the new key
        set_problem(name, None)
        return "", f"Saved in `{saved}`. {service.check_key(key, config.save_folder)}"

    # The box is emptied after saving, and never filled with the saved key, so it isn't shown on screen
    save_btn.click(on_save, inputs=[key_ui], outputs=[key_ui, status_ui], show_api=False)
    check_btn.click(on_check, outputs=[status_ui], show_api=False)
