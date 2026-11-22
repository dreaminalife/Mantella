from src.config.types.config_value import ConfigValue, ConfigValueTag
from src.config.types.config_value_selection import ConfigValueSelection
from src.logs.log_detail import LogDetail
from src.logs.log_topic import LogTopic

LOG_PART = "Log" # The folded part of the Other tab these show in
LOG_PART_INTRO = "Pick how much each part of Mantella shows in its window and log file. Where a part asks the LLM something, **Full** shows the whole request and reply."
RETIRED_LOG_KEYS = ("advanced_logs", "show_http_debug_messages") # Debug Mode and Show HTTP Debug Messages, which the Log part replaces
OFF_TEXT = "Only warnings and errors."
OFF_TEXT_ERRORS_ONLY = "Only errors." # For a topic that hides its warnings at Off, see LogTopic.hides_warnings_when_off


def _lower_first(text: str) -> str:
    """For a Full text after "plus": none of them starts with a name"""
    return text[:1].lower() + text[1:]


def _off_text(topic: LogTopic) -> str:
    return OFF_TEXT_ERRORS_ONLY if topic.hides_warnings_when_off else OFF_TEXT


def _option_descriptions(topic: LogTopic) -> dict[str, str]:
    """The text under the buttons, which shows only the picked option, so the others spell out what Short shows"""
    texts = {
        LogDetail.OFF: _off_text(topic),
        LogDetail.SHORT: topic.short_text,
        LogDetail.WITH_PROMPT: f"{topic.short_text} Plus {_lower_first(topic.prompt_text)}",
        LogDetail.FULL: f"{topic.short_text} Plus {_lower_first(topic.full_text)}",
    }
    return {detail.value: texts[detail] for detail in topic.details}


def _tooltip_option_texts(topic: LogTopic) -> dict[str, str]:
    """The tooltip lists all options together, so the others can point back to Short's line"""
    texts = {
        LogDetail.OFF: _off_text(topic),
        LogDetail.SHORT: topic.short_text,
        LogDetail.WITH_PROMPT: f"Everything Short shows, plus {_lower_first(topic.prompt_text)}",
        LogDetail.FULL: f"Everything Short shows, plus {_lower_first(topic.full_text)}",
    }
    return {detail.value: texts[detail] for detail in topic.details}


def _always_shown_text(topic: LogTopic) -> str:
    return "Errors always show." if topic.hides_warnings_when_off else "Warnings and errors always show."


class LogDefinitions:
    @staticmethod
    def get_log_config_value(topic: LogTopic) -> ConfigValue:
        options = _option_descriptions(topic)
        description = (f"What Mantella's window and log file show about {topic.label}:\n"
                       + "\n".join(f"- {option}: {text}" for option, text in _tooltip_option_texts(topic).items())
                       + f"\n{_always_shown_text(topic)} Changes apply right away.")
        return ConfigValueSelection(topic.setting_id, topic.label, description, topic.default.value, [detail.value for detail in topic.details],
                                    corresponding_enums=topic.details, tags=[ConfigValueTag.radio_buttons],
                                    option_descriptions=options).in_folded_part(LOG_PART)

    @staticmethod
    def get_log_config_values() -> list[ConfigValue]:
        return [LogDefinitions.get_log_config_value(topic) for topic in LogTopic]
