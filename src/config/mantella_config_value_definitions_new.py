from typing import Any, Callable
from src.conversation.action import Action
from src.config.config_values import ConfigValues
from src.config.types.config_value_bool import ConfigValueBool
from src.config.types.config_value import ConfigValue
from src.config.types.config_value_group import ConfigValueGroup
from src.config.definitions.game_definitions import GameDefinitions
from src.config.definitions.action_definitions import ACTION_KINDS, ADVANCED_ACTIONS_DEFAULT_MODEL, ActionDefinitions
from src.config.definitions.language_definitions import LanguageDefinitions
from src.config.definitions.llm_definitions import LLMDefinitions
from src.config.definitions.conversation_definitions import (CONVERSATION_EVENT, CONVERSATION_PAGE, DIRECTOR, GROUP, NEARBY_EVENT, NPC_EVENT, RANDOM_EVENT, SHARED,
                                                             SHOUT, ConversationDefinitions)
from src.config.types.config_value_group import ConfigValueGroup
from src.config.types.config_value_path import ConfigValuePath
from src.config.types.config_value_string import ConfigValueString
from src.config.definitions.other_definitions import OtherDefinitions
from src.config.definitions.log_definitions import LOG_PART, LOG_PART_INTRO, LogDefinitions
from src.config.definitions.startup_definitions import StartupDefinitions
from src.config.definitions.prompt_definitions import PromptDefinitions
from src.config.definitions.stt_definitions import PAGE_DESCRIPTION as STT_PAGE_DESCRIPTION, SERVICE_PART as STT_SERVICE_PART, TALKING_PART as STT_TALKING_PART, STTDefinitions
from src.stt_backends.presets import MOONSHINE_CHOICE, PARAKEET_CHOICE, PRESETS, WHISPER_CHOICE
from src.config.definitions.tts_definitions import TTSDefinitions, SERVICE_VOLUMES, PIPER_PART, POCKET_TTS_GROUP, SPEED_PART, XTTS_PART, XVASYNTH_PART
from src.config.definitions.vision_definitions import VisionDefinitions
from src.config.definitions.model_profile_definitions import ModelProfileDefinitions
import sys

# Folded parts of the LLM page
THOUGHTS_PART = "Thoughts and Reflections"
QUALITY_OF_LIFE_PART = "Quality of Life"
NARRATIONS_PART = "Narrations"
FEATURE_TOGGLES_PART = "Feature Toggles"
ADVANCED_PART = "Advanced"

