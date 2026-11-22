from src.setup import MantellaSetup
import pytest
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
import configparser
from pathlib import Path
from unittest.mock import patch
from pytest import MonkeyPatch
from pytest import LogCaptureFixture
import pandas as pd
import src.utils as utils
from src.logs.log_detail import LogDetail
from src.logs.log_setup import ILogSetup
from src.logs.log_topic import LogTopic, default_log_choices

@pytest.fixture
def mantella_setup() -> MantellaSetup:
    return MantellaSetup()

@pytest.fixture
def languages_csv_file(tmp_path: Path) -> str:
    data = {
        'alpha2': ['en', 'fr'],
        'name': ['English', 'French']
    }
    df = pd.DataFrame(data)
    file_path = tmp_path / "languages.csv"
    df.to_csv(file_path, index=False)
    return str(file_path)


def test_init(mantella_setup: MantellaSetup):
    assert mantella_setup.save_folder == ""
    assert mantella_setup.config is None
    assert mantella_setup.language_info == {}


@patch('os.chdir')
def test_set_cwd_to_exe_dir(mock_chdir, mantella_setup: MantellaSetup, monkeypatch: MonkeyPatch):
    # Test when not frozen (regular Python script)
    mantella_setup._set_cwd_to_exe_dir()
    mock_chdir.assert_not_called()
    
    # Test when frozen (executable)
    original_getattr = getattr
    def mock_getattr(obj, name, default=None):
        if obj is sys and name == 'frozen':
            return True
        return original_getattr(obj, name, default)
    
    # Mock the executable path
    mock_executable = '/mock/path/executable'
    monkeypatch.setattr(sys, 'executable', mock_executable)
    
    # Replace the built-in getattr with mock version
    monkeypatch.setattr('builtins.getattr', mock_getattr)
    
    mantella_setup._set_cwd_to_exe_dir()
    mock_chdir.assert_called_once_with(os.path.dirname(sys.executable))


def test_get_custom_user_folder_no_file(mantella_setup: MantellaSetup, tmp_path: Path, monkeypatch: MonkeyPatch):
    # Change to a temporary directory to avoid actual file
    monkeypatch.chdir(tmp_path)
    
    # Test when file doesn't exist
    result = mantella_setup._get_custom_user_folder()
    assert result == ""

def test_get_custom_user_folder_with_file(mantella_setup: MantellaSetup, tmp_path: Path, monkeypatch: MonkeyPatch):
    # Change to a temporary directory
    monkeypatch.chdir(tmp_path)
    
    # Create a test config file
    config = configparser.ConfigParser()
    config['UserFolder'] = {'custom_user_folder': str(tmp_path / 'test_folder')}
    
    with open('custom_user_folder.ini', 'w', encoding='utf-8') as f:
        config.write(f)
    
    # Test when file exists with valid data
    result = mantella_setup._get_custom_user_folder()
    assert result == str(tmp_path / 'test_folder')

def test_get_custom_user_folder_invalid_file(mantella_setup: MantellaSetup, tmp_path: Path, monkeypatch: MonkeyPatch, caplog):
    # Change to a temporary directory
    monkeypatch.chdir(tmp_path)
    
    # Create an invalid test config file
    with open('custom_user_folder.ini', 'w', encoding='utf-8') as f:
        f.write('This is not a valid config file')
    
    # Test with invalid file
    result = mantella_setup._get_custom_user_folder()
    assert result == ""
    assert "Unable to read / open 'custom_user_folder.ini'" in caplog.text

def test_get_custom_user_folder_missing_section(mantella_setup: MantellaSetup, tmp_path: Path, monkeypatch: MonkeyPatch, caplog: LogCaptureFixture):
    # Change to a temporary directory
    monkeypatch.chdir(tmp_path)
    
    # Create a test config file with missing section
    config = configparser.ConfigParser()
    config['WrongSection'] = {'custom_user_folder': str(tmp_path / 'test_folder')}
    
    with open('custom_user_folder.ini', 'w', encoding='utf-8') as f:
        config.write(f)
    
    # Test when file exists but section is missing
    result = mantella_setup._get_custom_user_folder()
    assert result == ""
    assert "Could not find option 'custom_user_folder'" in caplog.text


def test_initialise_starts_the_log_with_the_players_choices(mantella_setup: MantellaSetup, tmp_path: Path, monkeypatch: MonkeyPatch):
    '''The log starts after the settings are loaded, with the version and what each topic shows, and settings changes reach it'''
    class FakeLogSetup(ILogSetup):
        def __init__(self):
            self.started = None
            self.applied = []
            self.caught_early = False
        def catch_early_problems(self):
            self.caught_early = True
        def start(self, file_path, version, choices):
            self.started = (file_path, version, choices)
        def apply(self, choices):
            self.applied.append(choices)

    monkeypatch.setattr(utils, "get_my_games_directory", lambda custom_folder: str(tmp_path) + os.sep)
    monkeypatch.setattr(utils, "cleanup_mei", lambda remove: None) # leave the real temp folders alone
    monkeypatch.setattr(utils, "cleanup_tmp", lambda folder: None)
    log_setup = FakeLogSetup()
    config, _ = mantella_setup.initialise("config.ini", "logging.log", "data/language_support.csv", version="9.9.9", log_setup=log_setup)

    assert log_setup.caught_early # before the settings load, so their warnings reach the file
    file_path, version, choices = log_setup.started
    assert file_path == os.path.join(str(tmp_path) + os.sep, "logging.log")
    assert version == "9.9.9"
    assert choices == default_log_choices()

    config.definitions.get_config_value_definition(LogTopic.TIMINGS.setting_id).value = LogDetail.FULL.value
    assert log_setup.applied[-1][LogTopic.TIMINGS] == LogDetail.FULL


def test_get_language_info_valid(mantella_setup: MantellaSetup, languages_csv_file: str):
    result = mantella_setup._get_language_info(languages_csv_file, 'en')
    expected = {'alpha2': 'en', 'name': 'English'}
    assert result == expected

def test_get_language_info_invalid(mantella_setup: MantellaSetup, languages_csv_file: str, caplog: LogCaptureFixture):
    with caplog.at_level(logging.ERROR):
        result = mantella_setup._get_language_info(languages_csv_file, 'de')  # 'de' is not present in the test CSV
    assert result == {}
    assert any("Could not load language 'de'" in message for message in caplog.text.splitlines())