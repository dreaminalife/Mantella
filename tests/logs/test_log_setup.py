import logging

import pytest

from src.logs.log_detail import LogDetail
from src.logs.log_setup import MantellaLogSetup
from src.logs.log_topic import LogTopic, default_log_choices
from src.logs.topic_logger import get_prompt_logger, get_topic_logger


@pytest.fixture
def log_setup(tmp_path):
    """A started log in a temporary file. The root logger and the topic loggers are put back afterwards"""
    root = logging.getLogger()
    root_handlers, root_level = list(root.handlers), root.level
    topic_levels = {topic: get_topic_logger(topic).level for topic in LogTopic}
    prompt_level = get_prompt_logger(LogTopic.AI_REPLIES).level
    setup = MantellaLogSetup()
    path = tmp_path / "logging.log"
    setup.start(str(path), "1.2.3", default_log_choices())
    yield setup, path
    for handler in list(root.handlers):
        if handler not in root_handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(root_level)
    for topic, level in topic_levels.items():
        get_topic_logger(topic).setLevel(level)
    get_prompt_logger(LogTopic.AI_REPLIES).setLevel(prompt_level)


def file_text(path) -> str:
    for handler in logging.getLogger().handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


def test_each_topic_follows_its_choice(log_setup):
    setup, _ = log_setup
    choices = default_log_choices()
    choices[LogTopic.VOICES] = LogDetail.OFF
    choices[LogTopic.GAME_MASTER] = LogDetail.FULL
    setup.apply(choices)

    assert not get_topic_logger(LogTopic.VOICES).isEnabledFor(logging.INFO)
    assert get_topic_logger(LogTopic.CONVERSATION).isEnabledFor(logging.INFO)
    assert not get_topic_logger(LogTopic.CONVERSATION).isEnabledFor(logging.DEBUG)
    assert get_topic_logger(LogTopic.GAME_MASTER).isEnabledFor(logging.DEBUG)


def test_off_still_shows_warnings_and_errors(log_setup):
    setup, path = log_setup
    choices = default_log_choices()
    choices[LogTopic.VOICES] = LogDetail.OFF
    setup.apply(choices)

    voices = get_topic_logger(LogTopic.VOICES)
    voices.info("Lydia uses FemaleNord")
    voices.error("The voice service stopped")

    text = file_text(path)
    assert "Lydia uses FemaleNord" not in text
    assert "[Voices] ERROR: The voice service stopped" in text


def test_quest_tags_at_off_hide_warnings_too(log_setup):
    setup, path = log_setup
    choices = default_log_choices()
    choices[LogTopic.QUEST_TAGS] = LogDetail.OFF
    choices[LogTopic.VOICES] = LogDetail.OFF
    setup.apply(choices)

    get_topic_logger(LogTopic.QUEST_TAGS).warning("Quest tag x slot 1: can't read the condition")
    get_topic_logger(LogTopic.QUEST_TAGS).error("Quest tags broke")
    get_topic_logger(LogTopic.VOICES).warning("Lydia's voice is missing")

    text = file_text(path)
    assert "can't read the condition" not in text
    assert "[Quest tags] ERROR: Quest tags broke" in text
    assert "Lydia's voice is missing" in text # other topics at Off still show warnings


def test_quest_tags_at_short_show_warnings(log_setup):
    _, path = log_setup
    get_topic_logger(LogTopic.QUEST_TAGS).warning("Quest tag x slot 1: can't read the condition")
    assert "can't read the condition" in file_text(path)


def test_short_lines_show_and_full_lines_wait_for_full(log_setup):
    setup, path = log_setup
    log = get_topic_logger(LogTopic.GAME_MASTER)
    log.info("Lydia speaks next")
    log.debug("The whole question")
    assert "Lydia speaks next" in file_text(path)
    assert "The whole question" not in file_text(path)

    choices = default_log_choices()
    choices[LogTopic.GAME_MASTER] = LogDetail.FULL
    setup.apply(choices) # no restart
    log.debug("The whole question")
    assert "The whole question" in file_text(path)


