import json
import typing
import gradio as gr
from typing import Any, Callable, TypeVar, NamedTuple, Dict, TypedDict, Optional

from src.llm.client_base import ClientBase
from src.model_profile_manager import ModelProfileManager
from src.prompt_profile_manager import PromptProfileManager
from src.ui.prompt_highlight import prompt_box_classes
from src.config.config_loader import ConfigLoader
from src.config.types.config_value_path import ConfigValuePath
from src.config.types.config_value_bool import ConfigValueBool
from src.config.types.config_value_float import ConfigValueFloat
from src.config.types.config_value_selection import ConfigValueSelection
from src.config.types.config_value_multi_selection import ConfigValueMultiSelection
from src.config.types.config_value_string import ConfigValueString
from src.config.config_value_constraint import ConfigValueConstraintResult
from src.config.types.config_value import ConfigValue, ConfigValueTag, condition_holds, shown_when_conditions
from src.config.types.config_value_group import ConfigValueGroup
from src.config.types.config_value_int import ConfigValueInt
from src.config.types.config_value_visitor import ConfigValueVisitor
from src.config.definitions.conversation_definitions import (AT_REPLY_LIMIT, EXTRA_ROUNDS, REPLIES_IN_BETWEEN, SHARED, TALK_KINDS, WHO_MUST_SPEAK,
                                                             greyed_choices, talk_key, who_still_speaks_now)
from src.config.definitions.tts_definitions import (HEAR_IT_PART, NPC_VOICES_PART, ONLINE_SERVICE_GROUPS, PIPER_PART, POCKET_TTS_GROUP,
                                                    SERVICE_PARTS, SERVICE_VOLUMES, SPEED_PART, VOICE_EFFECTS_GROUP, VOICE_SETUPS_PART,
                                                    WORD_LISTS_GROUP, TTSDefinitions, every_npc_part, service_part_id, sound_part_id)
from src.tts.model_catalog import ServiceModel, ServiceModels
from src.tts.registry import voice_services
from src.tts.voice_style import EXTRA_REQUEST_LABEL, EXTRA_REQUESTS, ServiceStyle
from src.ui.voice_ui import QUICKEST_WAY, render_voice_setups_editor, render_voice_test, render_word_lists_editor
from src.ui.voice_clips_ui import pocket_tts_language_note, pocket_tts_signed_in, render_pocket_tts_panel, render_voice_clips_editor
from src.ui.online_voices_ui import (HOW_MODELS_WORK, SERVICES as ONLINE_SERVICES, ModelListUI, ModelSettingsUI, has_key,
                                     render_online_service_panel)
from src.ui.voice_effects_ui import render_voice_effects_editor
from src.config.definitions.stt_definitions import (GREYED_UNLESS_ON, SERVICE_DESCRIPTION as STT_SERVICE_DESCRIPTION, SERVICE_PART as STT_SERVICE_PART,
                                                    TALKING_DESCRIPTION as STT_TALKING_DESCRIPTION, TALKING_PART as STT_TALKING_PART)
from src.stt_backends.model_lists import speech_models
from src.stt_backends.presets import ONLINE_PRESETS, takes_name_list
from src.ui.stt_ui import render_key_box, render_stt_test
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.SETTINGS_PAGE)
models_log = get_topic_logger(LogTopic.MODELS_AND_PROFILES)

# Global reference to the game manager for character data reload functionality
_game_manager_ref = None

def set_game_manager_reference(game_manager):
    """Set the global game manager reference for UI access"""
    global _game_manager_ref
    _game_manager_ref = game_manager

def part_of_service(value: str | None) -> str:
    """The choice under Settings for that shows this TTS Service's settings: Your voice setups for a setup, Piper for one that's gone"""
    info = voice_services.find(value)
    if info is not None and info.is_setup:
        return VOICE_SETUPS_PART
    return info.name if info is not None and info.name in SERVICE_PARTS else PIPER_PART

def two_by_two(settings: list[ConfigValue]) -> list[list[ConfigValue]]:
    """Settings in rows of two, kept in their order: tick boxes side by side, the others side by side, and a tick box and another
    setting left over share a row. E.g. Similarity, Audio tags, Volume -> [Similarity, Volume], [Audio tags]"""
    ticks = [cf for cf in settings if isinstance(cf, ConfigValueBool)]
    others = [cf for cf in settings if not isinstance(cf, ConfigValueBool)]
    rows = [group[i:i + 2] for group in (ticks, others) for i in range(0, len(group), 2)]
    alone = [row for row in rows if len(row) == 1]
    if len(alone) == 2:
        rows.remove(alone[1])
        alone[0].append(alone[1][0])
        alone[0].sort(key=settings.index)
    return sorted(rows, key=lambda row: settings.index(row[0]))

class SettingUIComponents(NamedTuple):
    input_ui: Any
    error_message: gr.Markdown

class SettingConfig(NamedTuple):
    config_value: ConfigValue
    create_input: Callable[[ConfigValue], Any]
    update_on_change: bool
    update_on_submit: bool
    update_on_blur: bool
    additional_buttons: list[tuple[str, Callable[[], Any]]]

# Model dropdowns where no selection (empty value) means "use the active Summary model".
SUMMARY_FALLBACK_MODEL_IDS = {"thoughts_model", "reflection_model"}

class ModelConfig(TypedDict):
    dependent_config: str  # The config value this model depends on (e.g., "llm_api")
    model_list_getter: Callable[[str, str, str, bool], Any]  # Function to get model list based on service, secret_key_file, default_model, is_vision

