"""The online services' parts of the Text-to-Speech page (ElevenLabs, Inworld, HeyGen, Qwen, Boson AI): what it costs, how to turn on
voice cloning, and the key"""
from __future__ import annotations

from typing import Any, Callable

import gradio as gr

from src.config.config_loader import ConfigLoader
from src.tts import boson, elevenlabs, heygen, inworld, qwen
from src.tts.model_catalog import ServiceModel, ServiceModels
from src.tts.online_service import problem, set_problem
from src.tts.voice_folders import voice_clips_folder
from src.tts.voice_setups import key_file_places, save_key_file
from src.tts.voice_style import EXTRA_REQUEST_LABEL, ServiceStyle
from src.voice_clips.clip_store import shared_clip_store

STEPS = {
    elevenlabs.NAME: (
        f"1. Make an account at [elevenlabs.io]({elevenlabs.SIGN_UP_PAGE}). Voice cloning comes with the paid plans.\n"
        f"2. Make a key on [your API keys page]({elevenlabs.KEYS_PAGE}) with access to Text to Speech, Voices (write) and User (read).\n"
        "3. Paste it below and press **Save key**."),
    inworld.NAME: (
        f"1. Make an account at [Inworld]({inworld.SIGN_UP_PAGE}). Voice cloning comes with every plan.\n"
        f"2. Copy an API key from [Inworld's portal]({inworld.KEYS_PAGE}): the Basic one, from a key with write access.\n"
        "3. Paste it below and press **Save key**."),
    heygen.NAME: (
        f"1. Make an account at [HeyGen]({heygen.SIGN_UP_PAGE}). Voice cloning is free while HeyGen calls it a preview.\n"
        f"2. Make a key on [your API keys page]({heygen.KEYS_PAGE}). Turbo Free speaks for free until Nov 30, 2026; after that, add credit.\n"
        "3. Paste it below and press **Save key**."),
    qwen.NAME: (
        f"1. Make an Alibaba Cloud account and open [Model Studio in the Singapore region]({qwen.WEBSITE}). Voice cloning is free.\n"
        f"2. Make a key on [the API keys page]({qwen.KEYS_PAGE}), and copy your Workspace ID from the console's Workspace page into "
        "**Workspace ID** below. Both must come from the same workspace.\n"
        "3. Paste the key below and press **Save key**."),
    boson.NAME: (
        f"1. Make an account at [Boson AI]({boson.SIGN_UP_PAGE}).\n"
        f"2. Make a key on [your API key page]({boson.KEYS_PAGE}), and claim the $10 trial credit there.\n"
        "3. Paste it below and press **Save key**.\n\n"
        "Boson AI clones a voice only together with the words spoken in its clips. Mantella hears each clip once with your choice on the "
        "**Speech-to-Text** tab, also if you type instead of talking."),
}
SERVICES = {elevenlabs.NAME: (elevenlabs, elevenlabs.ElevenLabs), inworld.NAME: (inworld, inworld.Inworld), heygen.NAME: (heygen, heygen.HeyGen),
            qwen.NAME: (qwen, qwen.Qwen), boson.NAME: (boson, boson.BosonAI)}


HOW_MODELS_WORK = ("- **Each model keeps its own settings.** Pick a model next to **Settings for** to change its settings without speaking "
                   "with it.\n"
                   "- **Missing a model?** Type its name in the Model list next to **Settings for**, exactly as {name} writes it, "
                   "and press **Add**.\n"
                   "- **Don't use a model?** Pick it there and press **Remove**. Its settings are kept if you add it again. "
                   "**Put back Mantella's models** brings back the ones you removed.")


