"""The Speech-to-Text page of Mantella's UI: Talking first, then Service with the Test button at its end, each a fold that starts open.
Settings side by side, switches beside switches and boxes beside boxes, and settings that can't change anything greyed out"""
import gradio as gr
import pytest

from src.config.config_loader import ConfigLoader
from src.config.definitions.stt_definitions import GREYED_UNLESS_ON
from src.ui.start_ui import StartUI
from tests.ui.test_conversation_page import handlers_of, name_of
from tests.ui.test_tts_page import tab_of

TALKING_ROWS = [("ptt_enabled", "proactive_mic_mode"), ("ptt_hotkey", "min_refresh_secs"),
                ("audio_threshold", "pause_threshold", "listen_timeout"), ("allow_interruption", "play_cough_sound", "save_mic_input")]


@pytest.fixture
def page(default_config: ConfigLoader):
    default_config.auto_launch_ui = False
    ui = StartUI(default_config)
    blocks = ui.create_main_block()
    shown = ui._StartUI__constructor._SettingsUIConstructor__config_value_to_ui_element
    by_identifier = {config_value.identifier: component for config_value, component in shown.items()}
    on_page = [c for c in blocks.blocks.values() if tab_of(c) == "Speech-to-Text"]
    return blocks, by_identifier, on_page


def row_of(component):
    """The row a setting's panel sits in: two settings share one when they are side by side"""
    panel = component.parent
    while panel is not None and not (isinstance(panel, gr.Column) and panel.variant == "panel"):
        panel = panel.parent
    return panel.parent if panel is not None and isinstance(panel.parent, gr.Row) else None


def test_talking_comes_first_then_service_then_the_test(page):
    blocks, _, on_page = page
    folds = [c for c in on_page if isinstance(c, gr.Accordion)]
    assert [c.label for c in folds] == ["Talking", "Service", "How the test works"] # no Advanced, no Custom Service fold
    assert folds[0].open and folds[1].open
    heads = [c.value.splitlines()[0] for c in on_page if isinstance(c, gr.Markdown) and str(c.value or "").startswith(("## ", "### "))]
    assert heads == ["### Test Speech-to-Text"]
    assert [c.value for c in on_page if isinstance(c, gr.Button)][-1] == "Test"


def test_talking_settings_sit_side_by_side_in_rows(page):
    _, by_identifier, _ = page
    rows = [row_of(by_identifier[identifiers[0]]) for identifiers in TALKING_ROWS]
    for row, identifiers in zip(rows, TALKING_ROWS):
        assert row is not None and all(row_of(by_identifier[i]) is row for i in identifiers), identifiers
        assert len([c for c in row.children if isinstance(c, gr.Column)]) == len(identifiers), identifiers
    assert len(set(map(id, rows))) == len(rows)


def test_service_pairs(page):
    _, by_identifier, _ = page
    for first, second in (("stt_service", "stt_language"), ("whisper_model_size", "process_device"), ("stt_custom_model", "stt_custom_timeout")):
        row = row_of(by_identifier[first])
        assert row is not None and row is row_of(by_identifier[second]), (first, second)


def test_custom_service_is_a_choice_with_its_settings_shown_for_it(page):
    _, by_identifier, _ = page
    assert "Custom Service" in [choice[0] if isinstance(choice, tuple) else choice for choice in by_identifier["stt_service"].choices]
    assert "stt_use_custom_service" not in by_identifier
    for identifier in ("stt_custom_format", "stt_custom_address", "stt_custom_model", "stt_custom_timeout"):
        column = by_identifier[identifier].parent
        while column is not None and not (isinstance(column, gr.Column) and column.visible is False):
            column = column.parent
        assert column is not None, identifier # hidden while Moonshine, the default, is picked


@pytest.mark.parametrize("target, switch", GREYED_UNLESS_ON.items())
def test_a_setting_is_greyed_out_while_its_switch_is_off(page, target, switch):
    blocks, by_identifier, _ = page
    handler = next(h for h in handlers_of(blocks, by_identifier[switch], "change") if name_of(h) == "stt_greying")
    assert handler.outputs == [by_identifier[target]]
    assert handler.fn(False)["interactive"] is False
    assert handler.fn(True)["interactive"] is True


def test_catch_first_names_is_greyed_out_unless_the_service_takes_separate_names(page):
    blocks, by_identifier, _ = page
    handler = next(h for h in handlers_of(blocks, by_identifier["stt_online_service"], "change") if name_of(h) == "stt_names_greying")
    assert handler.fn("Online Service", "Deepgram", "OpenAI-style")["interactive"] is True
    assert handler.fn("Online Service", "Groq", "OpenAI-style")["interactive"] is False
    assert handler.fn("Custom Service", "Groq", "ElevenLabs-style")["interactive"] is True
    assert handler.fn("Moonshine", "Deepgram", "OpenAI-style")["interactive"] is False