class SettingsUIConstructor(ConfigValueVisitor):
    def __init__(self, config_loader: ConfigLoader | None = None) -> None:
        super().__init__()
        self.__identifier_to_config_value: dict[str, ConfigValue] = {}
        self.__config_value_to_ui_element: dict[ConfigValue, Any] = {}
        self.__pending_shared_setting: SettingConfig | None = None
        self.__row_groups: dict[str, list[SettingConfig]] = {}  # Track settings by row_group
        self.__last_row_group: str | None = None  # Track the last row group being built
        self.__model_dependencies: list[tuple[str, str]] = []  # (model_id, service_id) pairs
        self.__profile_manager: ModelProfileManager | None = None
        self.__config_loader = config_loader
        self.__prompt_profile_manager: PromptProfileManager | None = None
        self.__picked_option_description_ui: dict[str, gr.Markdown] = {} # By identifier: the line under a setting's choices
        self.__inside_setting: dict[str, Callable[[], None]] = {} # By identifier: draws more inside that setting's box, under its controls
        self.__second_in_row = False # The setting drawn now is the right one of two side by side: its help opens to the left

    @property
    def config_value_to_ui_element(self) -> dict[ConfigValue, gr.Column]:
        return self.__config_value_to_ui_element
    
    def get_profile_manager(self) -> ModelProfileManager:
        """Lazily initialize and return the profile manager"""
        if self.__profile_manager is None:
            self.__profile_manager = ModelProfileManager()
        return self.__profile_manager

    def get_prompt_profile_manager(self) -> PromptProfileManager:
        if self.__config_loader is not None:
            return self.__config_loader.prompt_profile_manager
        if self.__prompt_profile_manager is None:
            self.__prompt_profile_manager = PromptProfileManager()
        return self.__prompt_profile_manager
    

    
    def setup_model_dependencies(self) -> None:
        """Set up automatic model list updates when dependent service changes.
        Call this after all UI elements have been created."""
        special_handlers: Dict[str, ModelConfig] = {
            "model": {
                "dependent_config": "llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "vision_model": {
                "dependent_config": "vision_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "multi_npc_model": {
                "dependent_config": "multi_npc_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "game_master_model": {
                "dependent_config": "game_master_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "game_master_decision_model": {
                "dependent_config": "game_master_decision_service",
                "model_list_getter": ClientBase.get_decision_model_list,
            },
            "summary_model": {
                "dependent_config": "summary_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "thoughts_model": {
                "dependent_config": "thoughts_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "reflection_model": {
                "dependent_config": "reflection_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "npc_event_model": {
                "dependent_config": "npc_event_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "fallback_model": {
                "dependent_config": "fallback_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "advanced_actions_model": {
                "dependent_config": "advanced_actions_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "profile_selected_model": {
                "dependent_config": "profile_selected_service",
                "model_list_getter": ClientBase.get_model_list,
            }
        }
        
        for model_id, service_id in self.__model_dependencies:
            model_config = self.__identifier_to_config_value.get(model_id)
            service_config = self.__identifier_to_config_value.get(service_id)
            
            if model_config and service_config:
                model_ui = self.__config_value_to_ui_element.get(model_config)
                service_ui = self.__config_value_to_ui_element.get(service_config)
                
                if model_ui and service_ui and hasattr(service_ui, "_id"):
                    handler = special_handlers.get(model_id)
                    if handler:
                        def create_update_function(model_cfg, handler_cfg):
                            def update_model_list_for_service() -> gr.Dropdown:
                                return self.__create_model_dropdown(model_cfg, handler_cfg)
                            return update_model_list_for_service
                        
                        def create_change_handler(model_cfg, service_cfg, update_func):
                            def on_service_change(new_service_value):
                                # Update the service config value
                                service_cfg.value = new_service_value
                                # Return updated model dropdown
                                return update_func()
                            return on_service_change
                        
                        update_func = create_update_function(model_config, handler)
                        change_handler = create_change_handler(model_config, service_config, update_func)
                        
                        # Special handling for profile service changes
                        if model_config.identifier == "profile_selected_model":
                            parameters_config = self.__identifier_to_config_value.get("profile_parameters")
                            parameters_ui = self.__config_value_to_ui_element.get(parameters_config) if parameters_config else None
                            
                            if parameters_ui:
                                def profile_service_change_handler(new_service_value):
                                    # Update the service config value
                                    service_config.value = new_service_value
                                    
                                    # Clear parameters when service changes
                                    parameters_config.value = ""
                                    log.debug(f"Service changed to {new_service_value}, cleared profile parameters")
                                    
                                    # Return updated model dropdown and cleared parameters
                                    updated_dropdown = update_func()
                                    return updated_dropdown, ""
                                
                                service_ui.change(
                                    profile_service_change_handler,
                                    inputs=service_ui,
                                    outputs=[model_ui, parameters_ui],
                                    show_progress="hidden"
                                )
                            else:
                                service_ui.change(
                                    change_handler,
                                    inputs=service_ui,
                                    outputs=model_ui,
                                    show_progress="hidden"
                                )
                        else:
                            service_ui.change(
                                change_handler,
                                inputs=service_ui,
                                outputs=model_ui,
                                show_progress="hidden"
                            )
        
        # Set up automatic profile loading for model profiles  
        # This will be handled by the standard model dependency system above, 
        # we just need to add a custom handler for when the profile model changes
        profile_model_config = self.__identifier_to_config_value.get("profile_selected_model")
        profile_service_config = self.__identifier_to_config_value.get("profile_selected_service")
        
        if profile_model_config and profile_service_config:
            profile_model_ui = self.__config_value_to_ui_element.get(profile_model_config)
            parameters_config = self.__identifier_to_config_value.get("profile_parameters")
            parameters_ui = self.__config_value_to_ui_element.get(parameters_config) if parameters_config else None
            
            if profile_model_ui and parameters_ui:
                def load_profile_on_model_change(new_model_value):
                    """Load profile when model changes"""
                    try:
                        # Update the model config value
                        profile_model_config.value = new_model_value
                        
                        # Load profile if it exists
                        service_value = profile_service_config.value
                        if service_value and new_model_value:
                            profile = self.get_profile_manager().get_profile_for_model(service_value, new_model_value)
                            
                            if profile:
                                import json
                                parameters_json = json.dumps(profile.parameters, indent=4)
                                parameters_config.value = parameters_json
                                models_log.info(f"Loaded profile for {new_model_value}")
                                return parameters_json
                            else:
                                # Clear parameters if no profile exists
                                parameters_config.value = ""
                                models_log.debug(f"No profile found for {new_model_value}")
                                return ""
                        else:
                            return ""
                    except Exception as e:
                        models_log.error(f"Error loading profile on model change: {e}")
                        return ""
                
                # Set up profile loading when model changes
                profile_model_ui.change(
                    load_profile_on_model_change,
                    inputs=profile_model_ui,
                    outputs=parameters_ui,
                    show_progress="hidden"
                )
    
    def __create_model_dropdown(self, model_config: ConfigValue, handler: ModelConfig) -> gr.Dropdown:
        """Create a model dropdown for the given config and handler"""
        service: str = self.__identifier_to_config_value[handler["dependent_config"]].value
        # Choose appropriate secret key file based on service and model type
        if model_config.identifier == 'vision_model':
            secret_key_file = 'IMAGE_SECRET_KEY.txt'
        else:
            from src.llm.key_file_resolver import key_file_resolver
            # Use the first (primary) key file from the resolver
            key_files = key_file_resolver.get_key_files_for_service(service, 'GPT_SECRET_KEY.txt')
            secret_key_file = key_files[0] if key_files else 'GPT_SECRET_KEY.txt'
        default_model = 'meta-llama/llama-3.2-11b-vision-instruct:free' if model_config.identifier == 'vision_model' else 'google/gemma-2-9b-it:free'
        is_vision = True if model_config.identifier == 'vision_model' else False
        model_list = handler["model_list_getter"](service, secret_key_file, default_model, is_vision)
        selected_model = model_config.value
        # Leave an unselected summary-fallback model empty instead of replacing it with the default model
        if model_config.identifier in SUMMARY_FALLBACK_MODEL_IDS and not selected_model:
            selected_model = None
        elif not model_list.is_model_in_list(selected_model):
            selected_model = model_list.default_model
            # Update the config value to the default model
            model_config.value = selected_model
            
        return gr.update(
            value=selected_model,
            choices=model_list.available_models,
            allow_custom_value=model_list.allows_manual_model_input
        )
    
    def __tooltip_content_class(self, config_value: ConfigValue, is_second_setting: bool) -> str:
        """Bools and the right column of a pair keep the popover anchored to the ? on the right."""
        use_right_aligned_tooltip = is_second_setting or isinstance(config_value, ConfigValueBool)
        return "tooltip-content-right" if use_right_aligned_tooltip else "tooltip-content-left"

    def __create_tooltip(self, config_value: ConfigValue, is_second_setting: bool = False) -> str:
        """Creates the tooltip HTML for a config value"""
        constraints_html = (
            f'<p class="constraints">' +
            '<br>'.join(c.description for c in config_value.constraints) +
            '</p>' if config_value.constraints else ''
        )
        tooltip_content = self.__tooltip_content_class(config_value, is_second_setting)
        description_html = (config_value.description or "").replace("\n", "<br>")

        return f"""
        <div class="tooltip-container" role="tooltip" aria-label="{config_value.name} help">
            <span class="tooltip-icon" tabindex="0">?</span>
            <div class="{tooltip_content}">
                <p>{description_html}</p>
                {constraints_html}
            </div>
        </div>
        """

    def __create_buttons(self, config_value: ConfigValue, 
                        create_input_component: Callable[[ConfigValue], Any],
                        input_ui: Any,
                        error_message: gr.Markdown,
                        additional_buttons: list[tuple[str, Callable[[], Any]]]) -> None:
        """Creates the buttons for a setting"""
        with gr.Row(elem_classes="button-container"):
            for btn_label, btn_action in additional_buttons:
                button = gr.Button(btn_label, variant="primary", size='sm')
                # Ensure the button click event is properly connected
                if hasattr(button, "_id"):
                    button.click(btn_action, outputs=input_ui)
                else:
                    # Fallback: try connecting without checking _id
                    try:
                        button.click(btn_action, outputs=input_ui)
                    except Exception as e:
                        log.error(f"Failed to connect button '{btn_label}' click event: {e}")
            if not additional_buttons:
                reset_button = gr.Button("Default", size='sm')
                if hasattr(reset_button, "_id"):
                    reset_button.click(
                        lambda: self.__on_reset_click(config_value, create_input_component),
                        outputs = [input_ui, error_message]
                    )

    def __on_reset_click(self, config_value: ConfigValue, create_input_component: Callable[[ConfigValue], Any]):
        error_message = self.__on_change(config_value, config_value.default_value)
        return [gr.update(value=config_value.default_value), error_message]

    def __setup_event_handlers(self, config_value: ConfigValue, 
                             input_ui: Any,
                             error_message: gr.Markdown,
                             update_on_change: bool,
                             update_on_submit: bool,
                             update_on_blur: bool) -> None:
        """Sets up the event handlers for an input component"""
        if hasattr(input_ui, "_id") and isinstance(config_value, ConfigValueMultiSelection) and ConfigValueTag.tick_boxes in config_value.tags:
            # Tick boxes: some can't be ticked together, so a click can untick another. Only a click saves, not the update that follows it
            def on_ticks(ticked: list[str] | None):
                resolved = config_value.resolve_ticks(list(config_value.value), list(ticked or []))
                return gr.update(value=resolved), self.__on_change(config_value, resolved)
            input_ui.input(on_ticks, input_ui, [input_ui, error_message], show_progress="hidden")
            return
        if hasattr(input_ui, "_id"):
            if update_on_change:
                input_ui.change(lambda x: self.__on_change(config_value, x), input_ui, error_message, show_progress="hidden")
            if update_on_submit:
                input_ui.submit(lambda x: self.__on_change(config_value, x), input_ui, error_message, show_progress="hidden")
            if update_on_blur:
                input_ui.blur(lambda x: self.__on_change(config_value, x), input_ui, error_message, show_progress="hidden")

    def __create_setting_components(self, setting: SettingConfig, is_second_setting: bool = False) -> SettingUIComponents:
        """Creates the UI components for a single setting"""
        if isinstance(setting.config_value, ConfigValueBool):
            # dynamically change width of bool title depending on whether the setting shares a row with another setting
            elem_class = "setting-bool-container-wide" if ConfigValueTag.share_row in setting.config_value.tags else "setting-bool-container-narrow"
            with gr.Row(elem_classes=elem_class):
                input_ui = setting.create_input(setting.config_value)
                gr.HTML(self.__create_tooltip(setting.config_value, is_second_setting))
            if setting.config_value.identifier == "enable_bio_section_filter":
                self.__embed_bio_sections_to_exclude()
                with gr.Column(elem_classes="bio-section-filter-memory-toggle"):
                    self.__embed_bool_toggle("enable_bio_section_filter_for_memory", is_second_setting=True)
        else:
            self.__construct_name_description_constraints(setting.config_value, is_second_setting)
            with gr.Row(equal_height=True, elem_classes="setting-controls"):
                input_ui = setting.create_input(setting.config_value)
                input_ui.scale = 999
                self.__create_buttons(setting.config_value, setting.create_input,
                                    input_ui, gr.Markdown(None), setting.additional_buttons)
            if isinstance(setting.config_value, ConfigValueSelection) and setting.config_value.option_descriptions:
                self.__embed_picked_option_description(setting.config_value, input_ui)
            if setting.config_value.identifier == "save_summary_now":
                self.__embed_save_summary_now_toggles()
            if setting.config_value.identifier in self.__inside_setting:
                self.__inside_setting.pop(setting.config_value.identifier)()

        error_message = self.__construct_initial_error_message(setting.config_value)
        
        self.__setup_event_handlers(
            setting.config_value, input_ui, error_message,
            setting.update_on_change, setting.update_on_submit, setting.update_on_blur
        )
        if isinstance(setting.config_value, ConfigValueString):
            self.__embed_prompt_preview(setting.config_value, input_ui)

        return SettingUIComponents(input_ui, error_message)

    def __embed_picked_option_description(self, config_value: ConfigValueSelection, input_ui: Any) -> None:
        """Under a dropdown: what the picked option does, updated when another option is picked"""
        descriptions = config_value.option_descriptions
        description_ui = gr.Markdown(descriptions.get(config_value.value, ""), elem_classes="picked-option-description")
        self.__picked_option_description_ui[config_value.identifier] = description_ui
        if config_value.identifier in {talk_key(kind, AT_REPLY_LIMIT) for kind in [SHARED] + TALK_KINDS}:
            return # Also depends on the ticked boxes, so __grey_out_talk_settings updates it
        if hasattr(input_ui, "_id"):
            # Looked up in the browser: no trip to Mantella, so the text doesn't fade or jump while it changes
            input_ui.change(None, input_ui, description_ui, js=f"(picked) => ({json.dumps(descriptions)})[picked] || ''")

    def __embed_save_summary_now_toggles(self) -> None:
        """Render the Save Summary Now extra toggles inside the same settings panel."""
        with gr.Row(elem_classes="save-summary-now-toggles"):
            self.__embed_bool_toggle("save_summary_now_also_save_inner_thoughts", is_second_setting=False)
            self.__embed_bool_toggle("save_summary_now_also_save_personal_reflection", is_second_setting=True)

    def __embed_bio_sections_to_exclude(self) -> None:
        """Render the exclude-list field inside the bio section filter panel."""
        if self.__config_loader is None:
            return
        try:
            config_value = self.__config_loader.definitions.get_config_value_definition("bio_sections_to_exclude")
        except Exception:
            return
        if not isinstance(config_value, ConfigValueString):
            return

        def create_input_component(raw_config_value: ConfigValue) -> gr.Text:
            config_value = typing.cast(ConfigValueString, raw_config_value)
            return gr.Text(
                value=config_value.value,
                show_label=False,
                container=False,
                max_lines=1,
                placeholder="Personal History, Race",
            )

        with gr.Column(elem_classes="bio-section-filter-exclude"):
            setting = SettingConfig(
                config_value,
                create_input_component,
                False,
                True,
                True,
                [],
            )
            components = self.__create_setting_components(setting, is_second_setting=True)
            self.__identifier_to_config_value[config_value.identifier] = config_value
            self.__config_value_to_ui_element[config_value] = components.input_ui

    def __embed_bool_toggle(self, identifier: str, is_second_setting: bool) -> None:
        if self.__config_loader is None:
            return
        try:
            config_value = self.__config_loader.definitions.get_config_value_definition(identifier)
        except Exception:
            return
        if not isinstance(config_value, ConfigValueBool):
            return
        with gr.Column():
            error_message = self.__construct_initial_error_message(config_value)
            with gr.Row(elem_classes="setting-bool-container-wide"):
                checkbox = gr.Checkbox(
                    label=config_value.name,
                    value=config_value.value,
                    show_label=False,
                    container=False,
                    elem_classes="checkboxelement",
                )
                gr.HTML(self.__create_tooltip(config_value, is_second_setting))
            self.__setup_event_handlers(config_value, checkbox, error_message, True, False, False)
            self.__identifier_to_config_value[config_value.identifier] = config_value
            self.__config_value_to_ui_element[config_value] = checkbox

    def __create_paired_settings(self, setting1: SettingConfig, setting2: SettingConfig):
        """Creates two settings side by side in the same row"""
        with gr.Row():
            is_second_setting = False
            for setting in [setting1, setting2]:
                with gr.Column(variant="panel", scale=1):
                    components = self.__create_setting_components(setting, is_second_setting)
                    is_second_setting = True
                    self.__identifier_to_config_value[setting.config_value.identifier] = setting.config_value
                    self.__config_value_to_ui_element[setting.config_value] = components.input_ui

    def __create_single_setting(self, setting: SettingConfig):
        """Creates a single setting taking up the full row"""
        with gr.Column(variant="panel", scale=1):
            components = self.__create_setting_components(setting, self.__second_in_row)
            self.__identifier_to_config_value[setting.config_value.identifier] = setting.config_value
            self.__config_value_to_ui_element[setting.config_value] = components.input_ui

    def __create_config_value_ui_element(self, config_value: ConfigValue, 
                                       create_input_component: Callable[[ConfigValue], Any],
                                       update_on_change: bool = True, 
                                       update_on_submit: bool = False,
                                       update_on_blur: bool = False,
                                       additional_buttons: list[tuple[str, Callable[[], Any]]] = []):
        current_setting = SettingConfig(
            config_value, create_input_component,
            update_on_change, update_on_submit, update_on_blur,
            additional_buttons
        )

        # Handle row grouping
        if config_value.row_group is not None:
            # Check if we need to render a different pending row group first
            if self.__last_row_group is not None and self.__last_row_group != config_value.row_group:
                self.__render_row_group(self.__last_row_group)
            
            # Add to the current row group
            if config_value.row_group not in self.__row_groups:
                self.__row_groups[config_value.row_group] = []
            self.__row_groups[config_value.row_group].append(current_setting)
            self.__last_row_group = config_value.row_group
            return
        
        # If we have a pending row group, render it now before processing this non-grouped item
        if self.__last_row_group is not None:
            self.__render_row_group(self.__last_row_group)
            self.__last_row_group = None
        
        # Legacy share_row tag support (for backwards compatibility)
        if ConfigValueTag.share_row in config_value.tags:
            if self.__pending_shared_setting is not None:
                self.__create_paired_settings(self.__pending_shared_setting, current_setting)
                self.__pending_shared_setting = None
            else:
                self.__pending_shared_setting = current_setting
            return

        if self.__pending_shared_setting is not None:
            self.__create_single_setting(self.__pending_shared_setting)
            self.__pending_shared_setting = None
            
        self.__create_single_setting(current_setting)

    T = TypeVar('T')
    def __on_change(self, config_value: ConfigValue[T], new_value: T) -> Any:
        result: ConfigValueConstraintResult = config_value.does_value_cause_error(new_value)
        if result.is_success:
            if config_value.value != new_value:
                config_value.value = new_value
                log.debug(f'{config_value.name} set to {config_value.value}')
            return self.__update_error_message_panel('', is_visible=False)
        else:
            return self.__update_error_message_panel(result.error_message, is_visible=True)

    def __construct_error_message_panel(self, message: str, is_visible: bool) -> gr.Markdown:
        return gr.Markdown(value=message, visible=is_visible, elem_classes="constraint-violation")

    def __update_error_message_panel(self, message: str, is_visible: bool) -> Any:
        return gr.update(value=message, visible=is_visible)

    def _construct_badges(self, config_value: ConfigValue):
        if len(config_value.tags) > 0:
            with gr.Row():
                for tag in config_value.tags:
                    gr.HTML(f"<b>{str(tag).upper()}</b>", elem_classes=["badge",f"badge-{tag}"])
                gr.Column()

    def __construct_name_description_constraints(self, config_value: ConfigValue, is_second_setting: bool = False):
        with gr.Row():
            description_html = (config_value.description or "").replace("\n", "<br>")
            tooltip_content = self.__tooltip_content_class(config_value, is_second_setting)
            tooltip_html = f"""
            <div style="display: flex; align-items: center;">
                <h3 style="margin: 0; font-size: 1.25em;">{config_value.name}</h3>
                <div class="tooltip-container" role="tooltip" aria-label="{config_value.name} help">
                    <span class="tooltip-icon">?</span>
                    <div class="{tooltip_content}">
                        <p>{description_html}</p>
                        {'<p>' + '<br>'.join(c.description for c in config_value.constraints if c.description is not None) + '</p>' if config_value.constraints else ''}
                    </div>
                </div>
            </div>
            """
            gr.HTML(tooltip_html)
        
    def __construct_initial_error_message(self, config_value: ConfigValue) -> gr.Markdown:
        result: ConfigValueConstraintResult = config_value.does_value_cause_error(config_value.value)
        return self.__construct_error_message_panel(result.error_message, is_visible=not result.is_success)

    def __render_row_group(self, group_id: str):
        """Render all settings in a row group"""
        if group_id in self.__row_groups and len(self.__row_groups[group_id]) > 0:
            settings = self.__row_groups[group_id]
            with gr.Row():
                for i, setting in enumerate(settings):
                    is_second_setting = i > 0
                    with gr.Column(variant="panel", scale=1):
                        components = self.__create_setting_components(setting, is_second_setting)
                        self.__identifier_to_config_value[setting.config_value.identifier] = setting.config_value
                        self.__config_value_to_ui_element[setting.config_value] = components.input_ui
            # Clear the group after rendering
            del self.__row_groups[group_id]

    def render_model_profile_editor(self, config_value: ConfigValueGroup) -> None:
        """Render the fixed ten-slot model profile editor."""
        import json
        from src.llm.key_file_resolver import key_file_resolver

        values_by_id = {value.identifier: value for value in config_value.value}
        selected_service = values_by_id["profile_selected_service"].value
        selected_model = values_by_id["profile_selected_model"].value

        def get_models_for_service(service: str, preferred_model: str | None = None) -> tuple[str, Any]:
            key_files = key_file_resolver.get_key_files_for_service(service, "GPT_SECRET_KEY.txt")
            secret_key_file = key_files[0] if key_files else "GPT_SECRET_KEY.txt"
            model_list = ClientBase.get_model_list(
                service,
                secret_key_file,
                "google/gemma-2-9b-it:free",
                False
            )
            model = preferred_model if preferred_model and model_list.is_model_in_list(preferred_model) else model_list.default_model
            return model, model_list

        def make_model_dropdown(model: str, model_list: Any) -> gr.Dropdown:
            return gr.Dropdown(
                value=model,
                choices=model_list.available_models,
                multiselect=False,
                allow_custom_value=model_list.allows_manual_model_input,
                label="Model"
            )

        def update_model_dropdown(model: str, model_list: Any) -> Any:
            return gr.update(
                value=model,
                choices=model_list.available_models,
                allow_custom_value=model_list.allows_manual_model_input
            )

        def get_slot_values(service: str, model: str) -> list[Any]:
            slots = self.get_profile_manager().get_profile_slots(service, model)
            result: list[Any] = []
            for index, profile in enumerate(slots):
                result.append(profile.enabled if profile else index == 0)
                result.append(profile.weight if profile else 1.0)
                result.append(json.dumps(profile.parameters, indent=4) if profile else "")
            return result

        selected_model, initial_model_list = get_models_for_service(selected_service, selected_model)
        with gr.Row():
            service_ui = gr.Dropdown(
                value=selected_service,
                choices=["OpenRouter", "OpenAI", "NanoGPT"],
                allow_custom_value=False,
                label="Service"
            )
            model_ui = make_model_dropdown(selected_model, initial_model_list)

        slot_inputs: list[Any] = []
        initial_slots = self.get_profile_manager().get_profile_slots(selected_service, selected_model)
        for index in range(10):
            profile = initial_slots[index]
            with gr.Accordion(label=f"Profile {index + 1}", open=index == 0):
                enabled_ui = gr.Checkbox(
                    value=profile.enabled if profile else index == 0,
                    label="Enabled for random selection"
                )
                weight_ui = gr.Number(
                    value=profile.weight if profile else 1.0,
                    label="Selection weight",
                    minimum=0,
                    step=0.1
                )
                parameters_ui = gr.Textbox(
                    value=json.dumps(profile.parameters, indent=4) if profile else "",
                    label="Parameters (JSON)",
                    lines=8,
                    max_lines=20,
                    placeholder='{\n  "temperature": 0.8,\n  "max_tokens": 250\n}'
                )
                slot_inputs.extend([enabled_ui, weight_ui, parameters_ui])

        save_ui = gr.Button("Save Profiles", variant="primary")
        status_ui = gr.Markdown()

        def load_service(service: str):
            model, model_list = get_models_for_service(service)
            values_by_id["profile_selected_service"].value = service
            values_by_id["profile_selected_model"].value = model
            return [update_model_dropdown(model, model_list)] + get_slot_values(service, model)

        def load_model(service: str, model: str):
            values_by_id["profile_selected_service"].value = service
            values_by_id["profile_selected_model"].value = model
            return get_slot_values(service, model)

        def save_profiles(service: str, model: str, *slot_values: Any) -> str:
            import math

            values_by_id["profile_selected_service"].value = service
            values_by_id["profile_selected_model"].value = model
            slots: list[Optional[dict[str, Any]]] = []
            enabled_slots: list[bool] = []
            weight_slots: list[float] = []
            for index in range(0, len(slot_values), 3):
                enabled_slots.append(bool(slot_values[index]))
                try:
                    weight = float(slot_values[index + 1])
                except (TypeError, ValueError):
                    return f"Profile {index // 3 + 1} weight must be a number."
                if not math.isfinite(weight) or weight < 0:
                    return f"Profile {index // 3 + 1} weight must be a non-negative number."
                weight_slots.append(weight)
                raw_parameters = str(slot_values[index + 2] or "").strip()
                if not raw_parameters:
                    slots.append(None)
                    continue
                try:
                    parameters = json.loads(raw_parameters)
                except json.JSONDecodeError as e:
                    return f"Profile {index // 3 + 1} has invalid JSON: {e}"
                if not isinstance(parameters, dict):
                    return f"Profile {index // 3 + 1} parameters must be a JSON object."
                slots.append(parameters)

            success = self.get_profile_manager().save_profile_slots(
                service,
                model,
                slots,
                enabled_slots,
                weight_slots
            )
            return "Profiles saved." if success else "Profiles could not be saved. Check the log."

        service_ui.change(
            load_service,
            inputs=service_ui,
            outputs=[model_ui] + slot_inputs,
            show_progress="hidden"
        )
        model_ui.change(
            load_model,
            inputs=[service_ui, model_ui],
            outputs=slot_inputs,
            show_progress="hidden"
        )
        save_ui.click(
            save_profiles,
            inputs=[service_ui, model_ui] + slot_inputs,
            outputs=status_ui
        )

    def render_prompt_profile_editor(self, config_value: ConfigValueGroup) -> None:
        """Render named prompt profiles with separate edit and activate dropdowns."""
        manager = self.get_prompt_profile_manager()
        none_label = PromptProfileManager.NONE_ACTIVE_LABEL
        type_choices = PromptProfileManager.display_names()
        initial_type_display = type_choices[0]
        initial_type = PromptProfileManager.identifier_for_display(initial_type_display) or "skyrim_prompt"
        initial_names = manager.get_profile_names(initial_type)
        initial_edit = manager.get_default_edit_name(initial_type)
        initial_text = manager.get_profile_text(initial_type, initial_edit) if initial_edit else ""
        initial_active = manager.get_active_name(initial_type) or none_label

        def type_id(type_display: str) -> str:
            return PromptProfileManager.identifier_for_display(type_display) or "skyrim_prompt"

        def edit_dropdown(prompt_type: str, selected: str | None) -> Any:
            names = manager.get_profile_names(prompt_type)
            value = selected if selected in names else None
            return gr.update(choices=names, value=value)

        def active_dropdown(prompt_type: str) -> Any:
            names = manager.get_profile_names(prompt_type)
            active = manager.get_active_name(prompt_type) or none_label
            return gr.update(choices=[none_label] + names, value=active)

        def error_panel(message: str, visible: bool) -> Any:
            return self.__update_error_message_panel(message, is_visible=visible)

        def validation_result(prompt_type: str, text: str) -> tuple[str, bool]:
            result = manager.validate_text(prompt_type, text or "")
            if result.is_success:
                return "", False
            return result.error_message, True

        def validation_panel(prompt_type: str, text: str) -> Any:
            message, visible = validation_result(prompt_type, text)
            return error_panel(message, visible)

        def notify_if_needed(changed: bool) -> None:
            if changed and self.__config_loader is not None:
                self.__config_loader.notify_prompt_profiles_changed()

        if config_value.description:
            gr.Markdown(config_value.description)

        initial_section_names = manager.get_section_names()
        initial_section = initial_section_names[0] if initial_section_names else None
        initial_section_text = manager.get_section_text(initial_section) if initial_section else ""

        def section_dropdown(selected: str | None) -> Any:
            names = manager.get_section_names()
            value = selected if selected in names else (names[0] if names else None)
            return gr.update(choices=names, value=value)

        def section_validation_panel(text: str) -> Any:
            result = manager.validate_section_text(text or "")
            if result.is_success:
                return error_panel("", False)
            return error_panel(result.error_message, True)

        type_ui = gr.Dropdown(
            choices=type_choices,
            value=initial_type_display,
            label="Prompt type",
            allow_custom_value=False
        )
        with gr.Accordion("About this prompt", open=False):
            about_ui = gr.Markdown(manager.about_markdown(initial_type))
        with gr.Accordion("Variables", open=False):
            variables_ui = gr.Markdown(manager.variables_markdown(initial_type))
        with gr.Row():
            edit_ui = gr.Dropdown(
                choices=initial_names,
                value=initial_edit,
                label="Edit profile",
                allow_custom_value=False
            )
            save_btn = gr.Button("Save", variant="primary", size="sm")
            rename_btn = gr.Button("Rename", size="sm")
            delete_btn = gr.Button("Delete", variant="secondary", size="sm")
        name_ui = gr.Text(value=initial_edit or "", label="Name", max_lines=1)
        text_ui = gr.Textbox(
            value=initial_text or "",
            label="Prompt text",
            lines=16,
            max_lines=16,
            elem_classes=["multiline-textbox", *prompt_box_classes(initial_type)]
        )
        load_prompts_tab_btn = gr.Button("Load from Prompts tab", size="sm")
        initial_situations = PromptProfileManager.preview_situation_labels(initial_type)
        with gr.Accordion("Preview: the text sent in one situation", open=False, visible=bool(initial_situations)) as preview_accordion_ui:
            preview_situation_ui = gr.Dropdown(
                choices=initial_situations,
                value=initial_situations[0] if initial_situations else None,
                label="Show as",
                allow_custom_value=False
            )
            preview_ui = gr.Textbox(
                value=manager.preview_text(initial_type, initial_text or "", initial_situations[0]) if initial_situations else "",
                label="Text sent in this situation (variables are filled in when it is sent)",
                lines=12,
                max_lines=12,
                interactive=False,
                elem_classes="multiline-textbox"
            )
        initial_error_message, initial_error_visible = validation_result(initial_type, initial_text or "")
        error_ui = self.__construct_error_message_panel(initial_error_message, is_visible=initial_error_visible)
        active_ui = gr.Dropdown(
            choices=[none_label] + initial_names,
            value=initial_active,
            label="Active profile",
            allow_custom_value=False
        )
        status_ui = gr.Markdown("")

        # Shared sections: their own box below the profiles, since a section is used by profiles of every prompt type
        with gr.Column(elem_classes="prompt-editor-subsection"):
            gr.Markdown("### Shared sections")
            with gr.Accordion("How shared sections work", open=False):
                gr.Markdown(
                    "Write a block of prompt text once, then reuse it in any profile. "
                    "In a profile's prompt text, insert the section by name, for example `{roleplay_section}`. "
                    "Saving the section updates every active profile that uses it. The next request sends the new text.\n\n"
                    "- The name can use letters, digits, and underscores, such as `roleplay_section`.\n"
                    "- Inside the section, built-in variables such as `{name}` and `{player_name}` are filled in when the prompt is sent.\n"
                    "- A section cannot contain another shared section.\n"
                    "- If the expanded section uses a variable this prompt type does not allow, saving that profile is rejected."
                )
            with gr.Row():
                section_edit_ui = gr.Dropdown(
                    choices=initial_section_names,
                    value=initial_section,
                    label="Edit shared section",
                    allow_custom_value=False
                )
                section_save_btn = gr.Button("Save", variant="primary", size="sm")
                section_rename_btn = gr.Button("Rename", size="sm")
                section_delete_btn = gr.Button("Delete", variant="secondary", size="sm")
            section_name_ui = gr.Text(value=initial_section or "", label="Section name", max_lines=1)
            section_text_ui = gr.Textbox(
                value=initial_section_text or "",
                label="Section text",
                lines=8,
                max_lines=8,
                elem_classes=["multiline-textbox", *prompt_box_classes(None)] # A section can go into any prompt
            )
            initial_section_error = manager.validate_section_text(initial_section_text or "")
            section_error_ui = self.__construct_error_message_panel(
                "" if initial_section_error.is_success else initial_section_error.error_message,
                is_visible=not initial_section_error.is_success
            )
            section_status_ui = gr.Markdown("")

        def on_type_change(type_display: str):
            prompt_type = type_id(type_display)
            selected = manager.get_default_edit_name(prompt_type)
            text = manager.get_profile_text(prompt_type, selected) if selected else ""
            return (
                manager.about_markdown(prompt_type),
                manager.variables_markdown(prompt_type),
                edit_dropdown(prompt_type, selected),
                selected or "",
                # The text box's colouring follows the prompt type, for the conditions it may use
                gr.update(value=text or "", elem_classes=["multiline-textbox", *prompt_box_classes(prompt_type)]),
                validation_panel(prompt_type, text or ""),
                active_dropdown(prompt_type),
                ""
            )

        def on_edit_change(type_display: str, selected: str):
            prompt_type = type_id(type_display)
            if not selected:
                return "", "", error_panel("", False)
            text = manager.get_profile_text(prompt_type, selected) or ""
            return selected, text, validation_panel(prompt_type, text)

        def on_text_change(type_display: str, text: str):
            return validation_panel(type_id(type_display), text or "")

        def on_load_prompts_tab(type_display: str):
            if self.__config_loader is None:
                return gr.update(), gr.update(), "The Prompts tab is not available."
            prompt_type = type_id(type_display)
            text = str(self.__config_loader.definitions.get_config_value_definition(prompt_type).value)
            return (
                text,
                validation_panel(prompt_type, text),
                "Loaded the Prompts tab text into the editor. Click Save to keep it, or refresh the page to discard it."
            )

        def on_save(type_display: str, selected: str, name: str, text: str):
            prompt_type = type_id(type_display)
            ok, msg, created = manager.save_from_editor(prompt_type, selected, name, text)
            if not ok:
                return msg, error_panel(msg, True), gr.update(), name or "", gr.update()
            saved_name = name.strip()
            if created:
                return (
                    msg,
                    error_panel("", False),
                    edit_dropdown(prompt_type, saved_name),
                    saved_name,
                    active_dropdown(prompt_type)
                )
            if manager.get_active_name(prompt_type) == saved_name:
                notify_if_needed(True)
            return msg, error_panel("", False), gr.update(), saved_name, gr.update()

        def on_rename(type_display: str, selected: str, name: str):
            prompt_type = type_id(type_display)
            was_active = manager.get_active_name(prompt_type) == selected
            ok, msg = manager.rename_profile(prompt_type, selected, name)
            if not ok:
                return msg, error_panel(msg, True), gr.update(), name or "", gr.update()
            new_selected = name.strip()
            if was_active:
                notify_if_needed(True)
            return (
                msg,
                error_panel("", False),
                edit_dropdown(prompt_type, new_selected),
                new_selected,
                active_dropdown(prompt_type)
            )

        def on_delete(type_display: str, selected: str):
            prompt_type = type_id(type_display)
            was_active = manager.get_active_name(prompt_type) == selected
            ok, msg = manager.delete_profile(prompt_type, selected)
            if not ok:
                return msg, error_panel(msg, True), gr.update(), selected or "", gr.update(), gr.update()
            if was_active:
                notify_if_needed(True)
            next_selected = manager.get_default_edit_name(prompt_type)
            next_text = manager.get_profile_text(prompt_type, next_selected) if next_selected else ""
            return (
                msg,
                error_panel("", False),
                edit_dropdown(prompt_type, next_selected),
                next_selected or "",
                next_text or "",
                active_dropdown(prompt_type)
            )

        def on_section_edit(selected: str):
            if not selected:
                return "", "", error_panel("", False)
            text = manager.get_section_text(selected) or ""
            return selected, text, section_validation_panel(text)

        def on_section_text_change(text: str):
            return section_validation_panel(text or "")

        def on_section_save(selected: str, name: str, text: str, type_display: str, prompt_text: str):
            ok, msg, _created = manager.save_section_from_editor(selected, name, text)
            prompt_type = type_id(type_display)
            if not ok:
                return msg, error_panel(msg, True), gr.update(), name or "", gr.update(), gr.update()
            notify_if_needed(True)
            saved_name = name.strip()
            return (
                msg,
                error_panel("", False),
                section_dropdown(saved_name),
                saved_name,
                manager.variables_markdown(prompt_type),
                validation_panel(prompt_type, prompt_text or "")
            )

        def on_section_rename(selected: str, name: str, type_display: str, prompt_text: str):
            ok, msg = manager.rename_section(selected, name)
            prompt_type = type_id(type_display)
            if not ok:
                return msg, error_panel(msg, True), gr.update(), name or "", gr.update(), gr.update()
            notify_if_needed(True)
            new_selected = name.strip()
            return (
                msg,
                error_panel("", False),
                section_dropdown(new_selected),
                new_selected,
                manager.variables_markdown(prompt_type),
                validation_panel(prompt_type, prompt_text or "")
            )

        def on_section_delete(selected: str, type_display: str, prompt_text: str):
            ok, msg = manager.delete_section(selected)
            prompt_type = type_id(type_display)
            if not ok:
                return msg, error_panel(msg, True), gr.update(), selected or "", gr.update(), gr.update(), gr.update()
            notify_if_needed(True)
            names = manager.get_section_names()
            next_selected = names[0] if names else None
            next_text = manager.get_section_text(next_selected) if next_selected else ""
            return (
                msg,
                error_panel("", False),
                section_dropdown(next_selected),
                next_selected or "",
                next_text or "",
                manager.variables_markdown(prompt_type),
                validation_panel(prompt_type, prompt_text or "")
            )

        def on_preview_type_change(type_display: str):
            situations = PromptProfileManager.preview_situation_labels(type_id(type_display))
            return (gr.update(visible=bool(situations)),
                    gr.update(choices=situations, value=situations[0] if situations else None))

        def refresh_preview(type_display: str, text: str, situation: str):
            return manager.preview_text(type_id(type_display), text or "", situation or "")

        def on_active_change(type_display: str, active_value: str):
            prompt_type = type_id(type_display)
            names = manager.get_profile_names(prompt_type)
            if active_value and active_value != none_label and active_value not in names:
                return "Ignored invalid active selection."
            target = None if (not active_value or active_value == none_label) else active_value
            ok, msg, changed = manager.set_active(prompt_type, target)
            if ok:
                notify_if_needed(changed)
            return msg

        section_edit_ui.change(
            on_section_edit,
            inputs=[section_edit_ui],
            outputs=[section_name_ui, section_text_ui, section_error_ui],
            show_progress="hidden"
        )
        section_text_ui.blur(
            on_section_text_change,
            inputs=[section_text_ui],
            outputs=[section_error_ui],
            show_progress="hidden"
        )
        section_save_btn.click(
            on_section_save,
            inputs=[section_edit_ui, section_name_ui, section_text_ui, type_ui, text_ui],
            outputs=[section_status_ui, section_error_ui, section_edit_ui, section_name_ui, variables_ui, error_ui]
        )
        section_rename_btn.click(
            on_section_rename,
            inputs=[section_edit_ui, section_name_ui, type_ui, text_ui],
            outputs=[section_status_ui, section_error_ui, section_edit_ui, section_name_ui, variables_ui, error_ui]
        )
        section_delete_btn.click(
            on_section_delete,
            inputs=[section_edit_ui, type_ui, text_ui],
            outputs=[section_status_ui, section_error_ui, section_edit_ui, section_name_ui, section_text_ui, variables_ui, error_ui]
        )
        type_ui.change(
            on_type_change,
            inputs=[type_ui],
            outputs=[about_ui, variables_ui, edit_ui, name_ui, text_ui, error_ui, active_ui, status_ui],
            show_progress="hidden"
        )
        edit_ui.change(
            on_edit_change,
            inputs=[type_ui, edit_ui],
            outputs=[name_ui, text_ui, error_ui],
            show_progress="hidden"
        )
        text_ui.blur(
            on_text_change,
            inputs=[type_ui, text_ui],
            outputs=[error_ui],
            show_progress="hidden"
        )
        text_ui.submit(
            on_text_change,
            inputs=[type_ui, text_ui],
            outputs=[error_ui],
            show_progress="hidden"
        )
        load_prompts_tab_btn.click(
            on_load_prompts_tab,
            inputs=[type_ui],
            outputs=[text_ui, error_ui, status_ui]
        )
        save_btn.click(
            on_save,
            inputs=[type_ui, edit_ui, name_ui, text_ui],
            outputs=[status_ui, error_ui, edit_ui, name_ui, active_ui]
        )
        rename_btn.click(
            on_rename,
            inputs=[type_ui, edit_ui, name_ui],
            outputs=[status_ui, error_ui, edit_ui, name_ui, active_ui]
        )
        delete_btn.click(
            on_delete,
            inputs=[type_ui, edit_ui],
            outputs=[status_ui, error_ui, edit_ui, name_ui, text_ui, active_ui]
        )
        active_ui.change(
            on_active_change,
            inputs=[type_ui, active_ui],
            outputs=[status_ui],
            show_progress="hidden"
        )
        # The preview follows the prompt type, the chosen situation and the text, also when the text is loaded or a section changes
        type_ui.change(
            on_preview_type_change,
            inputs=[type_ui],
            outputs=[preview_accordion_ui, preview_situation_ui],
            show_progress="hidden"
        )
        preview_inputs = [type_ui, text_ui, preview_situation_ui]
        for trigger in (preview_situation_ui.change, text_ui.change, section_save_btn.click, section_rename_btn.click, section_delete_btn.click):
            trigger(refresh_preview, inputs=preview_inputs, outputs=[preview_ui], show_progress="hidden")

    def __embed_prompt_preview(self, config_value: ConfigValue, input_ui: Any) -> None:
        """The Prompts tab: under a merged prompt, the text it sends in one chosen situation. Shared sections are not expanded here,
        since the Prompts tab text is sent as it is"""
        prompt_type = config_value.identifier
        situations = PromptProfileManager.preview_situation_labels(prompt_type)
        if not situations:
            return
        manager = self.get_prompt_profile_manager()
        with gr.Accordion("Preview: the text sent in one situation", open=False):
            situation_ui = gr.Dropdown(choices=situations, value=situations[0], label="Show as", allow_custom_value=False)
            preview_ui = gr.Textbox(
                value=manager.preview_text(prompt_type, str(config_value.value), situations[0], expand_sections=False),
                label="Text sent in this situation (variables are filled in when it is sent)",
                lines=12,
                max_lines=12,
                interactive=False,
                elem_classes="multiline-textbox"
            )
        def refresh(text: str, situation: str):
            return manager.preview_text(prompt_type, text or "", situation or "", expand_sections=False)
        for trigger in (situation_ui.change, input_ui.change):
            trigger(refresh, inputs=[input_ui, situation_ui], outputs=[preview_ui], show_progress="hidden")

    def __render_tts_page(self, config_value: ConfigValueGroup) -> bool:
        """The Text-to-Speech page (see tts_definitions for its order): its settings, and the voice parts that keep their own files next
        to config.ini. A list (Settings for) shows one service's part at a time, and follows the TTS Service: Global settings (with Your
        key) and the Model, or its program settings. Under it, folded: Hear it and Customization per voice type and per NPC. False for any other group"""
        if config_value.identifier != "TTS" or self.__config_loader is None:
            return False
        config_loader = self.__config_loader
        chat_manager_getter = lambda: getattr(_game_manager_ref, "chat_manager", None) if _game_manager_ref is not None else None
        if config_value.description:
            gr.Markdown(config_value.description)
        parts: dict[str, list[ConfigValue]] = {} # e.g. XTTS: its settings, in the order they were added
        main_settings: list[ConfigValue] = []
        for cf in config_value.value:
            if not cf.is_hidden:
                if cf.shown_in:
                    parts.setdefault(cf.shown_in, []).append(cf)
                else:
                    main_settings.append(cf)

        with gr.Accordion(label=SPEED_PART, open=False):
            gr.Markdown(TTSDefinitions.SPEED_DESCRIPTION)
            self.__render_settings(parts.get(SPEED_PART, []))
        # TTS Service and the Model it speaks with (only for a service with models) side by side, then Backup Voice Service on its own row
        service_cf = next((cf for cf in main_settings if cf.identifier == "tts_service"), None)
        backup_cf = next((cf for cf in main_settings if cf.identifier == "tts_backup_service"), None)
        speaking_part = part_of_service(str(service_cf.value)) if service_cf is not None else ""
        used_models: dict[str, tuple[Any, gr.Markdown, gr.Column]] = {} # by service: its Model, the note under it, and its column
        with gr.Row(equal_height=True):
            if service_cf is not None:
                self.__render_settings([service_cf])
            for part in SERVICE_PARTS:
                models = ServiceModels.of(part)
                model_cf = next((cf for cf in parts.get(part, []) if models is not None and cf.identifier == models.setting), None)
                if model_cf is None:
                    continue
                drawn: dict[str, Any] = {}

                def under_model(part=part, models=models, drawn=drawn) -> None: # inside the Model's box
                    note = ModelSettingsUI.note(part, models.picked(config_loader))
                    drawn["note"] = gr.Markdown(note, visible=bool(note))

                with gr.Column(visible=part == speaking_part, scale=1, min_width=160) as column:
                    self.__inside_setting[model_cf.identifier] = under_model
                    self.__second_in_row = True
                    self.__render_settings([model_cf])
                    self.__second_in_row = False
                used_models[part] = (self.__config_value_to_ui_element.get(model_cf), drawn["note"], column)
        if backup_cf is not None:
            self.__render_settings([backup_cf])
        how_models: list[gr.Accordion] = [] # under Backup Voice Service, for the service the TTS Service picks
        for part in used_models:
            with gr.Accordion("How model settings work", open=False, visible=part == speaking_part, elem_classes="help-fold") as how:
                gr.Markdown(HOW_MODELS_WORK.format(name=part))
            how_models.append(how)
        self.__render_settings([cf for cf in main_settings if cf is not service_cf and cf is not backup_cf])

        dropdowns = [] # the TTS Service and Backup Voice Service lists, which list the voice setups too
        for identifier in ("tts_service", "tts_backup_service"):
            selection = self.__identifier_to_config_value.get(identifier)
            component = self.__config_value_to_ui_element.get(selection) if selection is not None else None
            if isinstance(selection, ConfigValueSelection) and component is not None:
                dropdowns.append((selection, component))
        service_ui = dropdowns[0][1] if dropdowns and dropdowns[0][0].identifier == "tts_service" else None
        tts_service = str(service_ui.value) if service_ui is not None else config_loader.tts_service_name
        picked = part_of_service(tts_service)

        notes = {POCKET_TTS_GROUP: pocket_tts_language_note(config_loader), VOICE_SETUPS_PART: QUICKEST_WAY} # in the box of the Settings for list
        notes = {part: note for part, note in notes.items() if note}
        intros: list[gr.Column] = []
        edit_models: dict[str, gr.Dropdown] = {} # by service: the model whose settings show, next to Settings for
        model_lists: dict[str, ModelListUI] = {} # by service: Add, Remove at the top right of its Model
        with gr.Group(elem_classes="tts-service-picker"):
            with gr.Row():
                picker_ui = gr.Dropdown(choices=list(SERVICE_PARTS), value=picked, label="Settings for", allow_custom_value=False, scale=1)
                for part in used_models:
                    models = ServiceModels.of(part)
                    shown = models.picked(config_loader) # opens on the model the service speaks with
                    ServiceStyle(config_loader, part).show_model(shown.id)
                    choices = models.choices(shown.id, config_loader)
                    if all(value != shown.id for _, value in choices): # a model typed in
                        choices.append((shown.id, shown.id))
                    edit_models[part] = gr.Dropdown(choices=choices, value=shown.id, label="Model", allow_custom_value=True, render=False)
                    model_lists[part] = ModelListUI(config_loader, part, part == picked, edit_models[part])
            for part, note in notes.items():
                with gr.Column(visible=part == picked, elem_classes="tts-service-intro") as intro:
                    gr.Markdown(note)
                intros.append(intro)

        columns: list[gr.Column] = []
        edit_ui = None # Setup to edit, under Your voice setups
        shown_model_ui = gr.Textbox(value="", visible=False) # changes when a Model is picked, so the voice under Customization per voice type and per NPC redraws
        model_settings_uis: list[ModelSettingsUI] = []
        for part in SERVICE_PARTS:
            module = ONLINE_SERVICES.get(part, (None, None))[0]
            sign_in = getattr(module, "WORKSPACE_SETTING", None) # Qwen's Workspace ID goes with the key
            settings = parts.get(part, [])
            with gr.Column(visible=part == picked, elem_id=service_part_id(part), elem_classes="tts-service-part") as column:
                if part == VOICE_SETUPS_PART:
                    edit_ui = render_voice_setups_editor(config_loader, dropdowns, selected=tts_service)
                # Your key (Turn on voice cloning for Pocket TTS), at the top of Global settings for <service>, which is open until it's saved
                key_panel, key_missing = None, False
                if part == POCKET_TTS_GROUP:
                    key_panel, key_missing = (lambda fold: render_pocket_tts_panel(config_loader)), not pocket_tts_signed_in()
                elif part in ONLINE_SERVICE_GROUPS:
                    key_settings = [cf for cf in settings if cf.identifier == sign_in]
                    key_panel = lambda fold, part=part, key_settings=key_settings: render_online_service_panel(
                        config_loader, part, lambda: self.__render_settings(key_settings), fold)
                    key_missing = not has_key(part)
                if part in SERVICE_VOLUMES: # Global settings for <service>, then the Model
                    self.__render_every_npc(config_loader, part, [cf for cf in settings if cf.identifier != sign_in], key_panel, key_missing,
                                            shown_model_ui, model_settings_uis, used_models.get(part), edit_models.get(part))
                    if part in model_lists:
                        model_lists[part].wire(next(ui for ui in model_settings_uis if ui.name == part))
                else:
                    self.__render_settings(settings) # e.g. XTTS's folder and server
            columns.append(column)

        def shown_service(part: str, setup: str | None) -> str:
            """The service whose part is shown: Hear it and Customization per voice type and per NPC use it. Under Your voice setups, the saved setup being edited"""
            if part != VOICE_SETUPS_PART:
                return part
            info = voice_services.find(setup)
            return info.name if info is not None and info.is_setup else ""

        def clones(service: str) -> bool:
            info = voice_services.find(service)
            return info is not None and info.uses_clips

        def no_cloning_note(service: str) -> str:
            if not service:
                return "Save a setup above to change the voices it clones."
            return TTSDefinitions.NO_CLONING_NOTE.format(name=service)

        first_shown = shown_service(picked, edit_ui.value if edit_ui is not None else None)
        shown_service_ui = gr.Textbox(value=first_shown, visible=False) # the service Hear it and Customization per voice type and per NPC use

        # a voice setup has its own Hear it, with the boxes as they are now
        with gr.Accordion(label=HEAR_IT_PART, open=False, visible=picked != VOICE_SETUPS_PART) as hear_part:
            render_voice_test(config_loader, chat_manager_getter, shown_service_ui)

        with gr.Accordion(label=NPC_VOICES_PART, open=False):
            no_cloning_ui = gr.Markdown(no_cloning_note(first_shown), visible=not clones(first_shown))
            with gr.Column(visible=clones(first_shown), elem_id=service_part_id(NPC_VOICES_PART)) as clips_part:
                gr.Markdown(TTSDefinitions.VOICE_CLIPS_DESCRIPTION)
                # redraws when another service is shown, or another model picked
                render_voice_clips_editor(config_loader, chat_manager_getter, shown_service_ui, shown_model_ui)

        with gr.Accordion(label=VOICE_EFFECTS_GROUP, open=False):
            gr.Markdown(TTSDefinitions.VOICE_EFFECTS_DESCRIPTION)
            render_voice_effects_editor(config_loader, chat_manager_getter)
        with gr.Accordion(label=WORD_LISTS_GROUP, open=False):
            gr.Markdown(TTSDefinitions.WORD_LISTS_DESCRIPTION)
            render_word_lists_editor(config_loader, chat_manager_getter)

        def on_pick(part: str, setup: str | None):
            return [*(gr.update(visible=p == part) for p in (*notes, *SERVICE_PARTS, *model_lists)),
                    gr.update(visible=part != VOICE_SETUPS_PART), shown_service(part, setup)]

        def on_shown(service: str):
            return gr.update(value=no_cloning_note(service), visible=not clones(service)), gr.update(visible=clones(service))

        setup_inputs = [edit_ui] if edit_ui is not None else []
        picker_ui.change(lambda part, *setup: on_pick(part, setup[0] if setup else None), inputs=[picker_ui, *setup_inputs],
                         outputs=[*intros, *columns, *(tools.column for tools in model_lists.values()), hear_part,
                                  shown_service_ui], show_progress="hidden", show_api=False)
        if edit_ui is not None: # another setup picked, saved or deleted under Your voice setups
            edit_ui.change(lambda part, setup: shown_service(part, setup) if part == VOICE_SETUPS_PART else gr.update(), inputs=[picker_ui, edit_ui],
                           outputs=[shown_service_ui], show_progress="hidden", show_api=False)
        shown_service_ui.change(on_shown, inputs=[shown_service_ui], outputs=[no_cloning_ui, clips_part], show_progress="hidden", show_api=False)
        if service_ui is not None and hasattr(service_ui, "_id"): # a new TTS Service shows its settings
            service_ui.change(part_of_service, inputs=[service_ui], outputs=[picker_ui], show_progress="hidden", show_api=False)
            # and the Model it speaks with, next to it, and How model settings work for that service
            service_ui.change(lambda value: [gr.update(visible=part_of_service(value) == p) for p in used_models] * 2, inputs=[service_ui],
                              outputs=[*(column for _, _, column in used_models.values()), *how_models], show_progress="hidden", show_api=False)
        return True

    def visit_ConfigValueGroup(self, config_value: ConfigValueGroup):
        if not config_value.is_hidden:
            if self.__render_tts_page(config_value) or self.__render_stt_page(config_value):
                return
            if config_value.name == "Prompt Profiles":
                self.render_prompt_profile_editor(config_value)
                return
            if config_value.name == "Model Profiles":
                if config_value.description:
                    gr.Markdown(config_value.description)
                self.render_model_profile_editor(config_value)
                for child_value in config_value.value:
                    if child_value.identifier not in {
                        "profile_selected_service",
                        "profile_selected_model",
                        "profile_parameters"
                    }:
                        child_value.accept_visitor(self)
                return
            if config_value.foldable:
                with gr.Accordion(label=config_value.name, open=False):
                    self.__render_group_settings(config_value)
            else:
                self.__render_group_settings(config_value)

    def __render_group_settings(self, config_value: ConfigValueGroup):
        if config_value.description:
            gr.Markdown(config_value.description)

        has_advanced_values = False
        has_basic_values = False
        regular_settings = []
        basic_settings = []
        advanced_settings = []
        folded_parts: dict[str, list[ConfigValue]] = {} # e.g. XTTS: its settings, in the order they were added
        for cf in config_value.value:
            if not cf.is_hidden:
                if cf.shown_in:
                    folded_parts.setdefault(cf.shown_in, []).append(cf)
                elif ConfigValueTag.advanced in cf.tags:
                    advanced_settings.append(cf)
                    has_advanced_values = True
                elif ConfigValueTag.basic in cf.tags:
                    basic_settings.append(cf)
                    has_basic_values = True
                else:
                    regular_settings.append(cf)

        # Render basic settings in an accordion
        if has_basic_values:
            with gr.Accordion(label="Basic Settings", open=False):
                for cf in basic_settings:
                    cf.accept_visitor(self)
                
                # Render any pending row group in basic section
                if self.__last_row_group is not None:
                    self.__render_row_group(self.__last_row_group)
                    self.__last_row_group = None
                
                # Render any remaining row groups in basic section
                for group_id in list(self.__row_groups.keys()):
                    self.__render_row_group(group_id)

        columns_by_switch = self.__render_settings_in_columns(regular_settings)
        if config_value.page and self.__pending_shared_setting is not None:
            # A section of a page (e.g. the Conversation page): its last setting stays in it, instead of sharing a row with the next section's first
            self.__create_single_setting(self.__pending_shared_setting)
            self.__pending_shared_setting = None
        self.__show_columns_with_their_switch(columns_by_switch)
        self.__grey_out_talk_settings(config_value)

        # Render any pending row group after processing regular settings
        if self.__last_row_group is not None:
            self.__render_row_group(self.__last_row_group)
            self.__last_row_group = None
        
        # Render any remaining row groups (shouldn't happen but just in case)
        for group_id in list(self.__row_groups.keys()):
            self.__render_row_group(group_id)
        
        for name, settings in folded_parts.items():
            self.__render_folded_part(name, settings, config_value.folded_part_intros.get(name))
        if has_advanced_values:
            self.__render_folded_part("Advanced", advanced_settings)

    def __render_settings_in_columns(self, settings: list[ConfigValue]) -> dict[tuple[str, ...], list[gr.Column]]:
        """Settings drawn in order. Those that only show while a switch is on or a list has a choice picked (shown_when) are kept in a column
        of their own, which those settings show and hide. Settings in a row with the same conditions share the column, so two can share a row.
        Returns the columns, for __show_columns_with_their_switch once every setting is drawn"""
        columns_by_switch: dict[tuple[str, ...], list[gr.Column]] = {}
        open_column: gr.Column | None = None
        shown_when: tuple[str, ...] = ()
        for cf in settings:
            conditions = shown_when_conditions(cf.shown_when)
            if conditions != shown_when:
                self.__close_shown_when_column(open_column)
                open_column, shown_when = None, conditions
                if shown_when:
                    open_column = gr.Column(visible=all(condition_holds(c, self.__current_value(c)) for c in shown_when))
                    open_column.__enter__()
                    columns_by_switch.setdefault(shown_when, []).append(open_column)
            cf.accept_visitor(self)
            self.__embed_after_setting(cf)
        self.__close_shown_when_column(open_column)
        return columns_by_switch

    def __render_stt_page(self, config_value: ConfigValueGroup) -> bool:
        """The Speech-to-Text page: Talking, then Service, each a fold (open at first) with a line on what it is for. Settings sit side by side.
        Service ends with the Test button. False for any other group"""
        if config_value.identifier != "STT" or self.__config_loader is None:
            return False
        config_loader = self.__config_loader
        parts: dict[str, list[ConfigValue]] = {STT_TALKING_PART: [], STT_SERVICE_PART: []}
        for cf in config_value.value:
            if not cf.is_hidden:
                parts.setdefault(cf.shown_in or STT_SERVICE_PART, []).append(cf)
        columns_by_switch: dict[tuple[str, ...], list[gr.Column]] = {}
        with gr.Column(elem_classes="stt-page"): # style.css dims the settings greyed out here
            if config_value.description:
                gr.Markdown(config_value.description)
            for part, description in ((STT_TALKING_PART, STT_TALKING_DESCRIPTION), (STT_SERVICE_PART, STT_SERVICE_DESCRIPTION)):
                with gr.Accordion(label=part, open=True):
                    gr.Markdown(description)
                    for conditions, columns in self.__render_settings_in_columns(parts[part]).items():
                        columns_by_switch.setdefault(conditions, []).extend(columns)
                    if self.__pending_shared_setting is not None: # a part's last setting stays in it
                        self.__create_single_setting(self.__pending_shared_setting)
                        self.__pending_shared_setting = None
                    if self.__last_row_group is not None: # so does a part's last row
                        self.__render_row_group(self.__last_row_group)
                        self.__last_row_group = None
                    if part == STT_SERVICE_PART:
                        render_stt_test(config_loader, lambda: getattr(_game_manager_ref, "stt", None) if _game_manager_ref is not None else None)
            self.__show_columns_with_their_switch(columns_by_switch)
            self.__grey_out_stt_settings()
        return True

    def __grey_out_stt_settings(self) -> None:
        """On the Speech-to-Text page, settings that can't change anything are greyed out (not hidden, so the pairs stay side by side), as soon
        as the page loads and after each change: PTT Hotkey while Enable PTT is off, Refresh Frequency while Proactive Mode is off, and
        Catch First Names unless the chosen service takes the names as separate words"""
        def ui_of(identifier: str) -> Any:
            component = self.__config_value_to_ui_element.get(self.__setting(identifier))
            return component if component is not None and hasattr(component, "_id") else None

        root = gr.context.Context.root_block
        for target, switch in GREYED_UNLESS_ON.items():
            target_ui, switch_ui = ui_of(target), ui_of(switch)
            if target_ui is None or switch_ui is None:
                continue
            def stt_greying(on):
                return gr.update(interactive=bool(on))
            switch_ui.change(stt_greying, switch_ui, target_ui, show_progress="hidden")
            if root is not None:
                root.load(stt_greying, switch_ui, target_ui, show_progress="hidden")

        names_ui = ui_of("stt_first_names")
        lists = [ui_of(identifier) for identifier in ("stt_service", "stt_online_service", "stt_custom_format")]
        if names_ui is None or any(ui is None for ui in lists):
            return
        def stt_names_greying(service, online_service, custom_format):
            return gr.update(interactive=takes_name_list(service, online_service, custom_format))
        for list_ui in lists:
            list_ui.change(stt_names_greying, lists, names_ui, show_progress="hidden")
        if root is not None:
            root.load(stt_names_greying, lists, names_ui, show_progress="hidden")

    def __render_every_npc(self, config_loader: ConfigLoader, part: str, settings: list[ConfigValue], key_panel: Callable[[Any], None] | None,
                           key_missing: bool, shown_model_ui: Any, model_settings_uis: list[ModelSettingsUI], used_model: tuple | None = None,
                           edit_ui: Any = None) -> None:
        """A service's Global settings for <service> (folded; open while *key_missing*), with *key_panel* drawing Your key at its top. For
        an online service, the model's own settings follow (see __render_model_settings)"""
        models = ServiceModels.of(part)
        with gr.Column(elem_id=sound_part_id(part)):
            if models is None or used_model is None or edit_ui is None:
                with gr.Accordion(every_npc_part(part), open=key_missing) as fold:
                    if key_panel is not None:
                        key_panel(fold)
                    self.__render_settings(settings)
            else:
                model_settings_uis.append(self.__render_model_settings(config_loader, part, models, settings, shown_model_ui, used_model,
                                                                       edit_ui, key_panel, key_missing))

    def __render_model_settings(self, config_loader: ConfigLoader, part: str, models: ServiceModels, settings: list[ConfigValue],
                                shown_model_ui: Any, used_model: tuple, edit_ui: Any, key_panel: Callable[[Any], None] | None = None,
                                key_missing: bool = False) -> ModelSettingsUI:
        """An online service's Global settings for <service> (folded): Your key (folded, drawn by *key_panel*) and the settings for every
        model (Audio tags, Directions, Volume), hidden when the Model it speaks with (*used_model*, next to TTS Service) skips them.
        Then Settings for <model> (folded) for the model picked next to Settings for (*edit_ui*): Use <model> for <service> when the
        service speaks with another, the settings each model keeps its own value of, and Settings Mantella doesn't have yet (folded)"""
        style = ServiceStyle(config_loader, part)
        used = models.picked(config_loader)
        shown = style.shown_model() or used
        per_model_names = {setting.name for setting in style.per_model_settings()}
        extra = EXTRA_REQUESTS.get(part)
        extra_cf = next((cf for cf in settings if extra is not None and cf.identifier == extra.name), None)
        for_every_model = [cf for cf in settings if cf.identifier != models.setting and cf.identifier not in per_model_names]
        per_model = [cf for cf in settings if cf.identifier in per_model_names and cf is not extra_cf]
        every_model_columns: dict[str, Any] = {}
        columns: dict[str, Any] = {}

        def in_column(cf: ConfigValue, model: ServiceModel, found: dict[str, Any]) -> None:
            with gr.Column(visible=model.takes(cf.identifier)) as column:
                self.__render_settings([cf])
            found[cf.identifier] = column

        def in_rows(row_settings: list[ConfigValue], model: ServiceModel, found: dict[str, Any]) -> None: # two to a row (two_by_two)
            for row in two_by_two(row_settings):
                with gr.Row():
                    for i, cf in enumerate(row):
                        self.__second_in_row = i > 0
                        in_column(cf, model, found)
                self.__second_in_row = False

        extra_intro_ui = None
        with gr.Accordion(every_npc_part(part), open=key_missing) as fold:
            if key_panel is not None:
                key_panel(fold)
            in_rows(for_every_model, used, every_model_columns)
        with gr.Accordion(ModelSettingsUI.heading(shown), open=False) as heading_ui:
            use_btn = gr.Button(ModelSettingsUI.use_label(part, shown), size="sm", visible=shown.id != used.id)
            in_rows(per_model, shown, columns)
            if extra_cf is not None:
                with gr.Accordion(EXTRA_REQUEST_LABEL, open=False, elem_classes="voice-clips-subsection"):
                    extra_intro_ui = gr.Markdown(ModelSettingsUI.extra_intro(part, shown))
                    in_column(extra_cf, shown, columns)
        values = {cf.identifier: self.__config_value_to_ui_element.get(cf) for cf in [*per_model, *([extra_cf] if extra_cf else [])]}
        used_ui, note_ui, _ = used_model
        ui = ModelSettingsUI(config_loader, part, used_ui, note_ui, every_model_columns, edit_ui, heading_ui, extra_intro_ui, use_btn, columns,
                             {name: component for name, component in values.items() if component is not None}, shown_model_ui)
        ui.wire()
        return ui

    def __render_folded_part(self, name: str, settings: list[ConfigValue], intro: str | None = None):
        """Settings in a part of the page that starts folded, e.g. XTTS or Advanced, under its intro line if it has one"""
        if self.__pending_shared_setting is not None: # a setting from above doesn't share a row with this part's first
            self.__create_single_setting(self.__pending_shared_setting)
            self.__pending_shared_setting = None
        with gr.Accordion(label=name, open=False):
            if intro:
                gr.Markdown(intro)
            self.__render_settings(settings)

    def __render_settings(self, settings: list[ConfigValue]):
        """Settings drawn where the page is now. A setting waiting to share a row, and row groups, stay here.
        A setting that only shows while another has a value (shown_when) gets a column of its own, as in a section"""
        columns_by_switch: dict[tuple[str, ...], list[gr.Column]] = {}
        for cf in settings:
            conditions = shown_when_conditions(cf.shown_when)
            column = None
            if conditions:
                if self.__pending_shared_setting is not None:
                    self.__create_single_setting(self.__pending_shared_setting)
                    self.__pending_shared_setting = None
                column = gr.Column(visible=all(condition_holds(c, self.__current_value(c)) for c in conditions))
                column.__enter__()
                columns_by_switch.setdefault(conditions, []).append(column)
            cf.accept_visitor(self)
            self.__close_shown_when_column(column)
        if self.__pending_shared_setting is not None:
            self.__create_single_setting(self.__pending_shared_setting)
            self.__pending_shared_setting = None
        self.__show_columns_with_their_switch(columns_by_switch)
        if self.__last_row_group is not None:
            self.__render_row_group(self.__last_row_group)
            self.__last_row_group = None
        for group_id in list(self.__row_groups.keys()):
            self.__render_row_group(group_id)

    def __close_shown_when_column(self, column: gr.Column | None):
        """Ends a column of settings that only show while a switch is on. Its last setting stays in it"""
        if column is None:
            return
        if self.__pending_shared_setting is not None:
            self.__create_single_setting(self.__pending_shared_setting)
            self.__pending_shared_setting = None
        column.__exit__(None, None, None)

    def __embed_after_setting(self, config_value: ConfigValue) -> None:
        """Controls drawn right under a setting: the Key box under the Online Service list, and under Custom Service's Model and Time Limit"""
        if self.__config_loader is None or config_value.identifier not in ("stt_online_service", "stt_custom_timeout"):
            return
        def refresh_running_key():
            if _game_manager_ref is not None and hasattr(_game_manager_ref, "refresh_stt_key"):
                _game_manager_ref.refresh_stt_key()
        def llm_api() -> str:
            setting = self.__setting("llm_api")
            return str(setting.value) if setting is not None else ""
        if config_value.identifier == "stt_online_service":
            render_key_box(self.__config_value_to_ui_element.get(config_value), refresh_running_key, llm_api)
        else:
            render_key_box(None, refresh_running_key, llm_api, custom=True)

    def __current_value(self, condition: str) -> Any:
        """The value of the setting a shown_when condition looks at"""
        setting = self.__setting(condition.partition("=")[0])
        return setting.value if setting is not None else None

    def __setting(self, identifier: str) -> ConfigValue | None:
        """A setting by its identifier, also before it is drawn"""
        setting = self.__identifier_to_config_value.get(identifier)
        if setting is None and self.__config_loader is not None:
            try:
                setting = self.__config_loader.definitions.get_config_value_definition(identifier)
            except Exception:
                setting = None
        return setting

    def __show_columns_with_their_switch(self, columns_by_switch: dict[tuple[str, ...], list[gr.Column]]):
        """Shows the settings that depend on switches and lists while every condition holds, and hides them otherwise"""
        for conditions, columns in columns_by_switch.items():
            components = []
            for condition in conditions:
                switch = self.__identifier_to_config_value.get(condition.partition("=")[0])
                components.append(self.__config_value_to_ui_element.get(switch) if switch else None)
            if any(component is None or not hasattr(component, "_id") for component in components):
                continue
            def show_or_hide(*values, conditions=conditions, count=len(columns)):
                visible = all(condition_holds(c, v) for c, v in zip(conditions, values))
                # Gradio takes a single output's value as it is, so one column gets a single update, not a list of one
                updates = [gr.update(visible=visible)] * count
                return updates[0] if count == 1 else updates
            for component in dict.fromkeys(components): # each setting once, even when two conditions look at it
                component.change(show_or_hide, components, columns, show_progress="hidden")

    def __grey_out_talk_settings(self, group: ConfigValueGroup):
        """The talk settings of each set in the section: "who still speaks", Replies in between and Extra rounds are greyed out (not hidden)
        while they can't change anything (conversation_definitions.greyed_choices), as soon as the page loads and after each change"""
        identifiers = {value.identifier for value in group.value}
        for kind in [SHARED] + TALK_KINDS:
            keys = [talk_key(kind, setting) for setting in (WHO_MUST_SPEAK, AT_REPLY_LIMIT, REPLIES_IN_BETWEEN, EXTRA_ROUNDS)]
            if not set(keys) <= identifiers:
                continue
            components = [self.__config_value_to_ui_element.get(self.__identifier_to_config_value.get(key)) for key in keys]
            if any(component is None or not hasattr(component, "_id") for component in components):
                continue
            must_speak_ui, at_limit_ui, between_ui, rounds_ui = components
            outputs = [at_limit_ui, between_ui, rounds_ui]
            for trigger in (must_speak_ui.change, at_limit_ui.change, rounds_ui.change):
                trigger(talk_greying, [must_speak_ui, at_limit_ui, rounds_ui], outputs, show_progress="hidden")
            root = gr.context.Context.root_block
            if root is not None:
                root.load(talk_greying, [must_speak_ui, at_limit_ui, rounds_ui], outputs, show_progress="hidden")
            # The line under "who still speaks": what the picked choice does, and who that is with the boxes ticked now
            line_ui = self.__picked_option_description_ui.get(keys[1])
            at_limit_value = self.__identifier_to_config_value.get(keys[1])
            if line_ui is not None and isinstance(at_limit_value, ConfigValueSelection):
                def describe(must_speak: list[str] | None, picked: str | None, kind=kind, descriptions=at_limit_value.option_descriptions) -> str:
                    return picked_choice_line(kind, descriptions, must_speak, picked)
                for trigger in (must_speak_ui.change, at_limit_ui.change) + ((root.load,) if root is not None else ()):
                    trigger(describe, [must_speak_ui, at_limit_ui], line_ui, show_progress="hidden")

    def visit_ConfigValueInt(self, config_value: ConfigValueInt):
        def create_input_component(raw_config_value: ConfigValue) -> gr.Number:
            config_value = typing.cast(ConfigValueInt, raw_config_value)
            return gr.Number(value=config_value.value,
                        precision=0,
                        show_label=False, 
                        container=False)
        
        self.__create_config_value_ui_element(config_value, create_input_component)

    def visit_ConfigValueFloat(self, config_value: ConfigValueFloat):
        def create_input_component(raw_config_value: ConfigValue) -> gr.Number | gr.Slider:
            config_value = typing.cast(ConfigValueFloat, raw_config_value)
            if ConfigValueTag.slider in config_value.tags:
                return gr.Slider(value=config_value.value, minimum=config_value.min_value, maximum=config_value.max_value, step=0.1,
                                 show_label=False, container=False)
            return gr.Number(value=config_value.value,
                    precision=2,
                    show_label=False, 
                    container=False,
                    step=0.1)
        
        self.__create_config_value_ui_element(config_value, create_input_component)

    def visit_ConfigValueBool(self, config_value: ConfigValueBool):
        if config_value.identifier in {
            "save_summary_now_also_save_inner_thoughts",
            "save_summary_now_also_save_personal_reflection",
            "enable_bio_section_filter_for_memory",
        }:
            return
        def create_input_component(raw_config_value: ConfigValue) -> gr.Checkbox:
            config_value = typing.cast(ConfigValueBool, raw_config_value)
            return gr.Checkbox(label = config_value.name,
                                    value=config_value.value,
                                    show_label=False, 
                                    container=False,elem_classes="checkboxelement")
        
        self.__create_config_value_ui_element(config_value, create_input_component)

    def visit_ConfigValueString(self, config_value: ConfigValueString):
        if config_value.identifier == "bio_sections_to_exclude":
            return
        def create_input_component(raw_config_value: ConfigValue) -> gr.Text:
            config_value = typing.cast(ConfigValueString, raw_config_value)
            
            # Special handling for real world timestamp - make it readonly
            if config_value.identifier == "real_world_timestamp":
                return gr.Text(
                    value=config_value.value,
                    show_label=False,
                    container=False,
                    max_lines=1,
                    interactive=False,
                    placeholder="Click 'Get Timestamp' to display current date and time"
                )
            
            # Special handling for example profile JSON - make it readonly
            if config_value.identifier == "example_profile_json":
                return gr.Text(
                    value=config_value.value,
                    show_label=False,
                    container=False,
                    lines=15,  # Show all the JSON
                    max_lines=15,
                    elem_classes="multiline-textbox",
                    interactive=False  # Make it readonly
                )
            
            # Special handling for profile parameters - always allow multi-line
            if config_value.identifier == "profile_parameters":
                return gr.Text(
                    value=config_value.value,
                    show_label=False,
                    container=False,
                    lines=10,  # Start with 10 lines for JSON input
                    max_lines=20,  # Allow up to 20 lines
                    elem_classes="multiline-textbox",
                    placeholder="Enter JSON parameters here...\nExample:\n{\n  \"temperature\": 0.8,\n  \"max_tokens\": 250\n}"
                )
            
            # Special handling for LLM pools - always allow multi-line
            if config_value.identifier in [
                "llm_pool_one_on_one",
                "llm_pool_multi_npc",
                "sequential_llm_pool_one_on_one",
                "sequential_llm_pool_multi_npc",
            ]:
                is_sequential = config_value.identifier.startswith("sequential_")
                pool_kind = "sequential " if is_sequential else ""
                pool_type = "one-on-one" if "one_on_one" in config_value.identifier else "multi-NPC"
                return gr.Text(
                    value=config_value.value,
                    show_label=False,
                    container=False,
                    lines=8,  # Start with 8 lines for JSON pool input
                    max_lines=15,  # Allow up to 15 lines
                    elem_classes="multiline-textbox",
                    placeholder=f"Enter JSON array for {pool_kind}{pool_type} LLM pool...\nExample:\n[\n  {{\"service\": \"OpenRouter\", \"model\": \"deepseek/deepseek-chat\"}},\n  {{\"service\": \"OpenAI\", \"model\": \"gpt-4o-mini\"}}\n]"
                )
            
            # An online service's Settings Mantella doesn't have yet: fields written as JSON
            if config_value.identifier.endswith("_extra_request"):
                return gr.Text(value=config_value.value, show_label=False, container=False, lines=3, max_lines=12,
                               elem_classes="multiline-textbox", placeholder='{"speed": 1.1}')

            count_rows = self.__count_rows_in_text(config_value.value)
            if PromptProfileManager.is_known_type(config_value.identifier):
                # A prompt: always a text area, so its tags, variables and sections can be coloured (also a one-line prompt, which grows as it wraps)
                return gr.Text(value=config_value.value,
                        show_label=False,
                        container=False,
                        lines=count_rows,
                        max_lines=max(20, count_rows),
                        elem_classes=["multiline-textbox", *prompt_box_classes(config_value.identifier)])
            if count_rows == 1:
                return gr.Text(value=config_value.value,
                        show_label=False, 
                        container=False,
                        max_lines=1)
            else:
                return gr.Text(value=config_value.value,
                        show_label=False, 
                        container=False,
                        lines= count_rows,
                        elem_classes="multiline-textbox")
        
        # Add reload button for character data reload functionality and model profile buttons
        additional_buttons = []
        if config_value.identifier == "reload_character_data":
            def on_reload_click() -> str:
                """Handle the reload character data button click"""
                global _game_manager_ref
                try:
                    # Log that the button was clicked
                    log.info("Attempting character data reload via UI button...")
                    
                    # Use the global game manager reference
                    if _game_manager_ref:
                        log.info("Game manager reference found. Calling reload_character_data()...")
                        success = _game_manager_ref.reload_character_data()
                        if success:
                            log.info("Character data reloaded successfully!")
                            return " Character data reloaded successfully!"
                        else:
                            log.error("reload_character_data() returned False.")
                            return " Reload failed. Check logs for details."
                    else:
                        log.warning("Button clicked, but game manager reference is not set. The game might not have been started.")
                        return " Game not started. Please start the game first."
                        
                except Exception as e:
                    log.error(f"Error during character data reload: {e}", exc_info=True)
                    return f" An error occurred: {str(e)}"

            additional_buttons.append(("Reload", on_reload_click))

        elif config_value.identifier == "save_summary_now":
            def on_save_summary_click() -> str:
                """Trigger summary/log saving without ending the current conversation."""
                global _game_manager_ref
                try:
                    if _game_manager_ref:
                        log.info("Manual summary trigger via UI button (will save summary/log if enabled)...")
                        ok = _game_manager_ref.save_summary_only()
                        if ok:
                            return " Summary/log save triggered (conversation continues)."
                        return " No active conversation to summarize."
                    else:
                        log.warning("Manual summary clicked, but game manager reference is not set. The game might not have been started.")
                        return " Game not started. Please start the game first."
                except Exception as e:
                    log.error(f"Error triggering summary-only via UI: {e}", exc_info=True)
                    return f" An error occurred: {str(e)}"

            additional_buttons.append(("Save Summary", on_save_summary_click))

        elif config_value.identifier == "save_inner_thoughts_now":
            def on_save_inner_thoughts_click() -> str:
                """Trigger private-thought saving without ending the current conversation."""
                global _game_manager_ref
                try:
                    if _game_manager_ref:
                        log.info("Manual inner-thoughts trigger via UI button...")
                        ok = _game_manager_ref.save_inner_thoughts_only()
                        if ok:
                            return " Inner thoughts save triggered (conversation continues)."
                        return " No active conversation to save thoughts for."
                    else:
                        log.warning("Manual inner thoughts clicked, but game manager reference is not set. The game might not have been started.")
                        return " Game not started. Please start the game first."
                except Exception as e:
                    log.error(f"Error triggering inner-thoughts save via UI: {e}", exc_info=True)
                    return f" An error occurred: {str(e)}"

            additional_buttons.append(("Save Inner Thoughts", on_save_inner_thoughts_click))

        elif config_value.identifier == "save_personal_reflection_now":
            def on_save_personal_reflection_click() -> str:
                """Trigger personal-reflection saving without ending the current conversation."""
                global _game_manager_ref
                try:
                    if _game_manager_ref:
                        log.info("Manual personal-reflection trigger via UI button...")
                        ok = _game_manager_ref.save_personal_reflection_only()
                        if ok:
                            return " Personal reflection save triggered (conversation continues)."
                        return " No active conversation to save personal reflections for."
                    else:
                        log.warning("Manual personal reflection clicked, but game manager reference is not set. The game might not have been started.")
                        return " Game not started. Please start the game first."
                except Exception as e:
                    log.error(f"Error triggering personal-reflection save via UI: {e}", exc_info=True)
                    return f" An error occurred: {str(e)}"

            additional_buttons.append(("Save Personal Reflection", on_save_personal_reflection_click))
        
        elif config_value.identifier == "real_world_timestamp":
            def on_get_timestamp_click() -> str:
                """Display the current real-world timestamp as Unix timestamp (seconds since epoch)."""
                from datetime import datetime, timezone
                try:
                    return str(int(datetime.now(timezone.utc).timestamp()))
                except Exception as e:
                    log.error(f"Error getting timestamp: {e}")
                    return f"Error: {str(e)}"
            additional_buttons.append(("Get Timestamp", on_get_timestamp_click))
        
        # Add model profile management buttons
        elif config_value.identifier == "profile_parameters":
            def on_save_profile_click() -> str:
                """Handle save profile button click"""
                log.info("Save Profile button clicked")
                try:
                    # Get values from profile config fields
                    service = self.__identifier_to_config_value.get("profile_selected_service", None)
                    model = self.__identifier_to_config_value.get("profile_selected_model", None)
                    parameters_json = self.__identifier_to_config_value.get("profile_parameters", None)
                    
                    models_log.debug(f"Profile save attempt - Service: {service.value if service else 'None'}, Model: {model.value if model else 'None'}")
                    
                    # Check if config values exist
                    if not all([service, model, parameters_json]):
                        models_log.error("Missing profile configuration fields")
                        return " Please select Service, Model, and enter Parameters"
                    
                    # Check if values are not empty/default
                    if not service.value or service.value.strip() == "":
                        models_log.error("Service not selected")
                        return " Please select a Service"
                    
                    if not model.value or model.value.strip() == "" or model.value == "Select a service first":
                        models_log.error("Model not selected") 
                        return " Please select a Model"
                    
                    # Handle empty parameters - allow empty JSON object as default
                    parameters_text = parameters_json.value.strip() if parameters_json.value else ""
                    if not parameters_text:
                        # Default to empty JSON object if no parameters provided
                        parameters_text = "{}"
                        parameters_json.value = parameters_text
                    
                    # Parse JSON parameters
                    try:
                        import json
                        parameters = json.loads(parameters_text)
                        if not isinstance(parameters, dict):
                            models_log.error("Parameters is not a JSON object")
                            return " Parameters must be a JSON object"
                    except json.JSONDecodeError as e:
                        models_log.error(f"JSON parsing error: {e}")
                        return f" Invalid JSON: {str(e)}"
                    
                    # Create or update the profile
                    success = self.get_profile_manager().create_or_update_profile(
                        service.value,
                        model.value,
                        parameters
                    )
                    
                    if success:
                        models_log.info(f"Profile saved successfully for {service.value}/{model.value}")
                        # Update the parameters field with properly formatted JSON
                        formatted_json = json.dumps(parameters, indent=4)
                        parameters_json.value = formatted_json
                        return formatted_json
                    else:
                        models_log.error(f"Failed to save profile for {service.value}/{model.value}")
                        return " ❌ Failed to save profile. Check logs for details."
                        
                except Exception as e:
                    models_log.error(f"Error saving profile: {e}")
                    # Return current parameters unchanged on error
                    return f" ❌ Error: {str(e)}"
            
            def on_delete_profile_click() -> str:
                """Handle delete profile button click"""
                try:
                    service = self.__identifier_to_config_value.get("profile_selected_service", None)
                    model = self.__identifier_to_config_value.get("profile_selected_model", None)
                    
                    if not all([service, model]):
                        return " Please select Service and Model"
                    
                    # Map display names to short identifiers for profile ID
                    service_map = {
                        "OpenRouter": "or",
                        "NanoGPT": "nano", 
                        "OpenAI": "openai"
                    }
                    service_id = service_map.get(service.value, service.value.lower())
                    profile_id = f"{service_id}:{model.value}"
                    success = self.get_profile_manager().delete_profile(profile_id)
                    
                    if success:
                        models_log.info(f"Profile deleted successfully for {service.value}/{model.value}")
                        # Clear parameters to default
                        parameters_config = self.__identifier_to_config_value.get("profile_parameters")
                        if parameters_config:
                            default_json = """{
    "max_tokens": 250,
    "temperature": 0.8,
    "top_p": 1.0,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "stop": ["#"]
}"""
                            parameters_config.value = default_json
                            return default_json
                    else:
                        models_log.error(f"Failed to delete profile for {service.value}/{model.value} (may not exist)")
                        # Return current parameters unchanged
                        parameters_config = self.__identifier_to_config_value.get("profile_parameters")
                        return parameters_config.value if parameters_config else ""
                        
                except Exception as e:
                    models_log.error(f"Error deleting profile: {e}")
                    # Return current parameters unchanged on error
                    parameters_config = self.__identifier_to_config_value.get("profile_parameters")
                    return parameters_config.value if parameters_config else ""
            
            additional_buttons.append(("Save", on_save_profile_click))
            additional_buttons.append(("Delete", on_delete_profile_click))
            
        self.__create_config_value_ui_element(config_value, create_input_component, False, True, True, additional_buttons)
    
    def __count_rows_in_text(self, text: str) -> int:
        count_CRLF = text.count("\r\n")
        count_newline = text.count("\n")
        return count_CRLF + (count_newline - count_CRLF) + 1

    def visit_ConfigValueSelection(self, config_value: ConfigValueSelection):
        # Define special handlers for different identifiers
        special_handlers: Dict[str, ModelConfig] = {
            "model": {
                "dependent_config": "llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "vision_model": {
                "dependent_config": "vision_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "multi_npc_model": {
                "dependent_config": "multi_npc_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "game_master_model": {
                "dependent_config": "game_master_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "game_master_decision_model": {
                "dependent_config": "game_master_decision_service",
                "model_list_getter": ClientBase.get_decision_model_list,
            },
            "summary_model": {
                "dependent_config": "summary_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "thoughts_model": {
                "dependent_config": "thoughts_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "reflection_model": {
                "dependent_config": "reflection_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "npc_event_model": {
                "dependent_config": "npc_event_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "fallback_model": {
                "dependent_config": "fallback_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "advanced_actions_model": {
                "dependent_config": "advanced_actions_llm_api",
                "model_list_getter": ClientBase.get_model_list,
            },
            "profile_selected_model": {
                "dependent_config": "profile_selected_service",
                "model_list_getter": ClientBase.get_model_list,
            },
            "model_summaries": {
                "dependent_config": "llm_api",
                "model_list_getter": ClientBase.get_model_list,
            }
        }

        def update_model_list() -> gr.Dropdown:
            current_config = self.__identifier_to_config_value[config_value.identifier]
            return create_input_component(current_config)

        stt_preset = next((p for p in ONLINE_PRESETS if p.model_setting == config_value.identifier), None)

        def update_speech_model_list() -> gr.Dropdown:
            """An online speech-to-text choice: the speech models its service lists, with the recommended ones first"""
            llm_api = self.__setting("llm_api")
            choices = speech_models(stt_preset, llm_api.value if llm_api is not None else "")
            return gr.Dropdown(value=config_value.value, choices=choices, multiselect=False, allow_custom_value=True, show_label=False, container=False)

        def create_input_component(raw_config_value: ConfigValue) -> gr.Dropdown:
            config_value = typing.cast(ConfigValueSelection, raw_config_value)
            

            
            # Handle special cases
            handler = special_handlers.get(config_value.identifier)
            if handler:
                service: str = self.__identifier_to_config_value[handler["dependent_config"]].value
                # Choose appropriate secret key file based on service and model type
                if config_value.identifier == 'vision_model':
                    secret_key_file = 'IMAGE_SECRET_KEY.txt'
                else:
                    from src.llm.key_file_resolver import key_file_resolver
                    # Use the first (primary) key file from the resolver
                    key_files = key_file_resolver.get_key_files_for_service(service, 'GPT_SECRET_KEY.txt')
                    secret_key_file = key_files[0] if key_files else 'GPT_SECRET_KEY.txt'
                default_model = 'meta-llama/llama-3.2-11b-vision-instruct:free' if config_value.identifier == 'vision_model' else 'google/gemma-2-9b-it:free'
                is_vision = True if config_value.identifier == 'vision_model' else False
                model_list = handler["model_list_getter"](service, secret_key_file, default_model, is_vision)
                selected_model = config_value.value
                
                # Leave an unselected summary-fallback model empty instead of replacing it with the default model
                if config_value.identifier in SUMMARY_FALLBACK_MODEL_IDS and not selected_model:
                    selected_model = None
                elif not model_list.is_model_in_list(selected_model):
                    selected_model = model_list.default_model
                    
                return gr.Dropdown(
                    value=selected_model,                        
                    choices=model_list.available_models,
                    multiselect=False,
                    allow_custom_value=model_list.allows_manual_model_input,
                    show_label=False,
                    container=False
                )
            

            
            # Default handling for non-special cases
            if config_value.identifier in ("tts_service", "tts_backup_service"):
                TTSDefinitions.refresh_voice_service_options(config_value) # the player's voice setups are listed too
            choices = config_value.options
            if config_value.option_labels:
                # (shown text, saved option): the dropdown shows the label, and the option is still what is saved. A saved value no
                # longer offered (a model that has ended) still shows its label
                shown = list(config_value.options)
                if config_value.value not in shown and config_value.value in config_value.option_labels:
                    shown.append(config_value.value)
                choices = [(config_value.option_labels.get(option, option), option) for option in shown]
            if ConfigValueTag.radio_buttons in config_value.tags:
                return gr.Radio(value=config_value.value, choices=choices, show_label=False, container=False)
            return gr.Dropdown(
                value=config_value.value,
                choices=choices,
                multiselect=False,
                allow_custom_value=config_value.allows_custom_value,
                show_label=False,
                container=False
            )

        # Add update button only for special cases
        additional_buttons: list[tuple[str, Callable[[], Any]]] = []
        if config_value.identifier in special_handlers:
            additional_buttons = [("Update", update_model_list)]
        elif stt_preset is not None and stt_preset.model_list: # ElevenLabs keeps its fixed list
            additional_buttons = [("Update", update_speech_model_list)]
        


        self.__create_config_value_ui_element(
            config_value,
            create_input_component,
            additional_buttons=additional_buttons
        )
        
        # Track model dependencies for later setup
        handler = special_handlers.get(config_value.identifier)
        if handler:
            self.__model_dependencies.append((config_value.identifier, handler["dependent_config"]))

    def visit_ConfigValueMultiSelection(self, config_value: ConfigValueMultiSelection):
        def create_input_component(raw_config_value: ConfigValue) -> Any:
            config_value = typing.cast(ConfigValueMultiSelection, raw_config_value)
            values: list[str | int | float] = []
            for s in config_value.value:
                values.append(s)
            if ConfigValueTag.tick_boxes in config_value.tags:
                return gr.CheckboxGroup(value=values, choices=config_value.options, show_label=False, container=False) # type: ignore
            return gr.Dropdown(value=values,                        
                choices=config_value.options, # type: ignore
                multiselect=True,
                allow_custom_value=False, 
                show_label=False,
                container=False)
            
        self.__create_config_value_ui_element(config_value, create_input_component)

    def visit_ConfigValuePath(self, config_value: ConfigValuePath):
        def on_pick_click() -> str | None:
            return config_value.show_file_or_path_picker_dialog()

        def create_input_component(raw_config_value: ConfigValue) -> gr.Text:
            config_value = typing.cast(ConfigValuePath, raw_config_value)
            return gr.Text(value=config_value.value, show_label=False, container=False, max_lines=1)
        
        self.__create_config_value_ui_element(config_value, create_input_component, True, True, True, [("Browse...", on_pick_click)])



def picked_choice_line(kind: str, descriptions: dict[str, str], must_speak: list[str] | None, picked: str | None) -> str:
    """The line under "who still speaks": what the picked choice does, then who still gets a turn with the boxes ticked now"""
    right_now = who_still_speaks_now(kind, list(must_speak or []), picked or "")
    return "\n\n".join(part for part in (descriptions.get(picked or "", ""), right_now) if part)


def talk_greying(must_speak: list[str] | None, at_reply_limit: str | None, extra_rounds: float | None) -> list[Any]:
    """Greys out "who still speaks", Replies in between and Extra rounds while they can't change anything"""
    greyed = greyed_choices(list(must_speak or []), at_reply_limit or "", extra_rounds)
    return [gr.update(interactive=not greyed[AT_REPLY_LIMIT]), gr.update(interactive=not greyed[REPLIES_IN_BETWEEN]),
            gr.update(interactive=not greyed[EXTRA_ROUNDS])]