class ModelSettingsUI:
    """One online service's models on the Text-to-Speech page. The Model it speaks with (*used_ui*, next to TTS Service, with its
    *note_ui*) hides the settings for every model that it skips, and tells Customization per voice type and per NPC. The model next
    to Settings for (*edit_ui*) picks whose settings show in Settings for <model> (voice_style.ServiceStyle.show_model), without
    speaking with it; *use_btn* makes the service speak with it. Picking the Model it speaks with shows that model's settings too"""

    def __init__(self, config: ConfigLoader, name: str, used_ui: Any, note_ui: gr.Markdown, every_model_columns: dict[str, Any],
                 edit_ui: Any, heading_ui: gr.Accordion, extra_intro_ui: gr.Markdown | None, use_btn: Any, columns: dict[str, Any],
                 values: dict[str, Any], shown_model_ui: Any = None) -> None:
        self.config = config
        self.name = name
        self.models = ServiceModels.of(name)
        self.style = ServiceStyle(config, name)
        self.used_ui = used_ui
        self.note_ui = note_ui
        self.every_model_columns = every_model_columns # setting name -> its column under Global settings, hidden with a model that skips it
        self.edit_ui = edit_ui
        self.heading_ui = heading_ui
        self.extra_intro_ui = extra_intro_ui
        self.use_btn = use_btn
        self.columns = columns # setting name -> its column under Settings for <model>, hidden with a model that skips it
        self.values = values # setting name -> its control, for the settings each model keeps its own value of
        self.shown_model_ui = shown_model_ui # Customization per voice type and per NPC redraws when it changes

    # ---- what the page says ----

    @staticmethod
    def note(name: str, model: ServiceModel) -> str:
        """Under Model: a model that isn't one of Mantella's, or one the service has ended"""
        if not model.known:
            return f"{model.id} isn't one of Mantella's models, so every {name} setting is shown."
        if model.ended():
            models = ServiceModels.of(name)
            instead = models.default().name if models is not None else "another model"
            return f"{name} ended {model.name}. Pick {instead}."
        return ""

    @staticmethod
    def heading(model: ServiceModel) -> str:
        """The fold that holds the settings each model keeps its own value of"""
        return f"Settings for {model.name}"

    @staticmethod
    def extra_intro(name: str, model: ServiceModel) -> str:
        return f"Sent with every line on {model.name}. Use the names from {name}'s own instructions."

    @staticmethod
    def shown_model(name: str, model: ServiceModel) -> str:
        return f"{name}:{model.id}"

    @staticmethod
    def use_label(name: str, model: ServiceModel) -> str:
        return f"Use {model.name} for {name}"

    def edit_choices(self, current: Any) -> list[tuple[str, str]]:
        """The models next to Settings for: the list's, and a model typed in as the Model"""
        choices = self.models.choices(current, self.config)
        current = str(current or "").strip()
        if current and all(value != current for _, value in choices):
            choices.append((current, current))
        return choices

    # ---- the Model it speaks with ----

    def used_outputs(self) -> list:
        found = [self.note_ui, *self.every_model_columns.values(), self.edit_ui, self.use_btn]
        if self.shown_model_ui is not None:
            found.append(self.shown_model_ui)
        return found

    def on_use(self, value: str | None) -> list:
        """The service speaks with another model: its settings show next to Settings for too"""
        if not value:
            return [gr.update() for _ in self.used_outputs()]
        model = self.models.resolve(value)
        note = self.note(self.name, model)
        updates = [gr.update(value=note, visible=bool(note)), *(gr.update(visible=model.takes(name)) for name in self.every_model_columns),
                   gr.update(choices=self.edit_choices(value), value=value), gr.update(visible=False)]
        if self.shown_model_ui is not None:
            updates.append(self.shown_model(self.name, model))
        return updates

    # ---- the model whose settings show ----

    def edit_outputs(self) -> list:
        found = [self.heading_ui]
        if self.extra_intro_ui is not None:
            found.append(self.extra_intro_ui)
        return found + list(self.columns.values()) + list(self.values.values()) + [self.use_btn]

    def edit_drawn(self, model: ServiceModel, values: dict[str, Any] | None, used: ServiceModel) -> list:
        """Everything that follows the shown model. *values* None: the controls keep their values"""
        updates = [gr.update(label=self.heading(model))]
        if self.extra_intro_ui is not None:
            updates.append(self.extra_intro(self.name, model))
        updates += [gr.update(visible=model.takes(name)) for name in self.columns]
        updates += [gr.update() if values is None or name not in values else gr.update(value=values[name]) for name in self.values]
        updates.append(gr.update(visible=model.id != used.id, value=self.use_label(self.name, model)))
        return updates

    def on_edit(self, value: str | None, used_value: str | None) -> list:
        """Another model picked next to Settings for: its own values, and the settings it takes"""
        if not value:
            return [gr.update() for _ in self.edit_outputs()]
        try:
            clips = shared_clip_store(voice_clips_folder(self.config.save_folder))
        except Exception:
            clips = None
        values = self.style.show_model(value, clips)
        return self.edit_drawn(self.models.resolve(value), values, self.models.resolve(used_value))

    def wire(self) -> None:
        if hasattr(self.used_ui, "_id"):
            self.used_ui.change(self.on_use, inputs=[self.used_ui], outputs=self.used_outputs(), show_progress="hidden", show_api=False)
        if hasattr(self.edit_ui, "_id"):
            self.edit_ui.change(self.on_edit, inputs=[self.edit_ui, self.used_ui], outputs=self.edit_outputs(), show_progress="hidden",
                                show_api=False)
            self.use_btn.click(lambda value: value, inputs=[self.edit_ui], outputs=[self.used_ui], show_progress="hidden", show_api=False)


