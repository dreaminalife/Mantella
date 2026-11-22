import logging
import threading

from src.logs.log_topic import LogTopic

LOGGER_PREFIX = "mantella"
SPOKEN_LINE = "mantella_spoken_line" # Set on a record that is a spoken line, so the window shows it like a script
PLAYERS_LINE = "mantella_players_line" # Set on a spoken line the player said
LINE_COLOUR = "mantella_line_colour" # Set on a line the window shows in its own colour instead of its topic's

PROMPT_SUFFIX = "prompt"

_TOPICS_BY_LOGGER_NAME: dict[str, LogTopic] = {f"{LOGGER_PREFIX}.{topic.key}": topic for topic in LogTopic}
_TOPICS_BY_LOGGER_NAME.update({f"{LOGGER_PREFIX}.{topic.key}.{PROMPT_SUFFIX}": topic for topic in LogTopic if topic.prompt_text})


def get_topic_logger(topic: LogTopic) -> logging.Logger:
    """The logger for a topic. log.info is shown at Short, log.debug at Full, and log.warning / log.error always"""
    return logging.getLogger(f"{LOGGER_PREFIX}.{topic.key}")


def get_prompt_logger(topic: LogTopic) -> logging.Logger:
    """The logger for a topic's prompt (logged once when it is made) and, for LLM requests and replies, each raw reply. log.info is shown at Prompt and replies and Full"""
    return logging.getLogger(f"{LOGGER_PREFIX}.{topic.key}.{PROMPT_SUFFIX}")


def is_prompt_line(record: logging.LogRecord) -> bool:
    """A line written to a topic's prompt logger: the prompt sent to the LLM, or a raw reply"""
    return record.name.startswith(f"{LOGGER_PREFIX}.") and record.name.endswith(f".{PROMPT_SUFFIX}")


def topic_of(record: logging.LogRecord) -> LogTopic | None:
    """The topic a line was written to. None for lines of other programs"""
    return _TOPICS_BY_LOGGER_NAME.get(record.name)


def in_colour(colour: str) -> dict[str, str]:
    """The extra for a line the window shows in its own colour, e.g. log.info(text, extra=in_colour(LINE_WHITE))"""
    return {LINE_COLOUR: colour}


def colour_of(record: logging.LogRecord) -> str | None:
    """A line's own colour, None for its topic's"""
    return getattr(record, LINE_COLOUR, None)


def is_spoken_line(record: logging.LogRecord) -> bool:
    return bool(getattr(record, SPOKEN_LINE, False))


def is_players_line(record: logging.LogRecord) -> bool:
    return bool(getattr(record, PLAYERS_LINE, False))


def spoken_line(log: logging.Logger, speaker: str, text: str, by_player: bool = False) -> None:
    """A line someone said, at Short. The player's lines show in their own colour. A player's line held by hold_players_line comes first"""
    show_players_line()
    log.info("%s: %s", speaker, text, extra={SPOKEN_LINE: True, PLAYERS_LINE: by_player})


_held_players_line: tuple[logging.Logger, str, str] | None = None
_held_lock = threading.Lock()


def hold_players_line(log: logging.Logger, speaker: str, text: str) -> None:
    """The player's line, kept back so it shows right above the reply to it instead of above the lines about asking the LLM.
    Shown by the next spoken line, or by show_players_line when no line answers (the conversation ends, an error, actions only)"""
    global _held_players_line
    show_players_line() # one held before and never answered still shows, in its place
    with _held_lock:
        _held_players_line = (log, speaker, text)


def show_players_line() -> None:
    """Shows the player's line held by hold_players_line, if there is one"""
    global _held_players_line
    with _held_lock:
        held, _held_players_line = _held_players_line, None
    if held:
        log, speaker, text = held
        log.info("%s: %s", speaker, text, extra={SPOKEN_LINE: True, PLAYERS_LINE: True})
