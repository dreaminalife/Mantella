import logging

from src.logs.topic_logger import colour_of, is_players_line, is_spoken_line, topic_of

RESET = "\x1b[0m"
BRIGHT = "\x1b[1m"
DIM = "\x1b[2m"
WARNING_STYLE = BRIGHT + "\x1b[93m"
ERROR_STYLE = BRIGHT + "\x1b[91m"
CRITICAL_STYLE = BRIGHT + "\x1b[97m\x1b[41m"
SPOKEN_STYLE = "\x1b[92m" # Green, as the conversation always was
PLAYERS_LINE_STYLE = "\x1b[95m" # Magenta: what the player said

FILE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _tag(record: logging.LogRecord) -> str:
    """[Topic] for Mantella's lines, [name] for other programs, nothing for lines without a logger of their own"""
    topic = topic_of(record)
    if topic:
        return f"[{topic.label}] "
    if record.name and record.name != "root":
        return f"[{record.name}] "
    return ""


class WindowFormatter(logging.Formatter):
    """Mantella.exe's window: a coloured topic tag before each line. Spoken lines have no tag: the NPCs' green, the player's magenta, so the conversation reads like
    a script. Full lines are dimmed, warnings are yellow and errors red"""

    def __init__(self) -> None:
        super().__init__("%(message)s")

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if is_spoken_line(record):
            return (PLAYERS_LINE_STYLE if is_players_line(record) else SPOKEN_STYLE) + text + RESET
        if record.levelno >= logging.CRITICAL:
            style = CRITICAL_STYLE
        elif record.levelno >= logging.ERROR:
            style = ERROR_STYLE
        elif record.levelno >= logging.WARNING:
            style = WARNING_STYLE
        else:
            topic = topic_of(record)
            style = colour_of(record) or (topic.colour if topic else "")
            if record.levelno < logging.INFO:
                style += DIM
        return style + _tag(record) + text + RESET


class FileFormatter(logging.Formatter):
    """logging.log: time, topic tag, and WARNING / ERROR on problems"""

    def __init__(self) -> None:
        super().__init__("%(message)s", FILE_TIME_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        problem = f"{record.levelname}: " if record.levelno >= logging.WARNING else ""
        return f"{self.formatTime(record, self.datefmt)} {_tag(record)}{problem}{message}"
