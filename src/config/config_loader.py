import configparser
import logging
import os
import sys
from typing import Any
from src.config.definitions.llm_definitions import AutoQuestTagOutcomesEnum, DecensorPlacementEnum, NarrationHandlingEnum, NarrationIndicatorsEnum
from src.config.definitions.conversation_definitions import (AT_REPLY_LIMIT, DIRECTOR, EXTRA_ROUNDS, GROUP, MAX_NPC_TURNS_KINDS, NPC_EVENT,
                                                             REPLIES_IN_BETWEEN, SHARED, SHOUT, TALK_KINDS, WHEN_NPC_TALKS, WHEN_YOU_TALK, WHO_MUST_SPEAK,
                                                             AtReplyLimitEnum, NewcomersRememberEnum, WhoAnswersFirstEnum, WhoMustSpeakEnum,
                                                             max_npc_turns_identifier, most_replies_identifier, talk_key, use_own_key)
from src.config.definitions.game_definitions import GameEnum, started_inside_game_folder
from src.config.definitions.tts_definitions import NO_BACKUP, TTSEnum
from src.tts.model_catalog import ServiceModels
from src.tts.registry import FALLBACK_SERVICE, normalize_service_name, voice_services
from src.tts.voice_setups import voice_setups_for
from src.conversation.action import Action
from src.actions.function_manager import FunctionManager
from src.config.definitions.action_definitions import ACTION_KINDS, NOT_OFFERED, OLD_KEYWORDS, actions_key, carried_over_ticks
from src.config.stt_carry_over import OLD_STT_KEYS, carried_over_stt
from src.config.retired_models import CURRENT_FREE_MODEL, replace_retired_models
from src.config.definitions.log_definitions import RETIRED_LOG_KEYS
from src.stt_backends.presets import PRESETS as STT_PRESETS
from src.stt_backends.keys import STT_KEY_FILE, copy_old_key
from src.config.config_values import ConfigValues
from src.config.mantella_config_value_definitions_new import MantellaConfigValueDefinitionsNew
from src.config.definitions.language_definitions import LanguageDefinitions
from src.config.config_json_writer import ConfigJsonWriter
from src.config.config_file_writer import ConfigFileWriter
from src.config.types.config_value_string import ConfigValueString
from src.prompt_profile_manager import PromptProfileManager
from src.logs.log_detail import LogDetail
from src.logs.log_setup import ILogSetup
from src.logs.log_topic import LogChoices, LogTopic
from src.logs.topic_logger import get_topic_logger
from src.config.types.config_value import ConfigValue
from src.config.types.config_value_group import ConfigValueGroup
import src.utils as utils
from pathlib import Path
import json

log = get_topic_logger(LogTopic.STARTUP)

VOICE_SERVICE_KEYS = ("tts_service", "tts_backup_service")
SECRET_WORDS = ("key", "token", "password", "secret") # a setting whose identifier or name has one of these is never written to the log

