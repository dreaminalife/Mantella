"""The Text-to-Speech page of Mantella's UI: XTTS's and xVASynth's settings in folded parts of their own"""
import gradio as gr

from src.config.config_loader import ConfigLoader
from src.ui.start_ui import StartUI
from tests.ui.test_conversation_page import accordions_of


def test_xtts_and_xvasynth_settings_have_folded_parts_of_their_own(default_config: ConfigLoader):
    default_config.auto_launch_ui = False
    ui = StartUI(default_config)
    blocks = ui.create_main_block()
    shown = ui._StartUI__constructor._SettingsUIConstructor__config_value_to_ui_element
    part_of = {config_value.identifier: accordions_of(component)[:1] for config_value, component in shown.items()}
    for identifier in ("xtts_server_folder", "xtts_url", "xtts_default_model", "xtts_device", "xtts_deepspeed", "xtts_lowvram", "xtts_data", "xtts_accent"):
        assert part_of[identifier] == ["XTTS"], identifier
    for identifier in ("xvasynth_folder", "tts_process_device", "pace", "use_cleanup", "use_sr"):
        assert part_of[identifier] == ["xVASynth"], identifier
    for identifier in ("lip_generation", "fast_response_mode", "fast_response_mode_volume", "tts_print"):
        assert part_of[identifier] == ["Advanced"], identifier
    for identifier in ("tts_service", "tts_backup_service", "number_words_tts"):
        assert part_of[identifier] == [], identifier
    labels = [component.label for component in blocks.blocks.values() if isinstance(component, gr.Accordion)]
    start = labels.index("xVASynth") # other pages have an Advanced part too
    assert labels[start:start + 4] == ["xVASynth", "XTTS", "Advanced", "Try A Voice"]


def test_config_ini_keeps_them_in_the_tts_section(default_config: ConfigLoader):
    tts = next(group for group in default_config.definitions.base_groups if group.identifier == "TTS")
    identifiers = {value.identifier for value in tts.value}
    assert {"xtts_server_folder", "xtts_url", "xvasynth_folder", "use_sr"} <= identifiers
