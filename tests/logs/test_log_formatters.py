import logging

from src.logs.log_formatters import DIM, ERROR_STYLE, PLAYERS_LINE_STYLE, SPOKEN_STYLE, FileFormatter, WARNING_STYLE, WindowFormatter
from src.logs.log_topic import LINE_GREY, LogTopic
from src.logs.topic_logger import LINE_COLOUR, PLAYERS_LINE, SPOKEN_LINE, get_topic_logger


def record(logger_name: str, level: int, message: str, **extra) -> logging.LogRecord:
    made = logging.LogRecord(logger_name, level, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(made, key, value)
    return made


VOICES = get_topic_logger(LogTopic.VOICES).name


def test_window_line_has_the_topic_tag_in_its_colour():
    text = WindowFormatter().format(record(VOICES, logging.INFO, "Lydia uses FemaleNord"))
    assert text.startswith(LogTopic.VOICES.colour + "[Voices] Lydia uses FemaleNord")


def test_window_line_with_its_own_colour():
    '''The raw reply is grey, whatever its topic's colour'''
    name = get_topic_logger(LogTopic.AI_REPLIES).name
    text = WindowFormatter().format(record(name, logging.INFO, "Full raw response (12 tokens): Lydia: Hm.", **{LINE_COLOUR: LINE_GREY}))
    assert text.startswith(LINE_GREY + f"[{LogTopic.AI_REPLIES.label}] Full raw response")


def test_window_warning_keeps_its_colour_over_a_lines_own():
    text = WindowFormatter().format(record(VOICES, logging.WARNING, "Clip missing", **{LINE_COLOUR: LINE_GREY}))
    assert text.startswith(WARNING_STYLE)


def test_window_full_lines_are_dimmed():
    text = WindowFormatter().format(record(VOICES, logging.DEBUG, "Clip is 2.1 seconds"))
    assert text.startswith(LogTopic.VOICES.colour + DIM + "[Voices] ")


def test_window_spoken_line_is_green_with_no_tag():
    text = WindowFormatter().format(record(get_topic_logger(LogTopic.CONVERSATION).name, logging.INFO, "Lydia: I am sworn to carry your burdens.",
                                           **{SPOKEN_LINE: True}))
    assert text.startswith(SPOKEN_STYLE + "Lydia: I am sworn")
    assert "[Conversation]" not in text


def test_window_players_line_has_its_own_colour():
    text = WindowFormatter().format(record(get_topic_logger(LogTopic.CONVERSATION).name, logging.INFO, "Yuji: hey there",
                                           **{SPOKEN_LINE: True, PLAYERS_LINE: True}))
    assert text.startswith(PLAYERS_LINE_STYLE + "Yuji: hey there")
    assert PLAYERS_LINE_STYLE != SPOKEN_STYLE


def test_window_problems_are_yellow_and_red_with_their_tag():
    assert WindowFormatter().format(record(VOICES, logging.WARNING, "Slow")).startswith(WARNING_STYLE + "[Voices] Slow")
    assert WindowFormatter().format(record(VOICES, logging.ERROR, "Stopped")).startswith(ERROR_STYLE + "[Voices] Stopped")


def test_window_other_programs_are_tagged_by_name():
    assert "[httpx] Retrying" in WindowFormatter().format(record("httpx", logging.WARNING, "Retrying"))


def test_file_line_has_time_tag_and_level_on_problems():
    formatter = FileFormatter()
    info = formatter.format(record(VOICES, logging.INFO, "Lydia uses FemaleNord"))
    error = formatter.format(record(VOICES, logging.ERROR, "Stopped"))
    assert info.endswith(" [Voices] Lydia uses FemaleNord")
    assert error.endswith(" [Voices] ERROR: Stopped")
    assert "\x1b[" not in info # no colours in the file


def test_file_line_includes_the_exception():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys
        failed = logging.LogRecord(VOICES, logging.ERROR, __file__, 1, "Failed", None, sys.exc_info())
    text = FileFormatter().format(failed)
    assert "[Voices] ERROR: Failed" in text
    assert "RuntimeError: boom" in text
