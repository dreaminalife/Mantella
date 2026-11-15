"""The Conversation page's settings: the talk settings (a shared set, and each kind's switch and own set), their defaults and wording,
and the other settings of the page"""
import os
import re

import pytest

from src.config.config_loader import ConfigLoader
from src.config.definitions.conversation_definitions import (AT_REPLY_LIMIT, CONVERSATION_EVENT, CONVERSATION_PAGE, EVENT_KINDS, EXTRA_ROUNDS, GROUP,
                                                             NEARBY_EVENT, NPC_EVENT, RANDOM_EVENT, REPLIES_IN_BETWEEN, SHARED, SHOUT, TALK_KINDS,
                                                             TALK_SETTINGS, WHEN_NPC_TALKS, WHEN_YOU_TALK, WHO_MUST_SPEAK, AtReplyLimitEnum,
                                                             WhoAnswersFirstEnum, WhoMustSpeakEnum, max_npc_turns_identifier, most_replies_identifier,
                                                             talk_key, use_own_key)
from src.config.types.config_value import ConfigValueTag
from src.config.types.config_value_multi_selection import ConfigValueMultiSelection
from src.config.types.config_value_selection import ConfigValueSelection

M, F, A = WhoMustSpeakEnum, WhoAnswersFirstEnum, AtReplyLimitEnum
SECTION_OF = {"": "All Conversations", "group_conversation": "Group Conversation", "shout": "Shout", "director": "Director Mode",
              "conversation_event": "Conversation Event", "nearby_event": "Nearby Event", "random_event": "Random Event", "npc_event": "Watched NPC Event"}


@pytest.fixture
def config(tmp_path) -> ConfigLoader:
    return ConfigLoader(str(tmp_path))


def settings_of(kind: str) -> list[str]:
    """The talk settings a set has. A watched NPC Event has nobody "you" talk to"""
    return [setting for setting in TALK_SETTINGS if not (kind == NPC_EVENT and setting == WHEN_YOU_TALK)]


def section_values(config: ConfigLoader, name: str) -> list:
    return next(group for group in config.definitions.base_groups if group.page == CONVERSATION_PAGE and group.name == name).value


@pytest.mark.parametrize("kind", [SHARED] + TALK_KINDS)
def test_each_set_of_talk_settings_sits_in_its_section(config: ConfigLoader, kind):
    identifiers = [value.identifier for value in section_values(config, SECTION_OF[kind])]
    keys = ([use_own_key(kind)] if kind else []) + [talk_key(kind, setting) for setting in settings_of(kind)]
    assert [identifier for identifier in identifiers if identifier in keys] == keys # The switch first, then the set in this order
    if kind == NPC_EVENT:
        assert talk_key(kind, WHEN_YOU_TALK) not in identifiers


@pytest.mark.parametrize("kind", TALK_KINDS)
def test_a_kinds_own_settings_only_show_while_its_switch_is_on(config: ConfigLoader, kind):
    for setting in settings_of(kind):
        assert config.definitions.get_config_value_definition(talk_key(kind, setting)).shown_when == use_own_key(kind)
    assert config.definitions.get_config_value_definition(use_own_key(kind)).shown_when is None
    for setting in TALK_SETTINGS: # The shared set always shows
        assert config.definitions.get_config_value_definition(talk_key(SHARED, setting)).shown_when is None


def test_the_defaults(config: ConfigLoader):
    for kind in [SHARED] + TALK_KINDS:
        if kind:
            assert getattr(config, use_own_key(kind)) == (kind == NPC_EVENT), kind # Only the watched NPC Event uses its own by default
        watched = kind == NPC_EVENT
        assert getattr(config, talk_key(kind, WHO_MUST_SPEAK)) == frozenset([M.NOBODY if watched else M.YOU_TALK_TO]), kind
        if not watched:
            assert getattr(config, talk_key(kind, WHEN_YOU_TALK)) == F.ANSWER_NEXT
        assert getattr(config, talk_key(kind, WHEN_NPC_TALKS)) == (F.GAME_MASTER if watched else F.ANSWER_NEXT), kind
        assert getattr(config, talk_key(kind, AT_REPLY_LIMIT)) == A.NOT_ANSWERED_YOU
        assert (getattr(config, talk_key(kind, REPLIES_IN_BETWEEN)), getattr(config, talk_key(kind, EXTRA_ROUNDS))) == (1, 1)


