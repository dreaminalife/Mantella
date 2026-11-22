import logging

from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger, hold_players_line, is_players_line, show_players_line, spoken_line


class Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def collecting(topic: LogTopic) -> tuple[logging.Logger, Collect]:
    log = get_topic_logger(topic)
    handler = Collect()
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return log, handler


def test_players_line_shows_right_above_the_reply():
    """The lines about asking the LLM come before the player's line, not between it and the reply"""
    log, handler = collecting(LogTopic.CONVERSATION)
    try:
        hold_players_line(log, "Yuji", "hey")
        log.info("Asking google/gemini-4-neon for a reply")
        spoken_line(log, "Tamda", "Hey there?")
        assert [r.getMessage() for r in handler.records] == ["Asking google/gemini-4-neon for a reply", "Yuji: hey", "Tamda: Hey there?"]
        assert is_players_line(handler.records[1]) and not is_players_line(handler.records[2])
    finally:
        log.removeHandler(handler)


def test_players_line_with_no_reply_still_shows():
    """A goodbye or an error: nothing answers, the line shows when the conversation ends"""
    log, handler = collecting(LogTopic.CONVERSATION)
    try:
        hold_players_line(log, "Yuji", "goodbye")
        assert handler.records == []
        show_players_line()
        show_players_line() # shown once only
        assert [r.getMessage() for r in handler.records] == ["Yuji: goodbye"]
    finally:
        log.removeHandler(handler)


def test_an_unanswered_players_line_shows_before_the_next_one():
    log, handler = collecting(LogTopic.CONVERSATION)
    try:
        hold_players_line(log, "Yuji", "hello?")
        hold_players_line(log, "Yuji", "anyone?")
        show_players_line()
        assert [r.getMessage() for r in handler.records] == ["Yuji: hello?", "Yuji: anyone?"]
    finally:
        log.removeHandler(handler)
