"""The settings page's own folder for the files it shows and takes in (sounds from the test buttons, uploaded files).
Gradio copies every sound it plays there and never removes them, so each test button press would leave a file behind.
Mantella gives the page a folder of its own and empties it each time Mantella starts, before the page shows anything"""
from __future__ import annotations

import os
import shutil
import tempfile

from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.SETTINGS_PAGE)

FOLDER_NAME = "mantella_settings_page"


def use_own_ui_files_folder() -> str:
    folder = os.path.join(tempfile.gettempdir(), FOLDER_NAME)
    shutil.rmtree(folder, ignore_errors=True)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        log.warning(f"Could not make the settings page's folder {folder}: {e}")
        return folder
    os.environ["GRADIO_TEMP_DIR"] = folder # read by Gradio when the page is built and whenever it stores a file
    return folder
