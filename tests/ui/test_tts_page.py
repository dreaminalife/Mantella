"""The Text-to-Speech page of Mantella's UI: Speed and lip sync at the top, then one service's settings at a time, then Voice Effects and
Fix Pronunciation at the end"""
import gradio as gr

from src.config.config_loader import ConfigLoader
from src.config.definitions.tts_definitions import NPC_VOICES_PART, SERVICE_PARTS, SERVICE_VOLUMES, service_part_id, sound_part_id
from src.ui.start_ui import StartUI

PART_NAMES = {service_part_id(name): name for name in (*SERVICE_PARTS, NPC_VOICES_PART)}
SOUND_NAMES = {sound_part_id(name): name for name in SERVICE_PARTS} # each service's Model and Global settings, in its part under Settings for


def part_of(component) -> str | None:
    """The fold or the service's part of the Text-to-Speech page a component is in, nearest first"""
    parent = getattr(component, "parent", None)
    while parent is not None:
        if isinstance(parent, gr.Accordion):
            return parent.label
        if getattr(parent, "elem_id", None) in PART_NAMES:
            return PART_NAMES[parent.elem_id]
        parent = getattr(parent, "parent", None)
    return None


def service_of(component) -> str | None:
    """The service whose part, or whose Global settings, a component is in"""
    parent = getattr(component, "parent", None)
    while parent is not None:
        elem_id = getattr(parent, "elem_id", None)
        if elem_id in SOUND_NAMES:
            return SOUND_NAMES[elem_id]
        if elem_id in PART_NAMES:
            return PART_NAMES[elem_id]
        parent = getattr(parent, "parent", None)
    return None


def tab_of(component) -> str | None:
    parent = getattr(component, "parent", None)
    while parent is not None:
        if isinstance(parent, gr.Tab):
            return parent.label
        parent = getattr(parent, "parent", None)
    return None


def test_each_setting_is_in_its_part_of_the_page(default_config: ConfigLoader):
    default_config.auto_launch_ui = False
    ui = StartUI(default_config)
    blocks = ui.create_main_block()
    shown = ui._StartUI__constructor._SettingsUIConstructor__config_value_to_ui_element
    part = {config_value.identifier: part_of(component) for config_value, component in shown.items()}
    for identifier in ("xtts_server_folder", "xtts_url", "xtts_default_model", "xtts_device", "xtts_deepspeed", "xtts_lowvram", "xtts_data", "xtts_accent"):
        assert part[identifier] == "XTTS", identifier
    for identifier in ("xvasynth_folder", "tts_process_device", "pace", "use_cleanup", "use_sr"):
        assert part[identifier] == "xVASynth", identifier
    for identifier in ("number_words_tts", "lip_generation", "fast_response_mode", "fast_response_mode_volume", "tts_print"):
        assert part[identifier] == "Speed and lip sync", identifier
    for identifier in ("tts_service", "tts_backup_service", "elevenlabs_model", "inworld_model", "heygen_model", "qwen_model", "boson_model"):
        assert part[identifier] is None, identifier # the Models are next to TTS Service
    on_page = [c for c in blocks.blocks.values() if tab_of(c) == "Text-to-Speech"]
    folds = [c.label for c in on_page if isinstance(c, gr.Accordion) and c.parent is not None and part_of(c) is None]
    assert folds.count("How model settings work") == 5 # one for each service with models, under Backup Voice Service
    folds = [label for label in folds if label != "How model settings work"]
    assert folds == ["Speed and lip sync", "Hear it", NPC_VOICES_PART, "Voice Effects", "Fix Pronunciation"]
    sounds = [SOUND_NAMES[c.elem_id] for c in on_page if isinstance(c, gr.Column) and c.elem_id in SOUND_NAMES]
    assert sounds == [name for name in SERVICE_PARTS if name in SERVICE_VOLUMES] # one each, for the services with voice settings
    assert all(part_of(c) == SOUND_NAMES[c.elem_id] for c in on_page if isinstance(c, gr.Column) and c.elem_id in SOUND_NAMES) # in its service's part
    every_npc = [c for c in on_page if isinstance(c, gr.Accordion) and c.label.startswith("Global settings for ")]
    assert len(every_npc) == len(SERVICE_VOLUMES) and not any(c.open for c in every_npc) # folded
    parts = [PART_NAMES[c.elem_id] for c in on_page if isinstance(c, gr.Column) and c.elem_id in PART_NAMES]
    assert parts == [*SERVICE_PARTS, NPC_VOICES_PART] # one part each, NPC voices under them
    word_lists = [part_of(c) for c in blocks.blocks.values() if isinstance(c, gr.Button) and c.value == "Reload the lists"]
    assert word_lists == ["Fix Pronunciation"] # only in its own part


def test_config_ini_keeps_them_in_the_tts_section(default_config: ConfigLoader):
    tts = next(group for group in default_config.definitions.base_groups if group.identifier == "TTS")
    identifiers = {value.identifier for value in tts.value}
    assert {"xtts_server_folder", "xtts_url", "xvasynth_folder", "use_sr", "number_words_tts", "tts_print"} <= identifiers