class ModelListUI:
    """The Model next to Settings for (*model_ui*, made with render=False), with Add, Remove and Put back Mantella's models small at
    its top right, which change the list both Model lists offer (model_catalog.ServiceModels.add / remove / put_back), and what they
    did under it. Drawn with the Settings for list, wired once the service's ModelSettingsUI exists"""

    def __init__(self, config: ConfigLoader, name: str, visible: bool, model_ui: gr.Dropdown) -> None:
        self.config = config
        self.name = name
        self.models = ServiceModels.of(name)
        with gr.Column(visible=visible, scale=1, elem_classes="tts-model-picker") as self.column:
            with gr.Row(elem_classes="tts-model-buttons"):
                self.add_btn = gr.Button("Add", size="sm", scale=0, min_width=0)
                self.remove_btn = gr.Button("Remove", size="sm", scale=0, min_width=0)
                self.put_back_btn = gr.Button("Put back Mantella's models", size="sm", scale=0, min_width=0,
                                              visible=bool(self.models.changes(config).removed))
            model_ui.render()
            self.status_ui = gr.Markdown("", visible=False, elem_classes="tts-model-status")

    def outputs(self, ui: ModelSettingsUI) -> list:
        return [ui.used_ui, ui.edit_ui, self.put_back_btn, self.status_ui]

    def drawn(self, ui: ModelSettingsUI, status: str, used: str | None = None, shown: str | None = None) -> list:
        """Both Model lists offer the list as it is now. *used*: the model the service speaks with now. *shown*: the one next to Settings for"""
        used_value = used or self.models.value(self.config)
        used_update = gr.update(choices=self.models.choices(used_value, self.config), **({"value": used} if used else {}))
        edit_update = gr.update(choices=ui.edit_choices(shown or used_value), **({"value": shown} if shown else {}))
        return [used_update, edit_update, gr.update(visible=bool(self.models.changes(self.config).removed)),
                gr.update(value=status, visible=bool(status))]

    def on_add(self, ui: ModelSettingsUI, value: str | None) -> list:
        value = str(value or "").strip()
        if not value:
            return self.drawn(ui, f"Type the model's name in Model above first, exactly as {self.name} writes it.")
        model = self.models.add(self.config, value)
        if model is None:
            return self.drawn(ui, f"{self.models.resolve(value).name} is already in the list.")
        return self.drawn(ui, f"Added {model.name} to both Model lists.", shown=model.id)

    def on_remove(self, ui: ModelSettingsUI, value: str | None, used_value: str | None) -> list:
        model = self.models.resolve(value)
        if not self.models.in_list(self.config, model.id):
            return self.drawn(ui, f"{model.name} isn't in the list.")
        if not self.models.remove(self.config, model.id):
            return self.drawn(ui, "Keep at least one model in the list.")
        if model.id == self.models.resolve(used_value).id: # the service spoke with it
            instead = self.models.first_offered(self.config)
            return self.drawn(ui, f"Removed {model.name}. {self.name} speaks with {instead.name} now.", used=instead.id, shown=instead.id)
        return self.drawn(ui, f"Removed {model.name}. Its settings are kept if you add it again.", shown=self.models.resolve(used_value).id)

    def on_put_back(self, ui: ModelSettingsUI) -> list:
        self.models.put_back(self.config)
        return self.drawn(ui, "Mantella's models are back in the list.")

    def wire(self, ui: ModelSettingsUI) -> None:
        outputs = self.outputs(ui)
        self.add_btn.click(lambda value: self.on_add(ui, value), inputs=[ui.edit_ui], outputs=outputs, show_progress="hidden", show_api=False)
        self.remove_btn.click(lambda value, used: self.on_remove(ui, value, used), inputs=[ui.edit_ui, ui.used_ui], outputs=outputs,
                              show_progress="hidden", show_api=False)
        self.put_back_btn.click(lambda: self.on_put_back(ui), outputs=outputs, show_progress="hidden", show_api=False)


