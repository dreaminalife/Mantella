import configparser
import os
from typing import Hashable
import src.utils as utils
import pandas as pd
import sys
from src.config.config_loader import ConfigLoader
from src.logs.console_window import WINDOWS_TERMINAL_TIP, is_classic_console
from src.logs.log_setup import ILogSetup, MantellaLogSetup
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.STARTUP)

class MantellaSetup:
    def __init__(self):
        self.save_folder = ""
        self.config = None
        self.language_info = {}

    def initialise(self, config_file, logging_file, language_file, version: str, log_setup: ILogSetup | None = None) -> tuple[ConfigLoader, dict[Hashable, str]]:
        '''Initialize Mantella with configuration, logging, and language settings'''
        self._set_cwd_to_exe_dir()
        self.log_setup = log_setup or MantellaLogSetup()
        self.log_setup.catch_early_problems() # The log only starts once the settings say what it shows
        self.save_folder = utils.get_my_games_directory(self._get_custom_user_folder())
        self.config = ConfigLoader(self.save_folder, config_file)
        self.log_setup.start(os.path.join(self.save_folder, logging_file), version, self.config.log_choices)
        self.config.use_log_setup(self.log_setup)
        self.config.log_loaded_settings()

        log.info(f'Mantella Remake v{version}')
        log.info(f'''Mantella.exe running in: 
    {os.getcwd()}
    config.ini, logging.log, and conversation histories available in:
    {self.save_folder}''')
        log.info(f'''Mantella currently running for {self.config.game.display_name}. Mantella mod files located in: 
    {self.config.mod_path}''')
        if is_classic_console():
            log.info(WINDOWS_TERMINAL_TIP)
        if not self.config.have_all_config_values_loaded_correctly:
            log.error("Cannot start Mantella. Not all settings that are required are set to correct values. This error often occurs when you start Mantella.exe manually without setting up the `Game` tab in the Mantella UI.")

        # clean up old instances of exe runtime files
        utils.cleanup_mei(self.config.remove_mei_folders)
        utils.cleanup_tmp(self.config.save_folder+'data\\tmp')
        utils.cleanup_tmp(os.getenv('TMP')+'\\voicelines') # cleanup temp voicelines

        self.language_info = self._get_language_info(language_file, self.config.language)
        
        return self.config, self.language_info

    def _set_cwd_to_exe_dir(self):
        '''Set the current working directory to the executable's directory if running as exe'''
        if getattr(sys, 'frozen', False): # if exe and not Python script
            # change the current working directory to the executable's directory
            os.chdir(os.path.dirname(sys.executable))

    def _get_custom_user_folder(self) -> str:
        '''Find Mantella's saved data folder from configuration file if it exists'''
        file_name = "custom_user_folder.ini"
        if not os.path.exists(file_name):
            return ""

        user_folder_config = configparser.ConfigParser()
        try:
            user_folder_config.read(file_name, encoding='utf-8')
            
        except Exception as e:
            utils.play_error_sound()
            log.error(repr(e))
            log.error(f"Unable to read / open '{file_name}'. If you have recently edited this file, please try reverting to a previous version. This error is normally due to using special characters.")
            log.warning("Using default user folder in '../Documents/My Games/Mantella/'.")
            return ""      
        
        try:
            return user_folder_config.get("UserFolder","custom_user_folder")
        except Exception as e:
            utils.play_error_sound()
            log.error(f"Could not find option 'custom_user_folder' in section 'UserFolder' in '{file_name}'.")
            log.warning("Using default user folder in '../Documents/My Games/Mantella/'.")
            return ""

    def _get_language_info(self, file_name, language) -> dict[Hashable, str]:
        language_df = pd.read_csv(file_name)
        try:
            language_info: dict[Hashable, str] = language_df.loc[language_df['alpha2']==language].to_dict('records')[0]
            return language_info
        except:
            log.error(f"Could not load language '{language}'. Please set a valid language in config.ini\n")
            return {}