class MantellaConfigValueDefinitionsNew:
    @staticmethod
    def get_config_values(is_integrated: bool, actions: list[Action], on_value_change_callback: Callable[..., Any] | None = None) -> ConfigValues:
        result: ConfigValues = ConfigValues()
        # hidden_category= ConfigValueGroup("Hidden", "Hidden", "Don't show these on the UI", on_value_change_callback, is_hidden=True)
        # hidden_category.add_config_value(ConfigValueBool("show_advanced","","", False, is_hidden=True))
        # result.add_base_group(hidden_category)

        # Always shown, even when Mantella works out the game's folders for itself: Your keys are at the top of this page
        game_category = ConfigValueGroup("Game", "Game", "Settings for the games Mantella supports.", on_value_change_callback)
        game_category.add_config_value(GameDefinitions.get_game_config_value())
        game_category.add_config_value(GameDefinitions.get_skyrim_mod_folder_config_value())
        game_category.add_config_value(GameDefinitions.get_skyrimvr_mod_folder_config_value())
        game_category.add_config_value(GameDefinitions.get_fallout4_mod_folder_config_value())
        game_category.add_config_value(GameDefinitions.get_fallout4vr_mod_folder_config_value())
        game_category.add_config_value(GameDefinitions.get_fallout4_folder_config_value())
        game_category.add_config_value(GameDefinitions.get_fallout4vr_folder_config_value())
        result.add_base_group(game_category)

        # Hidden settings (persisted in config.ini but not shown in UI)
        hidden_category = ConfigValueGroup("Hidden", "Hidden", "Hidden settings", on_value_change_callback, is_hidden=True)
        hidden_category.add_config_value(ConfigValueString(
            "bio_override_csv_path",
            "Bio Override CSV (optional)",
            "Optional: Path to the CSV override file to append base-only NPC bios to. You can enter a full CSV file path, or a directory (the file 'character_overrides.csv' will be used). Leave empty to use the default personal override file.",
            "",
            []
        ))
        # Bio Editor LLM settings (persisted but not shown in Settings UI)
        from src.config.types.config_value_selection import ConfigValueSelection
        from src.config.types.config_value_int import ConfigValueInt
        from src.config.types.config_value_float import ConfigValueFloat
        from src.config.types.config_value_bool import ConfigValueBool
        hidden_category.add_config_value(ConfigValueSelection(
            "bio_llm_api",
            "Bio LLM Service",
            "Service used by Bio Editor LLM requests.",
            "OpenRouter",
            ["OpenRouter", "OpenAI", "NanoGPT", "KoboldCpp", "textgenwebui"],
            allows_free_edit=True
        ))
        hidden_category.add_config_value(ConfigValueString(
            "bio_llm_model",
            "Bio LLM Model",
            "Model used by Bio Editor LLM requests.",
            "openrouter/free",
            []
        ))
        hidden_category.add_config_value(ConfigValueBool(
            "bio_llm_apply_profile",
            "Apply Bio LLM Profile",
            "Whether to apply model profile for Bio Editor requests.",
            False,
            []
        ))
        hidden_category.add_config_value(ConfigValueBool(
            "bio_llm_omit_stop",
            "Omit Bio LLM Stop Sequences",
            "If enabled, the stop field is removed from Bio Editor LLM requests (including those injected by a model profile).",
            True,
            []
        ))
        hidden_category.add_config_value(ConfigValueFloat(
            "bio_llm_temperature_override",
            "Bio LLM Temperature Override",
            "If > -1, overrides temperature for Bio Editor LLM requests.",
            -1.0,
            -1.0,
            2.0,
            []
        ))
        hidden_category.add_config_value(ConfigValueInt(
            "bio_llm_max_tokens_override",
            "Bio LLM Max Tokens Override",
            "If > 0, overrides max_tokens for Bio Editor LLM requests.",
            -1,
            -1,
            9999999,
            []
        ))
        hidden_category.add_config_value(ConfigValueString(
            "bio_prompt_profiles",
            "Bio Prompt Profiles",
            "JSON object mapping prompt names to prompt text for Bio Editor.",
            "{}",
            []
        ))
        hidden_category.add_config_value(ConfigValueString(
            "bio_prompt_selected",
            "Bio Prompt Selected",
            "Currently selected Bio Editor prompt name.",
            "",
            []
        ))
        hidden_category.add_config_value(ConfigValueString(
            "ui_tab_order",
            "UI Tab Order",
            "Comma-separated settings tab names in the order they should appear. Empty uses the default order.",
            "",
            []
        ))
        hidden_category.add_config_value(ConfigValueString(
            "bio_editor_player_name",
            "Bio Editor Player Name",
            "Player name used for {player_name} when the Bio Editor generates a personal reflection.",
            "",
            []
        ))
        result.add_base_group(hidden_category)
        
        llm_category = ConfigValueGroup("LLM", "Large Language Model", "Settings for the LLM providers and the LLMs themselves.", on_value_change_callback)
        llm_category.add_config_value(LLMDefinitions.get_llm_api_config_value())
        llm_category.add_config_value(LLMDefinitions.get_model_config_value())
        llm_category.add_config_value(LLMDefinitions.get_multi_npc_llm_api_config_value())
        llm_category.add_config_value(LLMDefinitions.get_multi_npc_model_config_value())
        llm_category.add_config_value(LLMDefinitions.get_game_master_llm_api_config_value())
        llm_category.add_config_value(LLMDefinitions.get_game_master_model_config_value())
        llm_category.add_config_value(LLMDefinitions.get_game_master_use_decision_model_config_value())
        llm_category.add_config_value(LLMDefinitions.get_game_master_decision_service_config_value())
        llm_category.add_config_value(LLMDefinitions.get_game_master_decision_model_config_value())
        llm_category.add_config_value(LLMDefinitions.get_summary_llm_api_config_value())
        llm_category.add_config_value(LLMDefinitions.get_summary_model_config_value())
        # (key, label, can use the Summary model instead). The NPC Event Generator and the Fallback always use their own model
        memory_tasks = [("thoughts", "Private Thoughts", True), ("reflection", "Personal Reflection", True), ("npc_event", "NPC Event Generator", False), ("fallback", "Fallback", False),
                        ("advanced_actions", "Advanced Actions", False)]
        for key, label, has_summary_option in memory_tasks:
            if has_summary_option:
                llm_category.add_config_value(LLMDefinitions.get_memory_task_use_summary_model_config_value(key, label))
        llm_category.add_config_value(LLMDefinitions.get_fallback_llm_enabled_config_value())
        for key, label, has_summary_option in memory_tasks:
            if key == "advanced_actions": # Its on/off switch sits right above its own service and model
                llm_category.add_config_value(ActionDefinitions.get_advanced_actions_own_model_config_value())
            llm_category.add_config_value(LLMDefinitions.get_memory_task_llm_api_config_value(key, label, has_summary_option))
            if key == "advanced_actions": # The other tasks' default model can't pick actions
                llm_category.add_config_value(LLMDefinitions.get_memory_task_model_config_value(key, label, has_summary_option, ADVANCED_ACTIONS_DEFAULT_MODEL))
            else:
                llm_category.add_config_value(LLMDefinitions.get_memory_task_model_config_value(key, label, has_summary_option))
        llm_category.add_config_value(LLMDefinitions.get_max_response_sentences_single_config_value())
        llm_category.add_config_value(LLMDefinitions.get_max_response_sentences_multi_config_value())
        llm_category.add_config_value(LLMDefinitions.get_llm_params_config_value())
        llm_category.add_config_value(LLMDefinitions.get_multi_npc_llm_params_config_value())
        llm_category.add_config_value(LLMDefinitions.get_game_master_llm_params_config_value())
        llm_category.add_config_value(LLMDefinitions.get_summary_llm_params_config_value())
        for key, label, has_summary_option in memory_tasks:
            llm_category.add_config_value(LLMDefinitions.get_memory_task_llm_params_config_value(key, label, has_summary_option))
        
        
        # llm_category.add_config_value(LLMDefinitions.get_llm_priority_config_value())
 
        # llm_category.add_config_value(LLMDefinitions.get_try_filter_narration())
        llm_category.add_config_value(OtherDefinitions.get_conversation_summary_enabled_config_value())
        llm_category.add_config_value(OtherDefinitions.get_inner_monologue_enabled_config_value())
        llm_category.add_config_value(OtherDefinitions.get_personal_reflection_enabled_config_value())
        # Folded parts of the page, in the order of the loop below. Switches beside switches and boxes beside boxes
        thoughts = [
            OtherDefinitions.get_inner_monologue_load_count_config_value().in_row("thoughts_sending_row"),
            OtherDefinitions.get_personal_reflection_load_mode_config_value().in_row("thoughts_sending_row"),
            LLMDefinitions.get_read_thoughts_after_sentence_cap_config_value().in_row("turn_thoughts_switches_row"),
            LLMDefinitions.get_game_master_include_turn_thoughts_config_value().in_row("turn_thoughts_switches_row"),
            LLMDefinitions.get_turn_thoughts_to_keep_config_value(),
        ]
        quality_of_life = [
            LLMDefinitions.get_multi_npc_bios_only_config_value(),
            LLMDefinitions.get_save_bios_for_new_npcs_config_value(),
            LLMDefinitions.get_sonnet_prompt_caching_config_value(),
            LLMDefinitions.get_drop_last_sentences_single_config_value(),
            LLMDefinitions.get_replace_braille_blank_config_value(),
            LLMDefinitions.get_enable_decensor_bypass_config_value(),
            LLMDefinitions.get_decensor_assistant_placement_config_value(),
            OtherDefinitions.get_random_llm_one_on_one_enabled_config_value(),
            OtherDefinitions.get_random_llm_multi_npc_enabled_config_value(),
            OtherDefinitions.get_random_llm_one_on_one_per_request_enabled_config_value(),
            OtherDefinitions.get_random_llm_multi_npc_per_request_enabled_config_value(),
            OtherDefinitions.get_llm_pool_one_on_one_config_value(),
            OtherDefinitions.get_llm_pool_multi_npc_config_value(),
        ]
        narrations = [
            LLMDefinitions.get_narration_handling(),
            LLMDefinitions.get_narrator_voice(),
            LLMDefinitions.get_narration_start_indicators(),
            LLMDefinitions.get_narration_end_indicators(),
            LLMDefinitions.get_speech_start_indicators(),
            LLMDefinitions.get_speech_end_indicators(),
            LLMDefinitions.get_narration_indicators(),
        ]
        feature_toggles = [ # Per-Character LLM Overrides on its own row, the two tag settings side by side below it
            LLMDefinitions.get_allow_per_character_llm_overrides_config_value(),
            LLMDefinitions.get_enable_character_tag_reading_config_value().in_row("character_tags_row"),
            LLMDefinitions.get_auto_quest_tag_outcomes_config_value().in_row("character_tags_row"),
        ]
        advanced = [ # a folded part of its own, so Narrations can come after it
            LLMDefinitions.get_llm_response_timeout_config_value(),
            LLMDefinitions.get_custom_token_count_config_value(),
            LLMDefinitions.get_multi_npc_custom_token_count_config_value(),
            LLMDefinitions.get_game_master_custom_token_count_config_value(),
            LLMDefinitions.get_summary_custom_token_count_config_value(),
            *(LLMDefinitions.get_memory_task_custom_token_count_config_value(key, label, has_summary_option)
              for key, label, has_summary_option in memory_tasks),
        ]
        for part, settings in ((QUALITY_OF_LIFE_PART, quality_of_life), (FEATURE_TOGGLES_PART, feature_toggles), (THOUGHTS_PART, thoughts),
                               (NARRATIONS_PART, narrations), (ADVANCED_PART, advanced)):
            for setting in settings:
                llm_category.add_config_value(setting.in_folded_part(part))

        llm_category.add_config_value(LLMDefinitions.get_multi_conversation_director_mode_config_value())
        llm_category.add_config_value(LLMDefinitions.get_multi_npc_game_master_mode_config_value())
        llm_category.add_config_value(OtherDefinitions.get_sequential_llm_one_on_one_per_request_enabled_config_value())
        llm_category.add_config_value(OtherDefinitions.get_sequential_llm_multi_npc_per_request_enabled_config_value())
        llm_category.add_config_value(OtherDefinitions.get_sequential_llm_pool_one_on_one_config_value())
        llm_category.add_config_value(OtherDefinitions.get_sequential_llm_pool_multi_npc_config_value())

               
        
        #llm_category.add_config_value(LLMDefinitions.get_llm_custom_service_url_config_value())
        
        
        
        
        # llm_category.add_config_value(LLMDefinitions.get_stop_llm_generation_on_assist_keyword())

        # Multi-NPC LLM Configuration
        llm_category.add_config_value(LLMDefinitions.get_enable_bio_section_filter_config_value())
        llm_category.add_config_value(LLMDefinitions.get_bio_sections_to_exclude_config_value())
        llm_category.add_config_value(LLMDefinitions.get_enable_bio_section_filter_for_memory_config_value())


        result.add_base_group(llm_category)

        for group in MantellaConfigValueDefinitionsNew.__conversation_groups(on_value_change_callback, actions):
            result.add_base_group(group)

        tts_category = ConfigValueGroup("TTS", "Text-to-Speech", "Settings for the TTS methods Mantella supports.", on_value_change_callback)
        tts_category.add_config_value(TTSDefinitions.get_tts_service_config_value())
        tts_category.add_config_value(TTSDefinitions.get_tts_backup_service_config_value())
        for config_value in TTSDefinitions.get_online_service_config_values(): # shown in the service's own part, above its Volume
            tts_category.add_config_value(config_value)
        for service in SERVICE_VOLUMES: # shown in the service's own part of the page
            tts_category.add_config_value(TTSDefinitions.get_service_volume_config_value(service))
        tts_category.add_config_value(TTSDefinitions.get_speed_config_value(POCKET_TTS_GROUP)) # next to its Volume: Pocket TTS has no models
        tts_category.add_config_value(TTSDefinitions.get_xvasynth_folder_config_value().in_folded_part(XVASYNTH_PART))
        tts_category.add_config_value(TTSDefinitions.get_xtts_folder_config_value().in_folded_part(XTTS_PART))
        tts_category.add_config_value(TTSDefinitions.get_piper_folder_config_value(is_integrated).in_folded_part(PIPER_PART))
        # Speed and lip sync: for every service, folded at the top of the page
        tts_category.add_config_value(TTSDefinitions.get_number_words_tts_config_value().in_folded_part(SPEED_PART))
        tts_category.add_config_value(TTSDefinitions.get_lip_generation_config_value().in_folded_part(SPEED_PART))
        tts_category.add_config_value(TTSDefinitions.get_fast_response_mode_config_value().in_folded_part(SPEED_PART))
        tts_category.add_config_value(TTSDefinitions.get_fast_response_mode_volume_config_value().in_folded_part(SPEED_PART))
        tts_category.add_config_value(TTSDefinitions.get_lipgen_folder_config_value(is_integrated).in_folded_part(SPEED_PART))
        tts_category.add_config_value(TTSDefinitions.get_facefx_folder_config_value(is_integrated).in_folded_part(SPEED_PART))
        tts_category.add_config_value(LLMDefinitions.get_wait_time_buffer_config_value().in_folded_part(SPEED_PART))
        tts_category.add_config_value(TTSDefinitions.get_xtts_url_config_value().in_folded_part(XTTS_PART))
        tts_category.add_config_value(TTSDefinitions.get_xtts_default_model_config_value().in_folded_part(XTTS_PART))
        tts_category.add_config_value(TTSDefinitions.get_xtts_device_config_value().in_folded_part(XTTS_PART))
        tts_category.add_config_value(TTSDefinitions.get_xtts_deepspeed_config_value().in_folded_part(XTTS_PART))
        tts_category.add_config_value(TTSDefinitions.get_xtts_lowvram_config_value().in_folded_part(XTTS_PART))
        tts_category.add_config_value(TTSDefinitions.get_xtts_data_config_value().in_folded_part(XTTS_PART))
        tts_category.add_config_value(TTSDefinitions.get_xtts_accent_config_value().in_folded_part(XTTS_PART))
        tts_category.add_config_value(TTSDefinitions.get_tts_print_config_value().in_folded_part(SPEED_PART))
        tts_category.add_config_value(TTSDefinitions.get_tts_process_device_config_value().in_folded_part(XVASYNTH_PART))
        tts_category.add_config_value(TTSDefinitions.get_pace_config_value().in_folded_part(XVASYNTH_PART))
        tts_category.add_config_value(TTSDefinitions.get_use_cleanup_config_value().in_folded_part(XVASYNTH_PART))
        tts_category.add_config_value(TTSDefinitions.get_use_sr_config_value().in_folded_part(XVASYNTH_PART))
        result.add_base_group(tts_category) # the voice parts of the page, with files of their own next to config.ini, are drawn with it

        # The Speech-to-Text page: Talking, then Service with the Test button at its end (settings_ui_constructor draws them)
        stt_category = ConfigValueGroup("STT", "Speech-to-Text", STT_PAGE_DESCRIPTION, on_value_change_callback)
        talking = [ # switches beside switches and boxes beside boxes, so no panel is stretched to its neighbour's height
            STTDefinitions.get_ptt_enabled_config_value(), STTDefinitions.get_proactive_mic_mode_config_value(),
            STTDefinitions.get_ptt_hotkey_config_value(), STTDefinitions.get_min_refresh_secs_config_value(), # each under the switch that greys it out
            STTDefinitions.get_audio_threshold_config_value().in_row("stt_waiting"), STTDefinitions.get_pause_threshold_config_value().in_row("stt_waiting"),
            STTDefinitions.get_listen_timeout_config_value().in_row("stt_waiting"),
            STTDefinitions.get_allow_interruption_config_value().in_row("stt_switches"), STTDefinitions.get_play_cough_sound_config_value().in_row("stt_switches"),
            STTDefinitions.get_save_mic_input_config_value().in_row("stt_switches"),
        ]
        models = {preset.name: STTDefinitions.get_model_config_value(preset) for preset in PRESETS} # one Model setting per choice, each shown only while its choice is picked
        service = [
            STTDefinitions.get_stt_service_config_value(), STTDefinitions.get_stt_language_config_value(),
            STTDefinitions.get_stt_online_service_config_value(), # its Key box is drawn right under it
            *(models[preset.name] for preset in PRESETS if preset.online),
            models[MOONSHINE_CHOICE], STTDefinitions.get_moonshine_folder_config_value(is_integrated),
            models[WHISPER_CHOICE], STTDefinitions.get_process_device_config_value(), STTDefinitions.get_stt_translate_config_value(),
            models[PARAKEET_CHOICE],
            STTDefinitions.get_custom_format_config_value(), STTDefinitions.get_custom_address_config_value(),
            STTDefinitions.get_custom_model_config_value(), STTDefinitions.get_custom_timeout_config_value(), # Custom Service's Key box is drawn under these two
            STTDefinitions.get_stt_first_names_config_value(),
        ]
        for part, settings in ((STT_TALKING_PART, talking), (STT_SERVICE_PART, service)):
            for setting in settings:
                stt_category.add_config_value(setting.in_folded_part(part))
        result.add_base_group(stt_category)

        vision_category = ConfigValueGroup("Vision", "Vision", "Vision settings.", on_value_change_callback)
        vision_category.add_config_value(VisionDefinitions.get_vision_enabled_config_value())
        vision_category.add_config_value(VisionDefinitions.get_low_resolution_mode_config_value())
        vision_category.add_config_value(VisionDefinitions.get_save_screenshot_config_value())
        vision_category.add_config_value(VisionDefinitions.get_image_quality_config_value())
        vision_category.add_config_value(VisionDefinitions.get_resize_method_config_value())
        vision_category.add_config_value(VisionDefinitions.get_capture_offset_config_value())
        vision_category.add_config_value(VisionDefinitions.get_custom_vision_model_config_value())
        vision_category.add_config_value(VisionDefinitions.get_vision_llm_api_config_value())
        vision_category.add_config_value(VisionDefinitions.get_vision_model_config_value())
        vision_category.add_config_value(VisionDefinitions.get_vision_custom_token_count_config_value())
        vision_category.add_config_value(VisionDefinitions.get_vision_llm_params_config_value())
        vision_category.add_config_value(VisionDefinitions.get_use_game_screenshots_config_value())
        result.add_base_group(vision_category)

        language_category = ConfigValueGroup("Language", "Language", "Change the language used by Mantella as well as keywords.", on_value_change_callback)
        language_category.add_config_value(LanguageDefinitions.get_language_config_value())
        language_category.add_config_value(LanguageDefinitions.get_end_conversation_keyword_config_value())
        language_category.add_config_value(LanguageDefinitions.get_goodbye_npc_response())
        language_category.add_config_value(LanguageDefinitions.get_collecting_thoughts_npc_response())
        for action in actions:
            if action.is_basic: # An advanced action has no word the NPC says
                language_category.add_config_value(LanguageDefinitions.get_action_keyword_override(action))
        result.add_base_group(language_category)

        prompts_category = ConfigValueGroup("Prompts", "Prompts", "Change the basic prompts used by Mantella.", on_value_change_callback)
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_multi_npc_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_multi_npc_director_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_game_master_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_game_master_npc_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_game_master_decision_instructions_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_game_master_decision_npc_criteria_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_game_master_decision_player_criteria_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_npc_initiative_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_npc_initiative_opener_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_npc_initiative_decision_instructions_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_npc_initiative_decision_npc_criteria_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_npc_initiative_decision_nobody_criteria_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_npc_event_generator_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_npc_event_wind_down_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_conversation_event_generator_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_shout_decision_nearby_criteria_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_shout_decision_nobody_criteria_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_shout_decision_addressed_criteria_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_shout_decision_not_addressed_criteria_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_shout_decision_event_part_criteria_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_shout_decision_event_not_part_criteria_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_shout_group_answer_note_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_skyrim_radiant_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_fallout4_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_fallout4_multi_npc_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_fallout4_radiant_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_memory_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_resummarize_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_inner_monologue_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_personal_reflection_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_vision_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_advanced_actions_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_stt_names_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_radiant_start_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_radiant_end_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_decensor_handshake_prompt_config_value())
        prompts_category.add_config_value(PromptDefinitions.get_decensor_assistant_prompt_config_value())
        result.add_base_group(prompts_category)

        prompt_profiles_category = ConfigValueGroup(
            "Prompt Profiles",
            "Prompt Profiles",
            "Create named prompt profiles and activate them per prompt type. If nothing is activated for a type, Mantella uses the Prompts tab. The Prompts tab stays independently editable; an active profile is only used at runtime and is not changed by editing the Prompts tab.",
            on_value_change_callback
        )
        result.add_base_group(prompt_profiles_category)

        other_category = ConfigValueGroup("Other", "Other", "Other settings.", on_value_change_callback)
        other_category.add_config_value(OtherDefinitions.get_automatic_greeting_config_value())
        other_category.add_config_value(OtherDefinitions.get_reload_character_data_config_value())
        other_category.add_config_value(OtherDefinitions.get_save_summary_now_config_value())
        other_category.add_config_value(OtherDefinitions.get_save_summary_now_also_save_inner_thoughts_config_value())
        other_category.add_config_value(OtherDefinitions.get_save_summary_now_also_save_personal_reflection_config_value())
        other_category.add_config_value(OtherDefinitions.get_save_inner_thoughts_now_config_value())
        other_category.add_config_value(OtherDefinitions.get_save_personal_reflection_now_config_value())
        other_category.add_config_value(OtherDefinitions.get_real_world_timestamp_config_value())
        other_category.add_config_value(OtherDefinitions.get_max_count_events_config_value())
        other_category.add_config_value(OtherDefinitions.get_events_refresh_time_config_value())
        other_category.add_config_value(OtherDefinitions.get_ingame_events_filter_config_value())
        other_category.add_config_value(OtherDefinitions.get_hourly_time_config_value())
        other_category.add_config_value(OtherDefinitions.get_player_character_description())
        other_category.add_config_value(OtherDefinitions.get_voice_player_input())
        other_category.add_config_value(OtherDefinitions.get_player_voice_model())
        other_category.add_config_value(OtherDefinitions.get_save_audio_data_to_character_folder_config_value())
        other_category.add_config_value(OtherDefinitions.get_hot_swap_enabled_config_value())
        other_category.add_config_value(OtherDefinitions.get_port_config_value())
        for log_value in LogDefinitions.get_log_config_values(): # The "Log" part: Off / Short / Full for each topic
            other_category.add_config_value(log_value)
        other_category.set_folded_part_intro(LOG_PART, LOG_PART_INTRO)
        # Startup settings (they used to have their own tab). config.ini values are read by name, so old [Startup] sections still load
        other_category.add_config_value(StartupDefinitions.get_auto_launch_ui_config_value())
        other_category.add_config_value(StartupDefinitions.get_play_startup_sound_config_value())
        other_category.add_config_value(StartupDefinitions.get_remove_mei_folders_config_value())
        result.add_base_group(other_category)

        model_profiles_category = ConfigValueGroup("Model Profiles", "Model Profiles", "Model profiles allow you to create parameter profiles for each model. These profiles can be applied across the entire system when profile application is enabled, making it convenient for users who want to assign different providers, temperatures, enable/disable thinking modes, and other parameters for different models. ", on_value_change_callback)
        model_profiles_category.add_config_value(ModelProfileDefinitions.get_selected_service_config_value())
        model_profiles_category.add_config_value(ModelProfileDefinitions.get_selected_model_config_value())
        model_profiles_category.add_config_value(ModelProfileDefinitions.get_profile_parameters_config_value())
        model_profiles_category.add_config_value(LLMDefinitions.get_apply_profile_one_on_one_config_value())
        model_profiles_category.add_config_value(LLMDefinitions.get_apply_profile_multi_npc_config_value())
        model_profiles_category.add_config_value(LLMDefinitions.get_apply_profile_game_master_config_value())
        model_profiles_category.add_config_value(LLMDefinitions.get_apply_profile_summaries_config_value())
        for key, label, has_summary_option in memory_tasks:
            model_profiles_category.add_config_value(LLMDefinitions.get_apply_profile_memory_task_config_value(key, label, has_summary_option))
        model_profiles_category.add_config_value(ModelProfileDefinitions.get_example_profile_json_config_value())
        result.add_base_group(model_profiles_category)

        return result

    @staticmethod
    def __conversation_groups(on_value_change_callback: Callable[..., Any] | None, actions: list[Action]) -> list[ConfigValueGroup]:
        """The settings at the end of the Conversation page, one section each: who speaks when in Game Master Mode, and the Shout and event
        options the game only sends to Mantella. Each section can be folded away, and shows its title on the fold. Last, not folded and under its
        own heading, the actions NPCs can use"""
        def section(identifier: str, title: str, description: str) -> ConfigValueGroup:
            return ConfigValueGroup(identifier, title, description, on_value_change_callback, page=CONVERSATION_PAGE, foldable=True)

        all_conversations = section("Conversation", "All Conversations", ConversationDefinitions.ALL_CONVERSATIONS_DESCRIPTION)
        all_conversations.add_config_value(LLMDefinitions.get_game_master_addressed_threshold_config_value())
        all_conversations.add_config_value(LLMDefinitions.get_game_master_history_message_count_config_value())
        for value in ConversationDefinitions.get_talk_config_values(SHARED):
            all_conversations.add_config_value(value)

        actions_section = ConfigValueGroup("ConversationActions", "Actions", ActionDefinitions.section_description(actions), on_value_change_callback,
                                           page=CONVERSATION_PAGE)
        actions_section.add_config_value(ActionDefinitions.get_advanced_actions_enabled_config_value())
        kinds = [ActionDefinitions.get_actions_config_value(actions, kind) for kind in ACTION_KINDS]
        global_actions = ActionDefinitions.get_global_actions_config_value(actions, kinds) # first, a shortcut for every list
        for kind_actions in [global_actions, *kinds]: # each list folded, under its own name
            actions_section.add_config_value(kind_actions.in_folded_part(kind_actions.name))

        def own_talk_settings(target: ConfigValueGroup, kind: str):
            """A kind's "Use its own settings" switch, then its own talk settings, which only show while it is on"""
            target.add_config_value(ConversationDefinitions.get_use_own_settings_config_value(kind))
            for value in ConversationDefinitions.get_talk_config_values(kind):
                target.add_config_value(value)

        until_your_turn = ("After you speak, the NPCs reply one after another until it's your turn again. Max NPC Turns per Player Turn, below, "
                           "is the most NPC replies in a row before your turn comes anyway.")
        group = section("ConversationGroup", "Group Conversation", f"You speak in a conversation with several NPCs and the Game Master, not shouting. {until_your_turn}")
        own_talk_settings(group, GROUP)
        group.add_config_value(ConversationDefinitions.get_max_npc_turns_config_value(GROUP))
        group.add_config_value(ConversationDefinitions.get_most_replies_config_value(GROUP, "Group Conversation"))
        group.add_config_value(ConversationDefinitions.get_group_conversation_max_answering_config_value())

        shout = section("ConversationShout", "Shout", f"You speak while shouting: anyone nearby can answer or join in. Also an NPC Event you stepped into. {until_your_turn}")
        own_talk_settings(shout, SHOUT)
        shout.add_config_value(ConversationDefinitions.get_max_npc_turns_config_value(SHOUT))
        shout.add_config_value(ConversationDefinitions.get_most_replies_config_value(SHOUT, "Shout"))
        shout.add_config_value(ConversationDefinitions.get_shout_max_people_config_value())
        shout.add_config_value(ConversationDefinitions.get_shout_max_answering_config_value())
        shout.add_config_value(ConversationDefinitions.get_shout_newcomers_remember_config_value())

        director = section("ConversationDirector", "Director Mode", "You give a direction, shouting or not. The Game Master never gives you the turn: "
                           "the NPCs reply one after another until Max NPC Turns per Direction, then you can direct again.")
        own_talk_settings(director, DIRECTOR)
        director.add_config_value(ConversationDefinitions.get_most_replies_config_value(DIRECTOR, "Director Mode"))
        director.add_config_value(LLMDefinitions.get_multi_npc_game_master_director_max_npc_turns_config_value())

        npc_initiative = section("ConversationNpcInitiative", "NPC Initiative", "A nearby NPC walks up to you and starts a conversation. Switch it "
                                 "on in the Mantella MCM. Once started, it works like any conversation with one NPC.")
        npc_initiative.add_config_value(LLMDefinitions.get_npc_initiative_threshold_config_value())

        uses_conversations = ("Max NPC Turns per Player Turn, below, is the most NPC replies in a row to the event, also in Director Mode. "
                              "It uses the Most Replies per Person of the conversation it happens in.")
        event_sections = []
        for kind, situation, identifier, title, default, what_it_is in [
                ("conversation", CONVERSATION_EVENT, "ConversationConversationEvent", "Conversation Event", True,
                 "The reaction to a Talk Event picked on the Conversation Wheel during a conversation you're in: something happens to the people "
                 "in the conversation, or around them."),
                ("nearby", NEARBY_EVENT, "ConversationNearbyEvent", "Nearby Event", False,
                 "The reaction to a Nearby Event picked on the Conversation Wheel during a conversation you're in: something happens among the people "
                 "nearby who aren't in the conversation. Shout is switched on, so they can join in."),
                ("random", RANDOM_EVENT, "ConversationRandomEvent", "Random Event", True,
                 "The reaction to a Random Event picked on the Conversation Wheel during a conversation you're in: something happens that can involve "
                 "anyone nearby, in the conversation or not. Shout is switched on, so people nearby can join in.")]:
            event = section(identifier, title, f"{what_it_is} {uses_conversations}")
            own_talk_settings(event, situation)
            event.add_config_value(ConversationDefinitions.get_event_send_history_config_value(kind, default))
            event.add_config_value(ConversationDefinitions.get_event_include_player_config_value(kind, default))
            event.add_config_value(ConversationDefinitions.get_max_npc_turns_config_value(situation))
            event_sections.append(event)

        npc_event = section("ConversationNpcEvent", "Watched NPC Event", LLMDefinitions.NPC_EVENT_DESCRIPTION + " The NPCs speak one after another "
                            "until Max Lines Without Player, then the scene ends. Once you step in, it's a Shout conversation.")
        own_talk_settings(npc_event, NPC_EVENT)
        npc_event.add_config_value(ConversationDefinitions.get_most_replies_config_value(NPC_EVENT, "Watched NPC Event"))
        npc_event.add_config_value(LLMDefinitions.get_npc_event_max_participants_config_value())
        npc_event.add_config_value(LLMDefinitions.get_npc_event_player_notice_delay_config_value())
        npc_event.add_config_value(LLMDefinitions.get_npc_event_max_lines_without_player_config_value())
        npc_event.add_config_value(LLMDefinitions.get_npc_event_wind_down_lines_config_value())

        vanilla_dialogues = section("ConversationVanillaDialogues", "Vanilla Dialogues", "NPCs remember Skyrim's own voiced lines they hear: the "
                                    "scenes NPCs play out, and your talk in the dialogue menu. Switch it on or off in the Mantella MCM, or on the "
                                    "Conversation Wheel.")
        vanilla_dialogues.add_config_value(OtherDefinitions.get_heard_lines_days_kept_config_value())
        vanilla_dialogues.add_config_value(OtherDefinitions.get_heard_lines_most_per_npc_config_value())
        vanilla_dialogues.add_config_value(OtherDefinitions.get_heard_lines_menu_talk_config_value())
        vanilla_dialogues.add_config_value(OtherDefinitions.get_heard_lines_scenes_config_value())
        vanilla_dialogues.add_config_value(OtherDefinitions.get_heard_lines_npcs_meet_config_value())
        vanilla_dialogues.add_config_value(OtherDefinitions.get_heard_lines_menu_only_config_value())
        vanilla_dialogues.add_config_value(OtherDefinitions.get_heard_lines_ignored_npcs_config_value())
        vanilla_dialogues.add_config_value(OtherDefinitions.get_heard_lines_story_quests_config_value())
        vanilla_dialogues.add_config_value(OtherDefinitions.get_heard_lines_chatter_quests_config_value())
        return [all_conversations, group, shout, director, npc_initiative, *event_sections, npc_event, vanilla_dialogues, actions_section]