@pytest.mark.parametrize("kind", [SHARED] + TALK_KINDS)
def test_the_page_shows_tick_boxes_and_radio_buttons_where_the_design_says(config: ConfigLoader, kind):
    must = config.definitions.get_config_value_definition(talk_key(kind, WHO_MUST_SPEAK))
    assert isinstance(must, ConfigValueMultiSelection) and ConfigValueTag.tick_boxes in must.tags
    assert must.options == [option.display_name for option in M if not (kind == NPC_EVENT and option == M.YOU_TALK_TO)]
    at_limit = config.definitions.get_config_value_definition(talk_key(kind, AT_REPLY_LIMIT))
    assert isinstance(at_limit, ConfigValueSelection) and ConfigValueTag.radio_buttons in at_limit.tags
    for setting in (WHEN_YOU_TALK, WHEN_NPC_TALKS):
        if setting in settings_of(kind):
            dropdown = config.definitions.get_config_value_definition(talk_key(kind, setting))
            assert ConfigValueTag.radio_buttons not in dropdown.tags # 2.1 and 2.2 stay dropdowns


def test_the_labels_are_the_designs(config: ConfigLoader):
    """The Text in the UI table, for Group Conversation and Shout"""
    label = lambda setting: config.definitions.get_config_value_definition(talk_key("shout", setting)).name
    assert [label(setting) for setting in TALK_SETTINGS] == [
        "Who must speak before your turn", "When you talk to someone", "When an NPC talks to someone",
        "When the NPCs reach Max NPC Turns per Player Turn, who still speaks", "Replies in between", "Extra rounds"]
    assert [option.display_name for option in M] == ["Everyone in the conversation", "Anyone you talk to", "Anyone an NPC talks to", "Nobody in particular"]
    assert [option.display_name for option in F] == ["They answer next", "Each answers in turn", "The Game Master decides"]
    assert [option.display_name for option in A] == ["Only people who haven't answered you yet", "Everyone who still has to speak", "Nobody, it's your turn now"]
    director = config.definitions.get_config_value_definition(talk_key("director", AT_REPLY_LIMIT)).name
    assert director == "When the NPCs reach Max NPC Turns per Direction, who still speaks"
    event = config.definitions.get_config_value_definition(talk_key("nearby_event", WHO_MUST_SPEAK)).name
    assert event == "Who must speak before your turn" # Each event has its own section, so its name isn't needed in front


DOC_ONLY_WORDS = [r"\blist\b", r"\bx\b", r"max resets", r"owed to an npc", r"\b2\.1\b", r"\b2\.2\b", r"\bsection 1\b", r"\bsection 3\b"]


@pytest.mark.parametrize("kind", [SHARED] + TALK_KINDS)
def test_the_page_never_uses_the_designs_own_words(config: ConfigLoader, kind):
    """The list, x, max resets, owed to an NPC and the section numbers are only the design's names: the page says what happens in the game"""
    values = [config.definitions.get_config_value_definition(talk_key(kind, setting)) for setting in settings_of(kind)]
    if kind:
        values.append(config.definitions.get_config_value_definition(use_own_key(kind)))
    for value in values:
        texts = [value.name, value.description] + list(getattr(value, "option_descriptions", {}).values()) + list(getattr(value, "option_labels", {}).values())
        for text in texts:
            for word in DOC_ONLY_WORDS:
                assert not re.search(word, text, re.IGNORECASE), f"{value.identifier}: {word!r} in {text!r}"


@pytest.mark.parametrize("kind", [SHARED] + TALK_KINDS)
def test_every_choice_is_explained(config: ConfigLoader, kind):
    must = config.definitions.get_config_value_definition(talk_key(kind, WHO_MUST_SPEAK))
    for option in must.options:
        assert f"- {option}: " in must.description, f"{option} is not explained for {kind or 'the shared set'}"
    for setting in (WHEN_YOU_TALK, WHEN_NPC_TALKS, AT_REPLY_LIMIT):
        if setting in settings_of(kind):
            selection = config.definitions.get_config_value_definition(talk_key(kind, setting))
            assert set(selection.option_descriptions) == set(selection.options)
            for line in selection.option_descriptions.values():
                assert line and line[0].isupper()