@pytest.mark.parametrize("detail, shows_prompt", [(LogDetail.OFF, False), (LogDetail.SHORT, False),
                                                   (LogDetail.WITH_PROMPT, True), (LogDetail.FULL, True)])
def test_the_prompt_shows_at_with_prompt_and_full(log_setup, detail, shows_prompt):
    setup, path = log_setup
    choices = default_log_choices()
    choices[LogTopic.AI_REPLIES] = detail
    setup.apply(choices)
    get_prompt_logger(LogTopic.AI_REPLIES).info("Prompt sent to LLM: You are Lydia")
    get_topic_logger(LogTopic.AI_REPLIES).debug("Request to the model")

    text = file_text(path)
    assert ("[LLM requests and replies] Prompt sent to LLM: You are Lydia" in text) == shows_prompt
    assert ("Request to the model" in text) == (detail == LogDetail.FULL)


def test_other_programs_only_show_warnings_and_errors(log_setup):
    _, path = log_setup
    other = logging.getLogger("httpx")
    other.info("HTTP Request: POST")
    other.warning("Retrying")
    text = file_text(path)
    assert "HTTP Request" not in text
    assert "[httpx] WARNING: Retrying" in text


def test_the_file_starts_with_the_version_and_the_choices(log_setup):
    setup, path = log_setup
    text = file_text(path)
    assert "Mantella v1.2.3. Log: Conversation Short," in text
    assert "Game connection Off" in text

    choices = default_log_choices()
    choices[LogTopic.TIMINGS] = LogDetail.FULL
    setup.apply(choices)
    assert "Log changed:" in file_text(path)
    assert "Timings Full" in file_text(path)


def test_the_header_is_written_even_with_startup_off(tmp_path):
    root = logging.getLogger()
    root_handlers, root_level = list(root.handlers), root.level
    startup_level = get_topic_logger(LogTopic.STARTUP).level
    try:
        choices = default_log_choices()
        choices[LogTopic.STARTUP] = LogDetail.OFF
        path = tmp_path / "logging.log"
        MantellaLogSetup().start(str(path), "1.2.3", choices)
        assert "Mantella v1.2.3" in file_text(path)
    finally:
        for handler in list(root.handlers):
            if handler not in root_handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(root_level)
        get_topic_logger(LogTopic.STARTUP).setLevel(startup_level)


def test_every_topic_at_off_still_shows_its_errors(log_setup):
    '''So the player always learns when something isn't working, whatever they picked'''
    setup, path = log_setup
    setup.apply({topic: LogDetail.OFF for topic in LogTopic})
    for topic in LogTopic:
        get_topic_logger(topic).info(f"{topic.key} short line")
        get_topic_logger(topic).error(f"{topic.key} broke")
    text = file_text(path)
    for topic in LogTopic:
        assert f"[{topic.label}] ERROR: {topic.key} broke" in text
        assert f"{topic.key} short line" not in text


def test_problems_before_the_log_starts_reach_the_file(tmp_path):
    '''Settings load before the log starts (they say what it shows): their warnings still end up in logging.log'''
    root = logging.getLogger()
    root_handlers, root_level = list(root.handlers), root.level
    topic_levels = {topic: get_topic_logger(topic).level for topic in LogTopic}
    try:
        setup = MantellaLogSetup()
        setup.catch_early_problems()
        get_topic_logger(LogTopic.STARTUP).warning("Could not identify config value 'x = 1'")
        get_topic_logger(LogTopic.STARTUP).info("Not a problem, not kept")
        path = tmp_path / "logging.log"
        setup.start(str(path), "1.2.3", default_log_choices())
        text = file_text(path)
        assert "[Startup] WARNING: Could not identify config value 'x = 1'" in text
        assert "Not a problem" not in text
        assert not any(type(handler).__name__ == "EarlyProblems" for handler in root.handlers) # gone once the log started
    finally:
        for handler in list(root.handlers):
            if handler not in root_handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(root_level)
        for topic, level in topic_levels.items():
            get_topic_logger(topic).setLevel(level)


def test_applying_the_same_choices_writes_nothing(log_setup):
    setup, path = log_setup
    setup.apply(default_log_choices())
    assert "Log changed" not in file_text(path)
