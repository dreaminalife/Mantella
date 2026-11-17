"""The settings page keeps the sounds it plays in a folder of its own, emptied each time Mantella starts"""
import os
import tempfile

from src.ui.ui_files_folder import FOLDER_NAME, use_own_ui_files_folder


def test_the_settings_page_folder_is_emptied_at_start(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("GRADIO_TEMP_DIR", "somewhere else") # put back after the test
    old_sound = tmp_path / FOLDER_NAME / "abc123" / "test_line.wav"
    old_sound.parent.mkdir(parents=True)
    old_sound.write_bytes(b"RIFF")
    folder = use_own_ui_files_folder()
    assert folder == str(tmp_path / FOLDER_NAME)
    assert os.path.isdir(folder) and os.listdir(folder) == []
    assert os.environ["GRADIO_TEMP_DIR"] == folder