def test_the_wording_fits_each_kind_of_conversation(config: ConfigLoader):
    description = lambda kind, setting: config.definitions.get_config_value_definition(talk_key(kind, setting)).description
    assert "your direction is about" in description("director", WHO_MUST_SPEAK)
    assert "the people the event is about" in description("conversation_event", WHO_MUST_SPEAK)
    assert "Max Lines Without Player" in description("npc_event", REPLIES_IN_BETWEEN)
    labels = config.definitions.get_config_value_definition(talk_key("npc_event", AT_REPLY_LIMIT)).option_labels
    assert labels[A.NOBODY.display_name] == "Nobody, the scene ends now" # Saved as the same option as everywhere, shown in the scene's words


def test_who_must_speak_refuses_boxes_that_cant_go_together(config: ConfigLoader):
    must = config.definitions.get_config_value_definition(WHO_MUST_SPEAK)
    assert not must.does_value_cause_error([M.YOU_TALK_TO.display_name, M.NPC_TALKS_TO.display_name]).error_message
    for value in ([], [M.NOBODY.display_name, M.YOU_TALK_TO.display_name], [M.EVERYONE.display_name, M.YOU_TALK_TO.display_name]):
        assert must.does_value_cause_error(value).error_message


def test_talk_settings_saved_in_config_ini_are_read_back(tmp_path):
    config = ConfigLoader(str(tmp_path))
    set_value = lambda identifier, value: setattr(config.definitions.get_config_value_definition(identifier), "value", value)
    set_value(use_own_key("shout"), True)
    set_value(talk_key("shout", WHO_MUST_SPEAK), [M.YOU_TALK_TO.display_name, M.NPC_TALKS_TO.display_name])
    set_value(talk_key("shout", WHEN_NPC_TALKS), F.EACH_IN_TURN.display_name)
    set_value(talk_key("shout", AT_REPLY_LIMIT), A.NOBODY.display_name)
    set_value(talk_key("shout", REPLIES_IN_BETWEEN), 5)
    set_value(talk_key("shout", EXTRA_ROUNDS), 20)
    set_value(WHO_MUST_SPEAK, [M.EVERYONE.display_name])
    config.update_config_loader_with_changed_config_values()
    for loaded in (config, ConfigLoader(str(tmp_path))):
        assert loaded.shout_use_own_talk_settings is True
        assert loaded.shout_who_must_speak == frozenset([M.YOU_TALK_TO, M.NPC_TALKS_TO])
        assert (loaded.shout_when_an_npc_talks_to_someone, loaded.shout_who_still_speaks_at_reply_limit) == (F.EACH_IN_TURN, A.NOBODY)
        assert (loaded.shout_replies_in_between, loaded.shout_extra_rounds) == (5, 20)
        assert loaded.who_must_speak == frozenset([M.EVERYONE])


def test_the_old_talk_level_settings_are_gone(config: ConfigLoader):
    identifiers = {value.identifier for group in config.definitions.base_groups for value in group.value}
    assert not [identifier for identifier in identifiers if identifier.endswith("_answer_rule")]


def test_the_other_settings_load_with_their_defaults(config: ConfigLoader):
    for situation in ("group_conversation", "shout", "director", "npc_event"):
        assert getattr(config, most_replies_identifier(situation)) == 100
    assert config.group_conversation_max_answering == 5
    # The defaults the MCM had
    assert (config.shout_max_people, config.shout_max_answering, config.shout_newcomers_remember) == (6, 5, 0)
    assert (config.conversation_event_send_history, config.conversation_event_include_player) == (True, True)
    assert (config.nearby_event_send_history, config.nearby_event_include_player) == (False, False)
    assert (config.random_event_send_history, config.random_event_include_player) == (True, True)


@pytest.mark.parametrize("event", ["conversation", "nearby", "random"])
def test_each_event_has_its_own_section_with_its_own_settings(config: ConfigLoader, event):
    names = [(value.identifier, value.name) for value in section_values(config, SECTION_OF[f"{event}_event"])]
    assert names[-3:] == [(f"{event}_event_send_history", "Send the Conversation So Far"), (f"{event}_event_include_player", "Include Me"),
                          (f"{event}_event_max_npc_turns", "Max NPC Turns per Player Turn")]
    assert all(identifier.startswith(f"{event}_event_") for identifier, _name in names) # Nothing of the other events
    assert all(":" not in name for _identifier, name in names) # No event name in front of the labels