class ConfigLoader:
    def __init__(self, mygame_folder_path: str, file_name='config.ini', game_override: GameEnum | None = None, prompt_profiles_path: str | None = None):
        # Started by the game, or from where the game would start it (e.g. from Mod Organizer 2): the game's folders are worked out from where Mantella.exe is
        self.is_run_integrated = "--integrated" in sys.argv or started_inside_game_folder(utils.resolve_path())
        self.save_folder = mygame_folder_path
        self.__has_any_value_changed: bool = False
        self.__log_next_system_prompt: bool = False
        self.__log_setup: ILogSetup | None = None
        self.__is_initial_load: bool = True
        self.__loaded_settings_logged: bool = False
        self.__file_name = os.path.join(mygame_folder_path, file_name)
        self.__game_override = game_override
        self.__prompt_profile_manager = PromptProfileManager(prompt_profiles_path)
        path_to_actions = os.path.join(utils.resolve_path(),"data","actions")
        self.__actions = [a for a in ConfigLoader.load_actions_from_json(path_to_actions) if a.identifier not in NOT_OFFERED]
        voice_setups_for(mygame_folder_path) # the player's voice setups go in the TTS Service list
        self.__definitions: ConfigValues = MantellaConfigValueDefinitionsNew.get_config_values(self.is_run_integrated, self.__actions, self.__on_config_value_change)
        if not os.path.exists(self.__file_name):
            log.info("Cannot find 'config.ini'. Assuming first time usage of MantellaSoftware and creating it.")
            self.__write_config_state(self.__definitions)

        config = configparser.ConfigParser()
        try:
            config.read(self.__file_name, encoding='utf-8')
        except Exception as e:
            log.error(repr(e))
            log.error(f'Unable to read / open config.ini. If you have recently edited this file, please try reverting to a previous version. This error is normally due to using special characters.')
            input("Press Enter to exit.")

        create_back_up_configini = False
        old_actions: str | None = None # The old Actions list of the Other tab, which the Actions section of the Conversation page replaces
        old_stt: dict[str, str] = {} # The old External Whisper Service settings, which Online Service and Custom Service replace
        has_action_switches = False
        has_retired_log_keys = False
        has_retired_models = False # A setting named an AI model that's no longer offered
        action_keys = {actions_key(kind) for kind in ACTION_KINDS}
        for section_name in config.sections():
            for (each_key, each_value) in config.items(section_name):
                if each_key == "active_actions":
                    old_actions = each_value
                    continue
                if each_key in OLD_STT_KEYS:
                    old_stt[each_key] = each_value
                    continue
                if each_key in RETIRED_LOG_KEYS: # Replaced by the Log part, nothing to carry over: dropped without a warning
                    has_retired_log_keys = True
                    continue
                has_action_switches = has_action_switches or each_key in action_keys
                try:
                    config_value = self.__definitions.get_config_value_definition(each_key)
                    each_value, retired = replace_retired_models(each_value)
                    if retired:
                        has_retired_models = True
                        log.warning(f"{', '.join(retired)} is no longer offered. {config_value.name} now uses {CURRENT_FREE_MODEL}.")
                    # Unescape hash symbols that were escaped for INI file storage
                    unescaped_value = ConfigFileWriter.unescape_hash_symbols(each_value)
                    # Attempt to JSON-decode string values first if they were encoded to preserve whitespace
                    if isinstance(config_value, ConfigValueString):
                        try:
                            import json
                            decoded = json.loads(unescaped_value)
                            # Only accept if decoding produced a string
                            if isinstance(decoded, str):
                                config_value.parse(decoded)
                                continue
                        except Exception:
                            pass
                    result = config_value.parse(unescaped_value)
                    if each_key in VOICE_SERVICE_KEYS and not result.is_success:
                        utils.play_error_sound()
                        log.warning(f"The voice service '{each_value}' chosen as {config_value.name} no longer exists (a voice setup that was removed or renamed?). {FALLBACK_SERVICE} is used instead. Pick another one on the Text-to-Speech page.")
                except:
                    create_back_up_configini = True
                    # TODO: filter out warnings for ['game', 'skyrim_mod_folder', 'skyrimvr_mod_folder', 'fallout4_mod_folder', 'fallout4vr_mod_folder', fallout4vr_folder]
                    utils.play_error_sound()
                    log.warning(f"Could not identify config value '{each_key} = {each_value}' in current config.ini. Value will not be loaded. A backup of this config.ini will be created.")

        if old_actions is not None:
            if not has_action_switches:
                self.__carry_over_actions(old_actions)
            create_back_up_configini = True # Written again without the old list, once

        moved_stt = self.__carry_over_stt(old_stt, config)
        if old_stt or moved_stt:
            create_back_up_configini = True # Written again without the old settings, once

        moved_models = self.__carry_over_models() # also on a start that writes a backup, often the first after an update
        moved_from_piper = self.__carry_over_missing_piper()
        if create_back_up_configini:
            self.__write_config_state(self.__definitions, True)
        elif moved_models or has_retired_log_keys or has_retired_models or moved_from_piper:
            self.__write_config_state(self.__definitions)

        self.__is_initial_load = False
        self.__update_config_values_from_current_state()
        self.__has_any_value_changed = False
    
    def __carry_over_missing_piper(self) -> bool:
        """Piper picked but not on this PC. Piper and its voices come with the original Mantella mod, not with this one: a player who
        removed the original Mantella gets Pocket TTS as the TTS Service and no backup in place of Piper, instead of NPCs that stay silent.
        A player who kept it (its piper folder is still there) keeps Piper. True when something changed"""
        if self.__piper_is_installed():
            return False
        changed = False
        for identifier, replacement in (("tts_service", FALLBACK_SERVICE), ("tts_backup_service", NO_BACKUP)):
            setting = self.__definitions.get_config_value_definition(identifier)
            service = voice_services.find(str(setting.value))
            if service is None or service.legacy_enum != TTSEnum.PIPER:
                continue
            setting.value = replacement
            changed = True
            log.warning(f"Piper isn't on this PC (it comes with the original Mantella mod), so your {setting.name} is now {replacement}. "
                        "To use Piper again, turn the original Mantella mod back on, then pick Piper on the Text-to-Speech page.")
        return changed

    def __piper_is_installed(self) -> bool:
        """Piper's program and at least one of its voices are where Mantella looks for them"""
        if self.is_run_integrated:
            folder = Path(utils.resolve_path()) / "piper" # where the original Mantella mod puts it
        else:
            text = self.__definitions.get_string_value("piper_folder", False).strip()
            if not text:
                return False
            folder = Path(text)
        try:
            return (folder / "piper.exe").is_file() and any((folder / "models").rglob("*.onnx"))
        except OSError:
            return False

    def __carry_over_models(self) -> bool:
        """The online services' Model settings (src/tts/model_catalog.py): a model saved by its name (Turbo) is kept by its ID from now on,
        and the Global settings values on the page belong to the picked model (src/tts/voice_style.py). True when something changed"""
        from src.tts.voice_folders import voice_clips_folder
        from src.tts.voice_style import ServiceStyle
        from src.voice_clips.clip_store import shared_clip_store
        changed = False
        clips = None
        for models in ServiceModels.all():
            setting = self.__definitions.get_config_value_definition(models.setting)
            models.refresh_options(setting, self) # the player's added and removed models
            model = models.resolve(setting.value)
            if model.known and setting.value != model.id:
                setting.value = model.id
                changed = True
            style = ServiceStyle(self, models.service)
            try:
                if clips is None:
                    clips = shared_clip_store(voice_clips_folder(self.save_folder))
            except Exception:
                clips = None
            changed = style.sync(clips) or changed
        return changed

    def __carry_over_actions(self, old_actions: str):
        """Someone updating: the Actions section's switches start from their old Actions list (see action_definitions.carried_over_ticks),
        and Offended and Forgiven, now Attack and StandDown, lose their old keywords if they kept the old default"""
        old_ticked = [name.strip() for name in old_actions.split(",") if name.strip()]
        for kind in ACTION_KINDS:
            self.__definitions.get_config_value_definition(actions_key(kind)).value = carried_over_ticks(self.__actions, kind, old_ticked)
        for key, (old_keyword, new_keyword) in OLD_KEYWORDS.items():
            try:
                keyword = self.__definitions.get_config_value_definition(key)
            except Exception:
                continue
            if keyword.value == old_keyword:
                keyword.value = new_keyword
        log.info(f"Your Actions list ({', '.join(old_ticked) or 'nothing ticked'}) was moved to the new Actions section of the Conversation page.")

    def __carry_over_stt(self, old_stt: dict[str, str], config: configparser.ConfigParser) -> bool:
        """Someone updating: their External Whisper Service setup moves to Online Service or Custom Service (see carried_over_stt),
        and OpenAI models that stop working on 26 February 2027 move to gpt-transcribe. True when anything changed"""
        stored = {key: value for section in config.sections() for key, value in config.items(section)}
        changes, message, key_file = carried_over_stt(old_stt, stored)
        if key_file and copy_old_key(key_file):
            message += f" Your key in {STT_KEY_FILE} was copied to {key_file}."
        for identifier, value in changes.items():
            try:
                self.__definitions.get_config_value_definition(identifier).value = value
            except Exception as e:
                log.warning(f"Could not move the old speech-to-text setting to {identifier}: {e}")
        if message:
            log.info(message)
        return bool(changes)

    @property
    def have_all_config_values_loaded_correctly(self) -> bool:
        return self.__definitions.have_all_loaded_values_succeded
    
    @property
    def has_any_config_value_changed(self) -> bool:
        return self.__has_any_value_changed
    
    @property
    def definitions(self) -> ConfigValues:
        return self.__definitions

    @property
    def prompt_profile_manager(self) -> PromptProfileManager:
        return self.__prompt_profile_manager

    def notify_prompt_profiles_changed(self) -> None:
        """Reload prompt overlays and mark config changed so the next request hot-swaps."""
        self.__prompt_profile_manager.reload()
        self.__apply_prompt_values()
        self.__has_any_value_changed = True
        self.__log_next_system_prompt = True

    def notify_voice_services_changed(self) -> None:
        """A voice setup was saved or removed: the next request starts the voice services again with it"""
        self.__has_any_value_changed = True

    def notify_keys_changed(self) -> None:
        """A key was saved on the settings page: the next request reloads the AI services, which read the key again"""
        self.__has_any_value_changed = True

    def consume_log_next_system_prompt(self) -> bool:
        """Return whether the next generated system prompt should be logged, then clear the flag."""
        should_log = self.__log_next_system_prompt
        self.__log_next_system_prompt = False
        return should_log
    
    def use_log_setup(self, log_setup: ILogSetup) -> None:
        """The log started after the settings were loaded: from now on, changes to what it shows apply right away"""
        self.__log_setup = log_setup

    def log_loaded_settings(self) -> None:
        """Every setting value read at startup, one line each, for the Full detail of the Startup topic.
        The log only starts after the ConfigLoader is built, so Setup calls this once, right after that. Settings that could hold a secret are skipped"""
        if self.__loaded_settings_logged or not log.isEnabledFor(logging.DEBUG):
            return
        self.__loaded_settings_logged = True
        def write(config_value: ConfigValue) -> None:
            if isinstance(config_value, ConfigValueGroup):
                for child in config_value.value:
                    write(child)
                return
            text = f"{config_value.identifier} {config_value.name}".lower()
            if not any(word in text for word in SECRET_WORDS):
                log.debug(f"{config_value.identifier} = {config_value.value}")
        for group in self.__definitions.base_groups:
            write(group)

    def update_config_loader_with_changed_config_values(self):
        self.__update_config_values_from_current_state()
        self.__has_any_value_changed = False
        if self.__log_setup:
            self.__log_setup.apply(self.log_choices)

    def get_ui_tab_order(self) -> list[str]:
        try:
            raw = self.__definitions.get_string_value("ui_tab_order") or ""
        except Exception:
            return []
        return [part.strip() for part in raw.split(",") if part.strip()]

    def __list_value(self, identifier: str) -> list[str]:
        """A list setting: the parts between commas or line breaks, trimmed and in lower case"""
        return [p.strip().lower() for p in self.__definitions.get_string_value(identifier).replace("\r", "\n").replace("\n", ",").split(",") if p.strip()]

    def save_ui_tab_order(self, names: list[str]) -> None:
        already_changed = self.__has_any_value_changed
        order = ",".join(name.strip() for name in names if isinstance(name, str) and name.strip())
        cv = self.__definitions.get_config_value_definition("ui_tab_order")
        if cv.value == order:
            return
        cv.value = order
        if not already_changed:
            self.__has_any_value_changed = False

    def sync_summary_llm_settings_from_definitions(self) -> None:
        """Refresh summary, private-thoughts and personal-reflection LLM attributes from UI definitions
        without clearing the config-changed flag.

        Used by Save Summary Now so the latest summary model is applied without preventing a later
        full hot-swap of other pending settings.
        """
        self.apply_profile_summaries = self.__definitions.get_bool_value("apply_profile_summaries")
        self.summary_llm_api = self.__definitions.get_string_value("summary_llm_api")
        self.summary_llm = self.__definitions.get_string_value("summary_model")
        self.summary_llm = self.summary_llm.split(' |')[0] if ' |' in self.summary_llm else self.summary_llm
        self.summary_custom_token_count = self.__definitions.get_int_value("summary_custom_token_count")
        try:
            self.summary_llm_params: dict[str, Any] | None = json.loads(
                self.__definitions.get_string_value("summary_llm_params").replace('\n', '')
            )
        except Exception as e:
            log.error(f"""Error in parsing Summary LLM parameter list: {e}
Summary LLM parameter list must follow the Python dictionary format: https://www.w3schools.com/python/python_dictionaries.asp""")
            self.summary_llm_params = None
        self.__apply_memory_task_llm_settings_from_definitions()

    def __apply_memory_task_llm_settings_from_definitions(self) -> None:
        """Load the private-thoughts and personal-reflection LLM settings (used when not following the summary model)."""
        (self.thoughts_use_summary_model, self.apply_profile_thoughts, self.thoughts_llm_api,
         self.thoughts_llm, self.thoughts_custom_token_count, self.thoughts_llm_params) = \
            self.__read_memory_task_llm_settings("thoughts", "Private Thoughts")
        (self.reflection_use_summary_model, self.apply_profile_reflection, self.reflection_llm_api,
         self.reflection_llm, self.reflection_custom_token_count, self.reflection_llm_params) = \
            self.__read_memory_task_llm_settings("reflection", "Personal Reflection")

    def __read_memory_task_llm_settings(self, key: str, label: str, has_summary_option: bool = True) -> tuple[bool, bool, str, str, int, dict[str, Any] | None]:
        use_summary_model = has_summary_option and self.__definitions.get_bool_value(f"{key}_use_summary_model")
        apply_profile = self.__definitions.get_bool_value(f"apply_profile_{key}")
        llm_api = self.__definitions.get_string_value(f"{key}_llm_api")
        llm = self.__definitions.get_string_value(f"{key}_model") or ""  # empty means "use the summary model"
        llm = llm.split(' |')[0] if ' |' in llm else llm
        custom_token_count = self.__definitions.get_int_value(f"{key}_custom_token_count")
        try:
            llm_params: dict[str, Any] | None = json.loads(self.__definitions.get_string_value(f"{key}_llm_params").replace('\n', ''))
        except Exception as e:
            log.error(f"""Error in parsing {label} LLM parameter list: {e}
{label} LLM parameter list must follow the Python dictionary format: https://www.w3schools.com/python/python_dictionaries.asp""")
            llm_params = None
        return use_summary_model, apply_profile, llm_api, llm, custom_token_count, llm_params

    def sync_bio_section_filter_settings_from_definitions(self) -> None:
        """Refresh bio-section-filter attributes from UI definitions without clearing the config-changed flag.

        Used by Bio Editor Generate Reflection and manual Save Summary/Thoughts/Reflection
        so the latest filter toggles apply without preventing a later full hot-swap.
        """
        self.__apply_bio_section_filter_settings_from_definitions()

    def sync_prompt_values_from_definitions(self) -> None:
        """Refresh prompt attributes from the Prompts tab and active profiles without clearing the config-changed flag.

        Used by Bio Editor Generate Reflection and manual Save Summary/Thoughts/Reflection
        so Prompts tab edits apply before the next game request.
        """
        self.__apply_prompt_values()

    def sync_save_summary_now_toggles_from_definitions(self) -> None:
        """Refresh Save Summary Now extra toggles from UI definitions without clearing the config-changed flag.

        The checkboxes update definitions immediately. The live attributes stay stale until the next
        game request, so Save Summary Now must copy them here or it keeps the previous values.
        """
        self.save_summary_now_also_save_inner_thoughts = self.__definitions.get_bool_value("save_summary_now_also_save_inner_thoughts")
        self.save_summary_now_also_save_personal_reflection = self.__definitions.get_bool_value("save_summary_now_also_save_personal_reflection")

    def __apply_bio_section_filter_settings_from_definitions(self) -> None:
        self.enable_bio_section_filter = self.__definitions.get_bool_value("enable_bio_section_filter")
        self.enable_bio_section_filter_for_memory = self.__definitions.get_bool_value("enable_bio_section_filter_for_memory")
        self.bio_sections_to_exclude = self.__definitions.get_string_value("bio_sections_to_exclude")
        self.bio_sections_to_exclude_list = []
        for section in self.bio_sections_to_exclude.split(","):
            if not section:
                continue
            normalized_section = section.strip().strip("\"'").strip().lower()
            if normalized_section:
                self.bio_sections_to_exclude_list.append(normalized_section)
    
    def __on_config_value_change(self):
        self.__has_any_value_changed = True
        if not self.__is_initial_load:
            self.__write_config_state(self.__definitions)
        if self.__log_setup:
            self.__log_setup.apply(self.__chosen_log_choices()) # Right away, not with the game's next request

    def __chosen_log_choices(self) -> LogChoices:
        return {topic: LogDetail.from_setting(self.__definitions.get_string_value(topic.setting_id), topic.default, topic.details) for topic in LogTopic}
    
    def __write_config_state(self, definitions: ConfigValues, create_back_up_configini: bool = False):
        try:
            writer: ConfigFileWriter = ConfigFileWriter()
            writer.write(self.__file_name, definitions, create_back_up_configini)
        except Exception as e:
            utils.play_error_sound()
            log.error(f"Failed to write default 'config.ini'. Possible reason: MantellaSoftware does not have rights to write at its location. Exception: {repr(e)}")

    def chosen_voice_service_name(self, identifier: str = "tts_service") -> str:
        """The voice service picked on the settings page right now. tts_service_name only changes with the next request from the game,
        so a button on the page that speaks with the TTS Service reads this instead"""
        return self.__voice_service_name(identifier)

    def chosen_voice_volume(self, identifier: str) -> int:
        """A voice service's Volume (pocket_tts_volume, ...) as set on the settings page right now: a change is heard from the next line"""
        try:
            return self.__definitions.get_int_value(identifier)
        except Exception:
            return 100

    def __voice_service_name(self, identifier: str) -> str:
        """The chosen voice service, by its name in the list of voice services. Pocket TTS when it is no longer in the list.
        NO_BACKUP for a Backup Voice Service of None, which voice_services.find turns into no service"""
        name = self.__definitions.get_string_value(identifier, False)
        if identifier == "tts_backup_service" and not normalize_service_name(name):
            return NO_BACKUP
        service = voice_services.find(name)
        if service is None:
            log.warning(f"The voice service '{name}' is not in the list of voice services. {FALLBACK_SERVICE} is used instead.")
            service = voice_services.find(FALLBACK_SERVICE)
        return service.name

    def __update_config_values_from_current_state(self):
        self.__definitions.clear_constraint_violations()
        try:
            # if the exe is being run by the game, or sits where the game would run it, replace config.ini paths with relative paths
            if self.is_run_integrated:
                # Plugins/MantellaSoftware folder
                exe_folder = Path(utils.resolve_path())
                # Working backwards from MantellaSoftware (where the .exe runs) -> Plugins -> F4SE/SKSE -> Data (mod folder) -> Game
                self.game_path = str(exe_folder.parent.parent.parent.parent)
                # Working backwards from MantellaSoftware (where the .exe runs) -> Plugins -> F4SE/SKSE -> Data (mod folder)
                self.mod_path = str(exe_folder.parent.parent.parent)

                self.lipgen_path = self.game_path+"\\Tools\\LipGen\\"
                self.facefx_path = self.mod_path+"\\Sound\\Voice\\Processing\\"
                self.piper_path = str(Path(utils.resolve_path())) + "\\piper"
                self.moonshine_folder = str(Path(utils.resolve_path()))

                game_parent_folder_name = os.path.basename(self.game_path).lower()
                if 'vr' in game_parent_folder_name:
                    if 'fallout' in game_parent_folder_name:
                        self.game = GameEnum.FALLOUT4_VR
                        # Fallout 4 VR uses the same creation kit as Fallout 4, so the lip gen folder needs to be set to the Fallout 4 folder
                        # Note that this path assumes that both Fallout 4 versions are installed in the same directory
                        # Working backwards from MantellaSoftware (where the .exe runs) -> Plugins -> F4SE -> Data -> Fallout 4 VR -> common (Steam folder for all games)
                        if not os.path.exists(self.lipgen_path):
                            self.lipgen_path = str(exe_folder.parent.parent.parent.parent.parent)+"\\Fallout 4"+"\\Tools\\LipGen\\"
                            if not os.path.exists(self.lipgen_path):
                                self.lipgen_path = ''
                    elif 'skyrim' in game_parent_folder_name:
                        self.game = GameEnum.SKYRIM_VR
                        # Skyrim VR uses the same creation kit as Skyrim Special Edition, so the lip gen folder needs to be set to the Skyrim Special Edition folder
                        # Note that this path assumes that both Skyrim versions are installed in the same directory
                        # Working backwards from MantellaSoftware (where the .exe runs) -> Plugins -> SKSE -> Data -> Skyrim VR -> common (Steam folder for all games)
                        if not os.path.exists(self.lipgen_path):
                            self.lipgen_path = str(exe_folder.parent.parent.parent.parent.parent)+"\\Skyrim Special Edition"+"\\Tools\\LipGen\\"
                            if not os.path.exists(self.lipgen_path):
                                self.lipgen_path = ''
                else:
                    if 'fallout' in game_parent_folder_name:
                        self.game = GameEnum.FALLOUT4
                    elif 'skyrim' in game_parent_folder_name:
                        self.game = GameEnum.SKYRIM
                    else: # default to Skyrim
                        self.game = GameEnum.SKYRIM

            else:
                # Allow chosen game to be overriden for testing purposes
                if self.__game_override:
                    self.game = self.__game_override
                else:
                    self.game: GameEnum = self.__definitions.get_enum_value("game", GameEnum)
                
                if self.game == GameEnum.FALLOUT4:
                    self.game_path: str = self.__definitions.get_string_value("fallout4_folder")
                    self.mod_path: str = self.__definitions.get_string_value("fallout4_mod_folder")
                elif self.game == GameEnum.FALLOUT4_VR:
                    self.game_path: str = self.__definitions.get_string_value("fallout4vr_folder")
                    self.mod_path: str = self.__definitions.get_string_value("fallout4vr_mod_folder")
                elif self.game == GameEnum.SKYRIM_VR:
                    self.game_path = None
                    self.mod_path: str = self.__definitions.get_string_value("skyrimvr_mod_folder")
                #if the game is not recognized Mantella will assume it's Skyrim since that's the most frequent one.
                else:
                    self.game = GameEnum.SKYRIM
                    self.game_path = None
                    self.mod_path: str = self.__definitions.get_string_value("skyrim_mod_folder")

                self.lipgen_path = self.__definitions.get_string_value("lipgen_folder")
                self.facefx_path = self.__definitions.get_string_value("facefx_folder")

            self.mod_path_base = self.mod_path
            self.mod_path += "\\Sound\\Voice\\Mantella.esp"

            self.all_actions: list[Action] = self.__actions # Which ones NPCs can use depends on the kind of conversation: see action_rules
            for kind in ACTION_KINDS:
                setattr(self, actions_key(kind), self.__definitions.get_string_list_value(actions_key(kind)))

            self.language = self.__definitions.get_string_value("language")
            self.end_conversation_keyword = self.__definitions.get_string_value("end_conversation_keyword")
            self.goodbye_npc_response = self.__definitions.get_string_value("goodbye_npc_response")
            self.collecting_thoughts_npc_response = self.__definitions.get_string_value("collecting_thoughts_npc_response")
            for a in self.__actions:
                if a.is_basic:
                    a.keyword = self.__definitions.get_string_value(LanguageDefinitions.action_keyword_key(a))

            #TTS
            self.tts_service_name: str = self.__voice_service_name("tts_service")
            self.tts_service: TTSEnum | None = voice_services.find(self.tts_service_name).legacy_enum # None for the newer services and voice setups
            self.tts_backup_service_name: str = self.__voice_service_name("tts_backup_service")
            # Each online service's Model (src/tts/model_catalog.py): a model ID
            for models in ServiceModels.all():
                setattr(self, models.setting, self.__definitions.get_string_value(models.setting))
            self.inworld_audio_tags = self.__definitions.get_bool_value("inworld_audio_tags")
            self.inworld_directions = self.__definitions.get_bool_value("inworld_directions")
            self.heygen_audio_tags = self.__definitions.get_bool_value("heygen_audio_tags")
            self.qwen_workspace_id = self.__definitions.get_string_value("qwen_workspace_id").strip()
            self.qwen_audio_tags = self.__definitions.get_bool_value("qwen_audio_tags")
            self.qwen_directions = self.__definitions.get_bool_value("qwen_directions")
            self.boson_audio_tags = self.__definitions.get_bool_value("boson_audio_tags")
            self.iodine_audio_tags = self.__definitions.get_bool_value("iodine_audio_tags")
            self.iodine_directions = self.__definitions.get_bool_value("iodine_directions")
            # How cloned voices sound (src/tts/voice_style.py): every NPC's, unless a voice has its own on the Voice Clips page
            self.heygen_similarity = self.__definitions.get_string_value("heygen_similarity")
            self.heygen_expressiveness = self.__definitions.get_float_value("heygen_expressiveness")
            self.inworld_delivery = self.__definitions.get_string_value("inworld_delivery")
            self.boson_expressiveness = self.__definitions.get_string_value("boson_expressiveness")
            self.iodine_temperature = self.__definitions.get_float_value("iodine_temperature")
            self.xtts_url = self.__definitions.get_string_value("xtts_url").rstrip('/')

            # Do not check if a given path exists unless the TTS service is actually selected
            validate_xtts_path = validate_xvasynth_path = validate_piper_path = False

            if self.tts_service == TTSEnum.XTTS:
                if 'http://127.0.0.1:8020' in self.xtts_url: # Only validate the XTTS folder if running locally
                   validate_xtts_path = True
            elif self.tts_service == TTSEnum.XVASYNTH:
                validate_xvasynth_path = True
            elif self.tts_service == TTSEnum.PIPER:
                validate_piper_path = True

            self.xtts_server_path = self.__definitions.get_string_value("xtts_server_folder", validate_xtts_path)
            self.xvasynth_path = self.__definitions.get_string_value("xvasynth_folder", validate_xvasynth_path)
            if not hasattr(self, 'piper_path'): # assign Piper path from config if not running integrated
                self.piper_path = self.__definitions.get_string_value("piper_folder", validate_piper_path)

            self.lip_generation = self.__definitions.get_string_value("lip_generation").strip().lower()
            self.fast_response_mode = self.__definitions.get_bool_value("fast_response_mode")
            self.fast_response_mode_volume = self.__definitions.get_int_value("fast_response_mode_volume")

            #Added from xTTS implementation
            self.xtts_default_model = self.__definitions.get_string_value("xtts_default_model")
            self.xtts_deepspeed = self.__definitions.get_bool_value("xtts_deepspeed")
            self.xtts_lowvram = self.__definitions.get_bool_value("xtts_lowvram")
            self.xtts_device = self.__definitions.get_string_value("xtts_device")
            self.number_words_tts = self.__definitions.get_int_value("number_words_tts")
            self.xtts_data = self.__definitions.get_string_value("xtts_data")
            self.xtts_accent = self.__definitions.get_bool_value("xtts_accent")

            self.tts_print = self.__definitions.get_bool_value("tts_print")
        
            self.xvasynth_process_device = self.__definitions.get_string_value("tts_process_device")
            self.pace = self.__definitions.get_float_value("pace")
            self.use_cleanup = self.__definitions.get_bool_value("use_cleanup")
            self.use_sr = self.__definitions.get_bool_value("use_sr")

            #STT
            self.stt_service = self.__definitions.get_string_value("stt_service")
            self.stt_online_service = self.__definitions.get_string_value("stt_online_service")
            self.stt_models = {preset.model_setting: self.__definitions.get_string_value(preset.model_setting) for preset in STT_PRESETS}
            self.moonshine_model = self.__definitions.get_string_value("moonshine_model_size")
            if not hasattr(self, 'moonshine_folder'):
                try:
                    self.moonshine_folder = str(Path(self.__definitions.get_string_value("moonshine_folder")).parent) # go up one folder since moonshine/ is in the model name
                except:
                    self.moonshine_folder = ''
            self.whisper_model = self.__definitions.get_string_value("whisper_model_size")
            self.whisper_process_device = self.__definitions.get_string_value("process_device")
            self.stt_language = self.__definitions.get_string_value("stt_language")
            if (self.stt_language == 'default'):
                self.stt_language = self.language
            self.stt_translate = self.__definitions.get_bool_value("stt_translate")
            self.stt_first_names = self.__definitions.get_bool_value("stt_first_names")
            self.audio_threshold = self.__definitions.get_float_value("audio_threshold")
            self.proactive_mic_mode = self.__definitions.get_bool_value("proactive_mic_mode")
            self.min_refresh_secs = self.__definitions.get_float_value("min_refresh_secs")
            self.play_cough_sound = self.__definitions.get_bool_value("play_cough_sound")
            self.allow_interruption = self.__definitions.get_bool_value("allow_interruption")
            self.ptt_enabled = self.__definitions.get_bool_value("ptt_enabled")
            self.ptt_hotkey = self.__definitions.get_string_value("ptt_hotkey")
            self.save_mic_input = self.__definitions.get_bool_value("save_mic_input")
            self.pause_threshold = self.__definitions.get_float_value("pause_threshold")
            self.listen_timeout = self.__definitions.get_int_value("listen_timeout")
            self.stt_custom_format = self.__definitions.get_string_value("stt_custom_format")
            self.stt_custom_address = self.__definitions.get_string_value("stt_custom_address")
            self.stt_custom_model = self.__definitions.get_string_value("stt_custom_model")
            self.stt_custom_timeout = self.__definitions.get_float_value("stt_custom_timeout")

            #LLM
            self.max_response_sentences_single = self.__definitions.get_int_value("max_response_sentences_single")
            self.max_response_sentences_multi = self.__definitions.get_int_value("max_response_sentences_multi")
            self.llm_response_timeout = self.__definitions.get_int_value("llm_response_timeout")
            self.read_thoughts_after_sentence_cap = self.__definitions.get_bool_value("read_thoughts_after_sentence_cap")
            self.turn_thoughts_to_keep = self.__definitions.get_int_value("turn_thoughts_to_keep")
            self.drop_last_sentences_single = self.__definitions.get_int_value("drop_last_sentences_single")
            self.replace_braille_blank = self.__definitions.get_bool_value("replace_braille_blank")
            self.llm = self.__definitions.get_string_value("model")
            self.llm = self.llm.split(' |')[0] if ' |' in self.llm else self.llm
            self.wait_time_buffer = self.__definitions.get_float_value("wait_time_buffer")
            self.llm_api = self.__definitions.get_string_value("llm_api")
            # self.llm_priority = self.__definitions.get_string_value("llm_priority")
            # if self.llm_api == "Custom":
            #     self.llm_api = self.__definitions.get_string_value("llm_custom_service_url")
            self.custom_token_count = self.__definitions.get_int_value("custom_token_count")
            try:
                self.llm_params: dict[str, Any] | None = json.loads(self.__definitions.get_string_value("llm_params").replace('\n', ''))
            except Exception as e:
                log.error(f"""Error in parsing LLM parameter list: {e}
LLM parameter list must follow the Python dictionary format: https://www.w3schools.com/python/python_dictionaries.asp""")
                self.llm_params = None

            # Sonnet Prompt Caching (OpenRouter only)
            try:
                self.sonnet_prompt_caching_enabled = self.__definitions.get_bool_value("sonnet_prompt_caching_enabled")
            except Exception:
                self.sonnet_prompt_caching_enabled = False

            self.allow_per_character_llm_overrides = self.__definitions.get_bool_value("allow_per_character_llm_overrides")
            self.enable_character_tag_reading = self.__definitions.get_bool_value("enable_character_tag_reading")
            self.auto_quest_tag_outcomes: AutoQuestTagOutcomesEnum = self.__definitions.get_enum_value("auto_quest_tag_outcomes", AutoQuestTagOutcomesEnum)

            # Profile application settings
            self.apply_profile_one_on_one = self.__definitions.get_bool_value("apply_profile_one_on_one")
            self.apply_profile_multi_npc = self.__definitions.get_bool_value("apply_profile_multi_npc")
            self.apply_profile_summaries = self.__definitions.get_bool_value("apply_profile_summaries")

            # Multi-NPC LLM Configuration
            self.multi_npc_llm_api = self.__definitions.get_string_value("multi_npc_llm_api")
            self.multi_npc_llm = self.__definitions.get_string_value("multi_npc_model")
            self.multi_npc_llm = self.multi_npc_llm.split(' |')[0] if ' |' in self.multi_npc_llm else self.multi_npc_llm
            self.multi_npc_custom_token_count = self.__definitions.get_int_value("multi_npc_custom_token_count")
            try:
                self.multi_npc_llm_params: dict[str, Any] | None = json.loads(self.__definitions.get_string_value("multi_npc_llm_params").replace('\n', ''))
            except Exception as e:
                log.error(f"""Error in parsing Multi-NPC LLM parameter list: {e}
Multi-NPC LLM parameter list must follow the Python dictionary format: https://www.w3schools.com/python/python_dictionaries.asp""")
                self.multi_npc_llm_params = None

            # Game Master LLM Configuration (multi-NPC Game Master Mode)
            self.apply_profile_game_master = self.__definitions.get_bool_value("apply_profile_game_master")
            self.game_master_llm_api = self.__definitions.get_string_value("game_master_llm_api")
            self.game_master_llm = self.__definitions.get_string_value("game_master_model")
            self.game_master_llm = self.game_master_llm.split(' |')[0] if ' |' in self.game_master_llm else self.game_master_llm
            self.game_master_custom_token_count = self.__definitions.get_int_value("game_master_custom_token_count")
            try:
                self.game_master_llm_params: dict[str, Any] | None = json.loads(self.__definitions.get_string_value("game_master_llm_params").replace('\n', ''))
            except Exception as e:
                log.error(f"""Error in parsing Game Master LLM parameter list: {e}
Game Master LLM parameter list must follow the Python dictionary format: https://www.w3schools.com/python/python_dictionaries.asp""")
                self.game_master_llm_params = None
            self.game_master_use_decision_model = self.__definitions.get_bool_value("game_master_use_decision_model")
            self.game_master_decision_service = self.__definitions.get_string_value("game_master_decision_service")
            self.game_master_decision_model = self.__definitions.get_string_value("game_master_decision_model")
            self.game_master_decision_model = self.game_master_decision_model.split(' |')[0] if ' |' in self.game_master_decision_model else self.game_master_decision_model
            self.npc_initiative_threshold = self.__definitions.get_float_value("npc_initiative_threshold")
            self.game_master_addressed_threshold = self.__definitions.get_float_value("game_master_addressed_threshold")

            # NPC Events. The generator always uses its own model: the Summary model is not meant for writing scenes
            (_, self.apply_profile_npc_event, self.npc_event_llm_api,
             self.npc_event_llm, self.npc_event_custom_token_count, self.npc_event_llm_params) = \
                self.__read_memory_task_llm_settings("npc_event", "NPC Event Generator", has_summary_option=False)
            self.npc_event_max_participants = self.__definitions.get_int_value("npc_event_max_participants")
            self.npc_event_player_notice_delay = self.__definitions.get_int_value("npc_event_player_notice_delay")
            self.npc_event_max_lines_without_player = self.__definitions.get_int_value("npc_event_max_lines_without_player")
            self.npc_event_wind_down_lines = self.__definitions.get_int_value("npc_event_wind_down_lines")

            # Fallback model for NPC replies the LLM service rejects
            self.fallback_llm_enabled = self.__definitions.get_bool_value("fallback_llm_enabled")
            (_, self.apply_profile_fallback, self.fallback_llm_api,
             self.fallback_llm, self.fallback_custom_token_count, self.fallback_llm_params) = \
                self.__read_memory_task_llm_settings("fallback", "Fallback", has_summary_option=False)

            # Advanced Actions: the AI model picks actions with their details (tool calling). By default each NPC's own model picks them
            self.advanced_actions_enabled = self.__definitions.get_bool_value("advanced_actions_enabled")
            self.advanced_actions_own_model = self.__definitions.get_bool_value("advanced_actions_own_model")
            (_, self.apply_profile_advanced_actions, self.advanced_actions_llm_api,
             self.advanced_actions_llm, self.advanced_actions_custom_token_count, self.advanced_actions_llm_params) = \
                self.__read_memory_task_llm_settings("advanced_actions", "Advanced Actions", has_summary_option=False)

            # Multi-NPC prompt content toggles
            self.multi_npc_bios_only = self.__definitions.get_bool_value("multi_npc_bios_only")
            self.enable_decensor_bypass = self.__definitions.get_bool_value("enable_decensor_bypass")
            self.decensor_assistant_placement: DecensorPlacementEnum = self.__definitions.get_enum_value("decensor_assistant_placement", DecensorPlacementEnum)
            self.__apply_bio_section_filter_settings_from_definitions()

            # Summary LLM Configuration
            self.summary_llm_api = self.__definitions.get_string_value("summary_llm_api")
            self.summary_llm = self.__definitions.get_string_value("summary_model")
            self.summary_llm = self.summary_llm.split(' |')[0] if ' |' in self.summary_llm else self.summary_llm
            self.summary_custom_token_count = self.__definitions.get_int_value("summary_custom_token_count")
            try:
                self.summary_llm_params: dict[str, Any] | None = json.loads(self.__definitions.get_string_value("summary_llm_params").replace('\n', ''))
            except Exception as e:
                log.error(f"""Error in parsing Summary LLM parameter list: {e}
Summary LLM parameter list must follow the Python dictionary format: https://www.w3schools.com/python/python_dictionaries.asp""")
                self.summary_llm_params = None
            self.__apply_memory_task_llm_settings_from_definitions()

            # self.stop_llm_generation_on_assist_keyword: bool = self.__definitions.get_bool_value("stop_llm_generation_on_assist_keyword")

            self.narration_handling: NarrationHandlingEnum = self.__definitions.get_enum_value("narration_handling", NarrationHandlingEnum)
            self.narrator_voice = self.__definitions.get_string_value("narrator_voice")
            self.narration_start_indicators = self.__definitions.get_string_list_value("narration_start_indicators")
            self.narration_end_indicators = self.__definitions.get_string_list_value("narration_end_indicators")
            self.speech_start_indicators = self.__definitions.get_string_list_value("speech_start_indicators")
            self.speech_end_indicators = self.__definitions.get_string_list_value("speech_end_indicators")
            self.narration_indicators: NarrationIndicatorsEnum = self.__definitions.get_enum_value("narration_indicators", NarrationIndicatorsEnum)
            
            self.remove_mei_folders = self.__definitions.get_bool_value("remove_mei_folders")

            #UI
            self.auto_launch_ui = self.__definitions.get_bool_value("auto_launch_ui")

            self.play_startup_sound = self.__definitions.get_bool_value("play_startup_sound")

            #Conversation
            self.automatic_greeting = self.__definitions.get_bool_value("automatic_greeting")
            self.conversation_summary_enabled = self.__definitions.get_bool_value("conversation_summary_enabled")
            self.inner_monologue_enabled = self.__definitions.get_bool_value("inner_monologue_enabled")
            self.inner_monologue_load_count = self.__definitions.get_int_value("inner_monologue_load_count")
            self.personal_reflection_enabled = self.__definitions.get_bool_value("personal_reflection_enabled")
            self.personal_reflection_load_mode = self.__definitions.get_string_value("personal_reflection_load_mode")
            self.save_summary_now_also_save_inner_thoughts = self.__definitions.get_bool_value("save_summary_now_also_save_inner_thoughts")
            self.save_summary_now_also_save_personal_reflection = self.__definitions.get_bool_value("save_summary_now_also_save_personal_reflection")
            
            # Random LLM Selection
            self.random_llm_one_on_one_enabled = self.__definitions.get_bool_value("random_llm_one_on_one_enabled")
            self.random_llm_multi_npc_enabled = self.__definitions.get_bool_value("random_llm_multi_npc_enabled")
            self.random_llm_one_on_one_per_request_enabled = self.__definitions.get_bool_value("random_llm_one_on_one_per_request_enabled")
            self.random_llm_multi_npc_per_request_enabled = self.__definitions.get_bool_value("random_llm_multi_npc_per_request_enabled")
            try:
                self.llm_pool_one_on_one = json.loads(self.__definitions.get_string_value("llm_pool_one_on_one"))
            except Exception as e:
                log.error(f"Error parsing one-on-one LLM pool: {e}")
                self.llm_pool_one_on_one = []
            try:
                self.llm_pool_multi_npc = json.loads(self.__definitions.get_string_value("llm_pool_multi_npc"))
            except Exception as e:
                log.error(f"Error parsing multi-NPC LLM pool: {e}")
                self.llm_pool_multi_npc = []

            self.sequential_llm_one_on_one_per_request_enabled = self.__definitions.get_bool_value("sequential_llm_one_on_one_per_request_enabled")
            self.sequential_llm_multi_npc_per_request_enabled = self.__definitions.get_bool_value("sequential_llm_multi_npc_per_request_enabled")
            try:
                self.sequential_llm_pool_one_on_one = json.loads(self.__definitions.get_string_value("sequential_llm_pool_one_on_one"))
            except Exception as e:
                log.error(f"Error parsing sequential one-on-one LLM pool: {e}")
                self.sequential_llm_pool_one_on_one = []
            try:
                self.sequential_llm_pool_multi_npc = json.loads(self.__definitions.get_string_value("sequential_llm_pool_multi_npc"))
            except Exception as e:
                log.error(f"Error parsing sequential multi-NPC LLM pool: {e}")
                self.sequential_llm_pool_multi_npc = []
            
            self.max_count_events = self.__definitions.get_int_value("max_count_events")
            self.events_refresh_time = self.__definitions.get_int_value("events_refresh_time")
            self.ingame_events_filter: list[str] = [p.strip().lower() for p in self.__definitions.get_string_value("ingame_events_filter").split(",") if p.strip()]
            self.heard_lines_days_kept: int = self.__definitions.get_int_value("heard_lines_days_kept")
            self.heard_lines_most_per_npc: int = self.__definitions.get_int_value("heard_lines_most_per_npc")
            self.heard_lines_menu_only: list[str] = self.__list_value("heard_lines_menu_only")
            self.heard_lines_menu_talk: bool = self.__definitions.get_bool_value("heard_lines_menu_talk")
            self.heard_lines_scenes: bool = self.__definitions.get_bool_value("heard_lines_scenes")
            self.heard_lines_ignored_npcs: list[str] = self.__list_value("heard_lines_ignored_npcs")
            self.heard_lines_npcs_meet: bool = self.__definitions.get_bool_value("heard_lines_npcs_meet")
            self.heard_lines_story_quests: list[str] = self.__list_value("heard_lines_story_quests")
            self.heard_lines_chatter_quests: list[str] = self.__list_value("heard_lines_chatter_quests")
            self.hourly_time = self.__definitions.get_bool_value("hourly_time")
            self.player_character_description: str = self.__definitions.get_string_value("player_character_description")
            self.voice_player_input: bool = self.__definitions.get_bool_value("voice_player_input")
            self.player_voice_model: str = self.__definitions.get_string_value("player_voice_model")

            #HTTP
            self.port = self.__definitions.get_int_value("port")

            self.log_choices: LogChoices = self.__chosen_log_choices()

            self.save_audio_data_to_character_folder = self.__definitions.get_bool_value("save_audio_data_to_character_folder")

            self.__apply_prompt_values()

            self.multi_conversation_director_mode = self.__definitions.get_bool_value("multi_conversation_director_mode")
            self.multi_npc_game_master_mode = self.__definitions.get_bool_value("multi_npc_game_master_mode")
            self.multi_npc_game_master_director_max_npc_turns = self.__definitions.get_int_value("multi_npc_game_master_director_max_npc_turns")
            self.game_master_history_message_count = self.__definitions.get_int_value("game_master_history_message_count")
            self.game_master_include_turn_thoughts = self.__definitions.get_bool_value("game_master_include_turn_thoughts")

            # Conversation page: the talk settings (the shared set, and each kind's switch and own set), and the Shout and event options
            must_speak_by_name = {option.display_name: option for option in WhoMustSpeakEnum}
            for kind in [SHARED] + TALK_KINDS:
                if kind:
                    setattr(self, use_own_key(kind), self.__definitions.get_bool_value(use_own_key(kind)))
                ticked = self.__definitions.get_string_list_value(talk_key(kind, WHO_MUST_SPEAK))
                setattr(self, talk_key(kind, WHO_MUST_SPEAK), frozenset(must_speak_by_name[name] for name in ticked if name in must_speak_by_name))
                if kind != NPC_EVENT: # A watched NPC Event has nobody "you" talk to
                    setattr(self, talk_key(kind, WHEN_YOU_TALK), self.__definitions.get_enum_value(talk_key(kind, WHEN_YOU_TALK), WhoAnswersFirstEnum))
                setattr(self, talk_key(kind, WHEN_NPC_TALKS), self.__definitions.get_enum_value(talk_key(kind, WHEN_NPC_TALKS), WhoAnswersFirstEnum))
                setattr(self, talk_key(kind, AT_REPLY_LIMIT), self.__definitions.get_enum_value(talk_key(kind, AT_REPLY_LIMIT), AtReplyLimitEnum))
                setattr(self, talk_key(kind, REPLIES_IN_BETWEEN), self.__definitions.get_int_value(talk_key(kind, REPLIES_IN_BETWEEN)))
                setattr(self, talk_key(kind, EXTRA_ROUNDS), self.__definitions.get_int_value(talk_key(kind, EXTRA_ROUNDS)))
            for situation in (GROUP, SHOUT, DIRECTOR, NPC_EVENT):
                setattr(self, most_replies_identifier(situation), self.__definitions.get_int_value(most_replies_identifier(situation)))
            for kind in MAX_NPC_TURNS_KINDS:
                setattr(self, max_npc_turns_identifier(kind), self.__definitions.get_int_value(max_npc_turns_identifier(kind)))
            self.group_conversation_max_answering: int = self.__definitions.get_int_value("group_conversation_max_answering")
            self.shout_max_people: int = self.__definitions.get_int_value("shout_max_people")
            self.shout_max_answering: int = self.__definitions.get_int_value("shout_max_answering")
            self.shout_newcomers_remember: int = self.__definitions.get_enum_value("shout_newcomers_remember", NewcomersRememberEnum).value
            for kind in ("conversation", "nearby", "random"):
                setattr(self, f"{kind}_event_send_history", self.__definitions.get_bool_value(f"{kind}_event_send_history"))
                setattr(self, f"{kind}_event_include_player", self.__definitions.get_bool_value(f"{kind}_event_include_player"))

            # Vision
            self.vision_enabled = self.__definitions.get_bool_value('vision_enabled')
            self.low_resolution_mode = self.__definitions.get_bool_value("low_resolution_mode")
            self.save_screenshot = self.__definitions.get_bool_value('save_screenshot')
            self.image_quality = self.__definitions.get_int_value("image_quality")
            self.resize_method = self.__definitions.get_string_value("resize_method")
            self.capture_offset = json.loads(self.__definitions.get_string_value("capture_offset"))
            self.use_game_screenshots = self.__definitions.get_bool_value("use_game_screenshots")

            # Custom Vision Model
            self.custom_vision_model = self.__definitions.get_bool_value("custom_vision_model")
            self.vision_llm_api = self.__definitions.get_string_value("vision_llm_api")
            self.vision_llm = self.__definitions.get_string_value("vision_model")
            self.vision_llm = self.vision_llm.split(' |')[0] if ' |' in self.vision_llm else self.vision_llm
            self.vision_custom_token_count = self.__definitions.get_int_value("vision_custom_token_count")
            try:
                self.vision_llm_params = json.loads(self.__definitions.get_string_value("vision_llm_params").replace('\n', ''))
            except Exception as e:
                log.error(f"""Error in parsing LLM parameter list: {e}
LLM parameter list must follow the Python dictionary format: https://www.w3schools.com/python/python_dictionaries.asp""")
                self.vision_llm_params = None
            
            pass
        except Exception as e:
            utils.play_error_sound()
            log.error('Parameter missing/invalid in config.ini file!')
            raise e

    def __overlay_prompt(self, prompt_type: str, fallback: str) -> str:
        overlay = self.__prompt_profile_manager.get_active_text(prompt_type)
        return overlay if overlay is not None else fallback

    def __apply_prompt_values(self) -> None:
        self.__prompt_profile_manager.reload()
        if self.game.base_game == GameEnum.FALLOUT4:
            self.prompt = self.__definitions.get_string_value("fallout4_prompt")
            self.multi_npc_prompt = self.__definitions.get_string_value("fallout4_multi_npc_prompt")
            self.radiant_prompt = self.__definitions.get_string_value("fallout4_radiant_prompt")
        else:
            self.prompt = self.__overlay_prompt("skyrim_prompt", self.__definitions.get_string_value("skyrim_prompt"))
            self.multi_npc_prompt = self.__overlay_prompt("skyrim_multi_npc_prompt", self.__definitions.get_string_value("skyrim_multi_npc_prompt"))
            self.multi_npc_director_prompt = self.__overlay_prompt("skyrim_multi_npc_director_prompt", self.__definitions.get_string_value("skyrim_multi_npc_director_prompt"))
            self.game_master_prompt = self.__overlay_prompt("skyrim_game_master_prompt", self.__definitions.get_string_value("skyrim_game_master_prompt"))
            self.game_master_npc_prompt = self.__overlay_prompt("skyrim_game_master_npc_prompt", self.__definitions.get_string_value("skyrim_game_master_npc_prompt"))
            self.game_master_decision_instructions = self.__overlay_prompt("skyrim_game_master_decision_instructions", self.__definitions.get_string_value("skyrim_game_master_decision_instructions"))
            self.game_master_decision_npc_criteria = self.__overlay_prompt("skyrim_game_master_decision_npc_criteria", self.__definitions.get_string_value("skyrim_game_master_decision_npc_criteria"))
            self.game_master_decision_player_criteria = self.__overlay_prompt("skyrim_game_master_decision_player_criteria", self.__definitions.get_string_value("skyrim_game_master_decision_player_criteria"))
            self.npc_initiative_prompt = self.__overlay_prompt("skyrim_npc_initiative_prompt", self.__definitions.get_string_value("skyrim_npc_initiative_prompt"))
            self.npc_initiative_opener = self.__overlay_prompt("skyrim_npc_initiative_opener", self.__definitions.get_string_value("skyrim_npc_initiative_opener"))
            self.npc_initiative_decision_instructions = self.__overlay_prompt("skyrim_npc_initiative_decision_instructions", self.__definitions.get_string_value("skyrim_npc_initiative_decision_instructions"))
            self.npc_initiative_decision_npc_criteria = self.__overlay_prompt("skyrim_npc_initiative_decision_npc_criteria", self.__definitions.get_string_value("skyrim_npc_initiative_decision_npc_criteria"))
            self.npc_initiative_decision_nobody_criteria = self.__overlay_prompt("skyrim_npc_initiative_decision_nobody_criteria", self.__definitions.get_string_value("skyrim_npc_initiative_decision_nobody_criteria"))
            self.npc_event_generator_prompt = self.__overlay_prompt("skyrim_npc_event_generator_prompt", self.__definitions.get_string_value("skyrim_npc_event_generator_prompt"))
            self.npc_event_wind_down_prompt = self.__overlay_prompt("skyrim_npc_event_wind_down_prompt", self.__definitions.get_string_value("skyrim_npc_event_wind_down_prompt"))
            self.conversation_event_generator_prompt = self.__overlay_prompt("skyrim_conversation_event_generator_prompt", self.__definitions.get_string_value("skyrim_conversation_event_generator_prompt"))
            self.shout_decision_nearby_criteria = self.__overlay_prompt("skyrim_shout_decision_nearby_criteria", self.__definitions.get_string_value("skyrim_shout_decision_nearby_criteria"))
            self.shout_decision_nobody_criteria = self.__overlay_prompt("skyrim_shout_decision_nobody_criteria", self.__definitions.get_string_value("skyrim_shout_decision_nobody_criteria"))
            self.shout_decision_addressed_criteria = self.__overlay_prompt("skyrim_shout_decision_addressed_criteria", self.__definitions.get_string_value("skyrim_shout_decision_addressed_criteria"))
            self.shout_decision_not_addressed_criteria = self.__overlay_prompt("skyrim_shout_decision_not_addressed_criteria", self.__definitions.get_string_value("skyrim_shout_decision_not_addressed_criteria"))
            self.shout_decision_event_part_criteria = self.__overlay_prompt("skyrim_shout_decision_event_part_criteria", self.__definitions.get_string_value("skyrim_shout_decision_event_part_criteria"))
            self.shout_decision_event_not_part_criteria = self.__overlay_prompt("skyrim_shout_decision_event_not_part_criteria", self.__definitions.get_string_value("skyrim_shout_decision_event_not_part_criteria"))
            self.shout_group_answer_note = self.__overlay_prompt("skyrim_shout_group_answer_note", self.__definitions.get_string_value("skyrim_shout_group_answer_note"))
            self.radiant_prompt = self.__overlay_prompt("skyrim_radiant_prompt", self.__definitions.get_string_value("skyrim_radiant_prompt"))

        self.radiant_start_prompt = self.__overlay_prompt("radiant_start_prompt", self.__definitions.get_string_value("radiant_start_prompt"))
        self.radiant_end_prompt = self.__overlay_prompt("radiant_end_prompt", self.__definitions.get_string_value("radiant_end_prompt"))
        self.memory_prompt = self.__overlay_prompt("memory_prompt", self.__definitions.get_string_value("memory_prompt"))
        self.resummarize_prompt = self.__overlay_prompt("resummarize_prompt", self.__definitions.get_string_value("resummarize_prompt"))
        self.inner_monologue_prompt = self.__overlay_prompt("inner_monologue_prompt", self.__definitions.get_string_value("inner_monologue_prompt"))
        self.personal_reflection_prompt = self.__overlay_prompt("personal_reflection_prompt", self.__definitions.get_string_value("personal_reflection_prompt"))
        self.vision_prompt = self.__overlay_prompt("vision_prompt", self.__definitions.get_string_value("vision_prompt"))
        self.advanced_actions_prompt = self.__overlay_prompt("advanced_actions_prompt", self.__definitions.get_string_value("advanced_actions_prompt"))
        self.stt_names_prompt = self.__overlay_prompt("stt_names_prompt", self.__definitions.get_string_value("stt_names_prompt"))
        self.decensor_handshake_prompt = self.__overlay_prompt(
            "decensor_handshake_prompt",
            self.__definitions.get_string_value("decensor_handshake_prompt"),
        )
        self.decensor_assistant_prompt = self.__overlay_prompt(
            "decensor_assistant_prompt",
            self.__definitions.get_string_value("decensor_assistant_prompt"),
        )
    
    @staticmethod
    def load_actions_from_json(actions_folder: str) -> list[Action]:
        """The actions in the action files of the folder: the basic ones, which an NPC can start its line with (they have a prompt), and
        the advanced ones, which only Advanced Actions can use"""
        os.makedirs(actions_folder, exist_ok=True)
        FunctionManager.load_all_actions(actions_dir=actions_folder)
        return FunctionManager.get_all_actions()
    
    def get_narration_indicators(self) -> tuple[str, str]:
        if self.narration_indicators == NarrationIndicatorsEnum.BRACKETS:
            return ("[", "]")
        elif self.narration_indicators == NarrationIndicatorsEnum.ASTERISKS:
            return ("*", "*")   
        else:
            return ("(", ")")
    
    # def get_config_value_json(self) -> str:
    #     json_writer = ConfigJsonWriter()
    #     for definition in self.__definitions.base_groups:
    #         definition.accept_visitor(json_writer)
    #     return json_writer.get_Json()

        


        