def has_key(name: str) -> bool:
    """An online service's key is saved"""
    return any(place.is_file() for place in key_file_places(SERVICES[name][0].KEY_FILE))


def render_online_service_panel(config: ConfigLoader, name: str, sign_in_settings: Callable[[], Any] = lambda: None,
                                outer_fold: gr.Accordion | None = None) -> None:
    """Your key, folded, at the top of Global settings for <service> (*outer_fold*): both open while there is no key, or the service
    can't speak. *sign_in_settings* draws the settings that go with the key (Qwen's Workspace ID) in it"""
    module, service = SERVICES[name]
    with gr.Accordion("Your key", open=not has_key(name), elem_classes="voice-clips-subsection") as key_fold:
        gr.Markdown(f"{name} needs a key, once. Without one, the Backup Voice Service speaks instead.")
        with gr.Accordion("How to get a key", open=False, elem_classes="help-fold"):
            gr.Markdown(
                STEPS[name] + "\n\n"
                f"**Voices on your account.** Each voice type, and each NPC with a voice of their own, is cloned once on your {name} account "
                "when they first speak. Prepare all voices skips online services. Hearing a clip under **Customization per voice type and per NPC** clones it for a moment: "
                f"Mantella deletes it again unless you clone from it.")
        with gr.Row():
            key_ui = gr.Textbox(value="", type="password", label=f"{name} key", max_lines=1)
            save_btn = gr.Button("Save key", size="sm")
            check_btn = gr.Button("Check my key", size="sm")
        status_ui = gr.Markdown("")
        sign_in_settings()

    def why_not() -> str:
        """Why the service can't clone voices right now, or empty"""
        found = problem(name)
        if found:
            return f"**Right now:** {found}"
        if not has_key(name):
            return "No key saved yet."
        if module is qwen:
            if not qwen.has_workspace():
                return "**Right now:** your key is saved, but there is no **Workspace ID**, so the Backup Voice Service speaks. Paste it below."
        return ""

    def on_open():
        """Each time the page is opened: Your key, and Global settings around it, open by themselves when something is wrong"""
        # Qwen's Workspace ID is loaded first, so the Voice Clips buttons can delete voices on Qwen also when Qwen hasn't spoken yet
        use_page_settings()
        found = why_not()
        folds = [key_fold, *([outer_fold] if outer_fold is not None else [])]
        return found, *(gr.update(open=True) if found else gr.update() for _ in folds)

    page = gr.context.Context.root_block
    if page is not None:
        page.load(on_open, outputs=[status_ui, key_fold, *([outer_fold] if outer_fold is not None else [])], show_progress="hidden",
                  show_api=False)

    def use_page_settings():
        """Qwen's Workspace ID as it is on the page now, for Check my key and for deleting voices on Qwen"""
        if module is qwen:
            try:
                qwen.use_workspace(config.definitions.get_config_value_definition(qwen.WORKSPACE_SETTING).value)
            except Exception:
                qwen.use_workspace(getattr(config, qwen.WORKSPACE_SETTING, ""))

    use_page_settings() # when Mantella starts, before Qwen has spoken

    def on_check():
        from src.tts.voice_setups import read_key_file
        key = read_key_file(module.KEY_FILE)
        if not key:
            return "No key saved yet. Paste one above and press **Save key**."
        use_page_settings()
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
        use_page_settings()
        return "", f"Saved in `{saved}`. {service.check_key(key, config.save_folder)}"

    # The box is emptied after saving, and never filled with the saved key, so it isn't shown on screen
    save_btn.click(on_save, inputs=[key_ui], outputs=[key_ui, status_ui], show_api=False)
    check_btn.click(on_check, outputs=[status_ui], show_api=False)
