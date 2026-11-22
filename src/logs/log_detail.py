import logging
from enum import Enum


class LogDetail(Enum):
    """How much of a topic the log shows. The value is what the settings page and config.ini show"""
    OFF = "Off"
    SHORT = "Short"
    WITH_PROMPT = "Prompt and replies" # Short, plus the prompt once when it is made (and for LLM requests and replies, each raw reply). Only for topics with a prompt text, see LogTopic.details
    FULL = "Full"

    @property
    def level(self) -> int:
        """The level the topic's logger is set to: Off still lets warnings and errors through, Short adds the info lines, Full the debug lines"""
        return _LEVELS[self]

    @property
    def shows_prompt(self) -> bool:
        """Whether the topic's prompt logger (see get_prompt_logger) shows its lines"""
        return self in (LogDetail.WITH_PROMPT, LogDetail.FULL)

    @classmethod
    def from_setting(cls, value: str, default: "LogDetail", allowed: "list[LogDetail]") -> "LogDetail":
        for detail in allowed:
            if detail.value == value:
                return detail
        return default


_LEVELS: dict[LogDetail, int] = {
    LogDetail.OFF: logging.WARNING,
    LogDetail.SHORT: logging.INFO,
    LogDetail.WITH_PROMPT: logging.INFO,
    LogDetail.FULL: logging.DEBUG,
}
