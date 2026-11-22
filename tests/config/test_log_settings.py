from src.config.config_loader import ConfigLoader
from src.config.definitions.log_definitions import LOG_PART
from src.config.types.config_value import ConfigValueTag
from src.config.types.config_value_selection import ConfigValueSelection
from src.logs.log_detail import LogDetail
from src.logs.log_topic import LogTopic


def test_one_setting_per_topic_in_the_other_tabs_log_part(tmp_path):
    config = ConfigLoader(tmp_path)
    other = next(group for group in config.definitions.base_groups if group.identifier == "Other")
    log_settings = [value for value in other.value if value.shown_in == LOG_PART]

    assert [value.identifier for value in log_settings] == [topic.setting_id for topic in LogTopic]
    assert len(log_settings) == 18
    for value in log_settings:
        assert isinstance(value, ConfigValueSelection)
        expected = ["Off", "Short", "Prompt and replies", "Full"] if value.identifier == LogTopic.AI_REPLIES.setting_id else ["Off", "Short", "Full"]
        assert value.options == expected
        assert ConfigValueTag.radio_buttons in value.tags


def test_defaults_are_quiet(tmp_path):
    config = ConfigLoader(tmp_path)
    off = {topic for topic, detail in config.log_choices.items() if detail == LogDetail.OFF}
    assert off == {LogTopic.GAME_CONNECTION, LogTopic.TIMINGS}
    assert LogDetail.FULL not in config.log_choices.values()


def test_ai_replies_show_the_prompt_by_default(tmp_path):
    '''The NPCs' prompt shows once per conversation, as it did before the Log part, without every whole request'''
    config = ConfigLoader(tmp_path)
    assert config.log_choices[LogTopic.AI_REPLIES] == LogDetail.WITH_PROMPT
    assert [topic for topic, detail in config.log_choices.items() if detail == LogDetail.WITH_PROMPT] == [LogTopic.AI_REPLIES]


def test_with_prompt_is_only_read_for_topics_that_offer_it():
    assert LogDetail.from_setting("Prompt and replies", LogDetail.SHORT, LogTopic.GAME_MASTER.details) == LogDetail.SHORT
    assert LogDetail.from_setting("Prompt and replies", LogDetail.SHORT, LogTopic.AI_REPLIES.details) == LogDetail.WITH_PROMPT


def test_with_prompt_says_what_it_adds():
    from src.config.definitions.log_definitions import LogDefinitions
    value = LogDefinitions.get_log_config_value(LogTopic.AI_REPLIES)
    assert value.option_descriptions["Prompt and replies"] == (LogTopic.AI_REPLIES.short_text
                                                        + " Plus the NPCs' prompt, once when a conversation starts and again when someone joins, and each reply exactly as the LLM wrote it, with its token count.")
    assert "- Prompt and replies: Everything Short shows, plus the NPCs' prompt" in value.description


def test_the_old_switches_are_gone(tmp_path):
    config = ConfigLoader(tmp_path)
    for old in ("advanced_logs", "show_http_debug_messages"):
        try:
            config.definitions.get_config_value_definition(old)
        except Exception:
            continue
        raise AssertionError(f"{old} is still a setting")


def test_the_old_switches_are_dropped_quietly(tmp_path, caplog):
    '''A config.ini from before the Log part still has Debug Mode and Show HTTP Debug Messages: they are dropped without the
    unknown-setting warning (and its error sound) or a backup'''
    import logging
    ConfigLoader(tmp_path) # writes a config.ini with the current settings
    config_path = tmp_path / "config.ini"
    with open(config_path, "a", encoding="utf-8") as f:
        f.write("\n[Debug]\nadvanced_logs = True\nshow_http_debug_messages = True\n")

    with caplog.at_level(logging.DEBUG):
        ConfigLoader(tmp_path)

    assert "Could not identify config value" not in caplog.text
    assert not list(tmp_path.glob("config_backup_*.ini"))
    text = config_path.read_text(encoding="utf-8")
    assert "advanced_logs" not in text
    assert "show_http_debug_messages" not in text


def test_the_description_keeps_capitals():
    from src.config.definitions.log_definitions import LogDefinitions
    assert "- Short: Mantella's version" in LogDefinitions.get_log_config_value(LogTopic.STARTUP).description


def test_the_log_part_says_what_it_is_for(tmp_path):
    from src.config.definitions.log_definitions import LOG_PART_INTRO
    config = ConfigLoader(tmp_path)
    other = next(group for group in config.definitions.base_groups if group.identifier == "Other")
    assert other.folded_part_intros[LOG_PART] == LOG_PART_INTRO


def test_llm_requests_full_says_it_shows_the_whole_request():
    from src.config.definitions.log_definitions import LogDefinitions
    value = LogDefinitions.get_log_config_value(LogTopic.AI_REPLIES)
    assert "the whole request every turn (the prompt and the conversation history)" in value.option_descriptions["Full"]


def test_quest_tags_off_says_only_errors_show():
    from src.config.definitions.log_definitions import LogDefinitions
    value = LogDefinitions.get_log_config_value(LogTopic.QUEST_TAGS)
    assert value.option_descriptions["Off"] == "Only errors."
    assert "- Off: Only errors." in value.description
    assert "Errors always show." in value.description
    assert "Warnings and errors always show." not in value.description


def test_each_option_says_what_it_shows():
    from src.config.definitions.log_definitions import LogDefinitions
    value = LogDefinitions.get_log_config_value(LogTopic.GAME_MASTER)
    assert value.option_descriptions["Off"] == "Only warnings and errors."
    assert value.option_descriptions["Short"] == LogTopic.GAME_MASTER.short_text
    assert value.option_descriptions["Full"] == LogTopic.GAME_MASTER.short_text + " Plus the whole question asked to the Game Master and its whole answer."
    assert "- Full: Everything Short shows, plus the whole question" in value.description