MOVED_FROM_LLM_TAB = ["game_master_addressed_threshold", "multi_npc_game_master_director_max_npc_turns",
                      "npc_event_max_participants", "npc_event_player_notice_delay", "npc_event_max_lines_without_player", "npc_event_wind_down_lines"]


def test_the_conversation_page_holds_its_settings_in_order_and_the_llm_tab_no_longer_does(config: ConfigLoader):
    groups = [group for group in config.definitions.base_groups if group.page == CONVERSATION_PAGE]
    assert [group.name for group in groups] == ["All Conversations", "Actions", "Group Conversation", "Shout", "Director Mode", "Conversation Event", "Nearby Event", "Random Event", "Watched NPC Event"]
    on_page = [value.identifier for group in groups for value in group.value]
    for identifier in MOVED_FROM_LLM_TAB + ["shout_max_people", "shout_max_answering", "shout_newcomers_remember", "group_conversation_max_answering"]:
        assert identifier in on_page
    llm = next(group for group in config.definitions.base_groups if group.identifier == "LLM")
    assert not set(MOVED_FROM_LLM_TAB) & {value.identifier for value in llm.value}


def test_a_moved_setting_in_an_old_config_ini_section_still_loads(tmp_path):
    # Saved before the move, under [LLM]
    (tmp_path / "config.ini").write_text("[LLM]\nmulti_npc_game_master_director_max_npc_turns = 7\n", encoding="utf-8")
    assert ConfigLoader(str(tmp_path)).multi_npc_game_master_director_max_npc_turns == 7


@pytest.mark.parametrize("kind, section", [(GROUP, "Group Conversation"), (SHOUT, "Shout"), (CONVERSATION_EVENT, "Conversation Event"),
                                           (NEARBY_EVENT, "Nearby Event"), (RANDOM_EVENT, "Random Event")])
def test_each_kind_of_conversation_has_its_own_max_npc_turns(config: ConfigLoader, kind, section):
    values = {value.identifier: value for value in section_values(config, section)}
    assert values[max_npc_turns_identifier(kind)].name == "Max NPC Turns per Player Turn"
    assert getattr(config, max_npc_turns_identifier(kind)) == 3
    assert "multi_npc_game_master_max_npc_turns" not in {value.identifier for value in section_values(config, "All Conversations")}


def test_a_kinds_own_max_npc_turns_is_read_from_config_ini(tmp_path):
    (tmp_path / "config.ini").write_text("[ConversationShout]\nshout_max_npc_turns = 7\n", encoding="utf-8")
    loaded = ConfigLoader(str(tmp_path))
    assert (loaded.shout_max_npc_turns, loaded.group_conversation_max_npc_turns) == (7, 3) # Only Shout's changes


def my_config_folder() -> str:
    """Where Mantella keeps your config.ini (Documents\\My Games\\Mantella), or "" if this machine has none"""
    try:
        from src.utils import get_my_games_directory
        folder = get_my_games_directory("")
    except Exception:
        return ""
    return folder if folder and os.path.isfile(os.path.join(folder, "config.ini")) else ""


def test_your_config_ini_loads_without_unknown_settings(tmp_path):
    """After moving your config.ini over to the talk settings by hand: every setting in it is known, so loading it doesn't log
    "Could not identify config value", play the error sound or write a backup of it. Its talk settings can all be saved"""
    folder = my_config_folder()
    if not folder:
        pytest.skip("No config.ini in My Games\\Mantella on this machine")
    from configparser import ConfigParser
    parser = ConfigParser(interpolation=None)
    parser.read(os.path.join(folder, "config.ini"), encoding="utf-8")
    definitions = ConfigLoader(str(tmp_path)).definitions
    known = {value.identifier: value for group in definitions.base_groups for value in group.value}
    unknown = [key for section in parser.sections() for key in parser[section] if key not in known]
    assert not unknown, f"Unknown settings in {folder}config.ini: {unknown}"
    talk_keys = {talk_key(kind, setting) for kind in [SHARED] + TALK_KINDS for setting in TALK_SETTINGS} | {use_own_key(kind) for kind in TALK_KINDS}
    for section in parser.sections():
        for key, text in parser[section].items():
            if key in talk_keys:
                assert known[key].parse(text).is_success, f"{key} = {text}"
