"""Which actions NPCs can use in each kind of conversation (src/actions/action_rules.py), the Actions section of the Conversation page that sets it
(src/config/definitions/action_definitions.py), carrying over the old Actions list, and the default prompts that list the actions"""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.actions.action_rules import INVOLVE_PLAYER, actions_for, offers_look, tool_actions_for
from src.config.config_loader import ConfigLoader
from src.config.definitions.action_definitions import (ACTION_KINDS, ADVANCED_ACTIONS_DEFAULT_MODEL, DIRECTOR, GROUP, NPC_EVENT, ONE_ON_ONE, ORIGINAL_ACTIONS, RADIANT, SHOUT,
                                                       WITHOUT_PLAYER, actions_key, carried_over_ticks, default_ticks)
from src.config.definitions.game_definitions import GameEnum
from src.config.definitions.language_definitions import LanguageDefinitions
from src.config.definitions.prompt_definitions import PromptDefinitions
from src.conversation.action import Action
from src.conversation.conversation_type import multi_npc, multi_npc_game_master, npc_event, pc_to_npc, radiant, shout
from src.llm.output.actions_parser import actions_parser
from src.llm.output.output_parser import sentence_generation_settings
from src.llm.sentence_content import SentenceContent, SentenceTypeEnum
from src import utils

ACTIONS_FOLDER = os.path.join(utils.resolve_path(), "data", "actions")
# The basic actions whose game script acts on the player
INVOLVE_PLAYER_NAMES = {"Attack", "Brawl", "Flee", "Teleport", "Inventory", "Barter", "Follow", "Unfollow", "MoveTo"}
BASIC = ["Attack", "Barter", "Brawl", "CollectIngredients", "Flee", "Follow", "Inventory", "Listen", "Loot", "MoveTo",
         "StandDown", "Teleport", "Unfollow", "Wait"] # Look (needs Vision) and EndConversation (never offered) are loaded too
ADVANCED = ["AbsolveCrime", "AddToConversation", "CancelTravel", "CastSpell", "CheckDirections", "Emote", "LeadTo", "ReportCrime",
            "ShareConversation", "TravelTo"] # Only with Advanced Actions on


@pytest.fixture(scope="module")
def loaded() -> list[Action]:
    """The original Mantella's basic actions, as loaded (without other mods' files)"""
    return [a for a in ConfigLoader.load_actions_from_json(ACTIONS_FOLDER) if a.name in ORIGINAL_ACTIONS and a.is_basic and a.name not in ("Look", "EndConversation")]


def config_with(actions: list[Action], **ticked: list[str]) -> SimpleNamespace:
    """A config with these actions, each kind's switches as given, or its defaults"""
    values = {actions_key(kind): ticked.get(kind, default_ticks(actions, kind)) for kind in ACTION_KINDS}
    return SimpleNamespace(all_actions=actions, **values)


def names(actions: list[Action]) -> list[str]:
    return sorted(a.name for a in actions)


# The actions

def test_the_original_basic_actions_are_loaded(loaded: list[Action]):
    assert names(loaded) == sorted(BASIC)


def test_every_action_that_involves_the_player_is_known_by_its_identifier(loaded: list[Action]):
    assert {a.identifier for a in loaded if a.name in INVOLVE_PLAYER_NAMES} <= INVOLVE_PLAYER


# Defaults: the original Mantella's

@pytest.mark.parametrize("kind, expected", [
    (ONE_ON_ONE, ["Attack", "Barter", "Flee", "Inventory", "MoveTo", "StandDown"]),
    (GROUP, ["Attack", "Barter", "Flee", "Inventory", "MoveTo", "StandDown"]),
    (SHOUT, ["Attack", "Barter", "Flee", "Inventory", "MoveTo", "StandDown"]),
    (NPC_EVENT, ["Attack", "Flee", "MoveTo", "StandDown"]),
    (DIRECTOR, ["Attack", "Flee", "MoveTo", "StandDown"]),
    (RADIANT, ["Attack", "Flee", "MoveTo", "StandDown"])])
def test_each_kind_starts_from_the_original_defaults(loaded: list[Action], kind: str, expected: list[str]):
    assert sorted(default_ticks(loaded, kind)) == expected


@pytest.mark.parametrize("kind, expected", [
    (ONE_ON_ONE, ["Attack", "Barter", "Flee", "Inventory", "MoveTo", "StandDown"]),
    (SHOUT, ["Attack", "Barter", "Flee", "Inventory", "MoveTo", "StandDown"]),
    (NPC_EVENT, ["StandDown"]),
    (DIRECTOR, ["StandDown"]),
    (RADIANT, ["StandDown"])])
def test_by_default_npcs_who_dont_know_the_player_is_there_get_only_the_short_list(loaded: list[Action], kind: str, expected: list[str]):
    assert names(actions_for(config_with(loaded), kind)) == expected


# Every action in every kind of conversation

@pytest.mark.parametrize("kind", ACTION_KINDS)
@pytest.mark.parametrize("name", BASIC)
def test_a_ticked_action_is_offered_unless_it_would_involve_a_player_the_npcs_dont_know_is_there(loaded: list[Action], name: str, kind: str):
    offered = names(actions_for(config_with(loaded, **{kind: [name]}), kind))
    blocked = kind in WITHOUT_PLAYER and name in INVOLVE_PLAYER_NAMES
    assert offered == ([] if blocked else [name])


@pytest.mark.parametrize("kind", ACTION_KINDS)
def test_an_unticked_action_is_never_offered(loaded: list[Action], kind: str):
    assert actions_for(config_with(loaded, **{kind: []}), kind) == []


@pytest.mark.parametrize("off_in", ACTION_KINDS)
def test_switching_an_action_off_for_one_kind_hides_it_there_and_only_there(loaded: list[Action], off_in: str):
    everywhere = {kind: ["Wait", "StandDown"] for kind in ACTION_KINDS}
    everywhere[off_in] = ["StandDown"]
    config = config_with(loaded, **everywhere)
    for kind in ACTION_KINDS:
        assert ("Wait" in names(actions_for(config, kind))) == (kind != off_in), kind


def test_end_conversation_is_never_offered_to_npcs():
    """Only the player ends a conversation"""
    actions = ConfigLoader.load_actions_from_json(ACTIONS_FOLDER)
    assert [a for a in actions if a.name == "EndConversation"] # Loaded with the others
    config = config_with(actions, **{kind: ["EndConversation"] for kind in ACTION_KINDS})
    config.advanced_actions_enabled = True
    assert all(actions_for(config, kind) == [] and tool_actions_for(config, kind) == [] for kind in ACTION_KINDS)


@pytest.mark.parametrize("vision", [True, False])
def test_look_is_only_offered_with_vision_on(vision: bool):
    actions = ConfigLoader.load_actions_from_json(ACTIONS_FOLDER)
    config = config_with(actions, **{kind: ["Look"] for kind in ACTION_KINDS})
    config.vision_enabled = vision
    assert all(names(actions_for(config, kind)) == (["Look"] if vision else []) for kind in ACTION_KINDS)
    assert all(offers_look(config, kind) == vision for kind in ACTION_KINDS)


def test_the_settings_list_every_action_but_end_conversation(tmp_path):
    names = [a.name for a in ConfigLoader(str(tmp_path)).all_actions]
    assert "EndConversation" not in names
    assert set(ADVANCED + BASIC + ["Look"]) <= set(names)


def test_the_advanced_actions_are_loaded_without_a_prompt():
    actions = [a for a in ConfigLoader.load_actions_from_json(ACTIONS_FOLDER) if a.name in ORIGINAL_ACTIONS and not a.is_basic]
    assert names(actions) == sorted(ADVANCED)


# Advanced Actions: the actions the AI model can pick with their details

def advanced_config(loaded_all: list[Action], **ticked: list[str]) -> SimpleNamespace:
    config = config_with(loaded_all, **ticked)
    config.advanced_actions_enabled = True
    return config


@pytest.fixture(scope="module")
def loaded_all() -> list[Action]:
    """The original Mantella's actions, basic and advanced, as loaded"""
    return [a for a in ConfigLoader.load_actions_from_json(ACTIONS_FOLDER) if a.name in ORIGINAL_ACTIONS and a.name not in ("Look", "EndConversation")]


@pytest.mark.parametrize("kind", ACTION_KINDS)
def test_with_advanced_actions_off_the_model_gets_no_tools(loaded_all: list[Action], kind: str):
    config = config_with(loaded_all, **{kind: ADVANCED + BASIC})
    assert tool_actions_for(config, kind) == []
    assert not any(a.name in ADVANCED for a in actions_for(config, kind)) # Never in the prompt either


@pytest.mark.parametrize("kind", ACTION_KINDS)
def test_with_advanced_actions_on_the_ticked_actions_are_tools(loaded_all: list[Action], kind: str):
    tools = names(tool_actions_for(advanced_config(loaded_all, **{kind: ADVANCED + BASIC}), kind))
    if kind in WITHOUT_PLAYER:
        # What acts on the player whoever is named is left out. Attack, Flee, Teleport and MoveTo stay, aimed at NPCs only
        left_out = {"Barter", "Brawl", "Follow", "Inventory", "Unfollow", "AbsolveCrime", "ReportCrime"}
    else:
        left_out = set()
    assert tools == sorted(set(ADVANCED + BASIC) - left_out - {"Listen"}) # Listen is the player's


def test_an_unticked_action_is_no_tool(loaded_all: list[Action]):
    assert names(tool_actions_for(advanced_config(loaded_all, **{ONE_ON_ONE: ["CastSpell"]}), ONE_ON_ONE)) == ["CastSpell"]


def test_each_action_instruction_ends_with_a_full_stop(default_context):
    attack = Action("mantella_npc_offended", "Attack", "Attack", "", "If an NPC says something highly offensive, begin your response with '{key}:'",
                    False, False, True, True, True)
    barter = Action("mantella_npc_barter", "Barter", "Barter", "", "To show the player what you have for sale, begin your response with '{key}:'.",
                    False, False, True, True, True)
    text = default_context._context__get_action_texts([attack, barter])
    assert text == "If an NPC says something highly offensive, begin your response with 'Attack:'. To show the player what you have for sale, begin your response with 'Barter:'. "


# Which switches each kind of conversation uses

def type_config(director: bool) -> MagicMock:
    cfg = MagicMock()
    cfg.multi_conversation_director_mode = director
    cfg.game.base_game = GameEnum.SKYRIM
    return cfg


@pytest.mark.parametrize("make, director, expected", [
    (pc_to_npc, False, ONE_ON_ONE),
    (multi_npc, False, GROUP),
    (multi_npc, True, DIRECTOR),
    (multi_npc_game_master, False, GROUP),
    (multi_npc_game_master, True, DIRECTOR),
    (shout, False, SHOUT),
    (shout, True, DIRECTOR),
    (npc_event, False, NPC_EVENT),
    (npc_event, True, NPC_EVENT), # The player watches, whatever Director Mode says
    (radiant, False, RADIANT)])
def test_each_conversation_type_uses_its_kinds_switches(make, director: bool, expected: str):
    assert make(type_config(director)).action_kind() == expected


def test_an_npc_who_walked_up_to_the_player_uses_the_one_on_one_switches(loaded: list[Action]):
    cfg = type_config(False)
    cfg.all_actions = loaded
    cfg.actions_one_on_one = ["Barter"]
    context = MagicMock()
    context.npc_initiative = True
    pc_to_npc(cfg).generate_prompt(context)
    assert names(context.generate_system_message.call_args.args[1]) == ["Barter"]
    assert context.generate_system_message.call_args.args[0] is cfg.npc_initiative_prompt


@pytest.mark.parametrize("has_more_npcs", [False, True])
def test_a_shout_uses_the_shout_switches_however_many_npcs_are_in_it(loaded: list[Action], has_more_npcs: bool):
    cfg = type_config(False)
    cfg.all_actions = loaded
    cfg.actions_one_on_one = ["Follow"]
    cfg.actions_group = ["Follow"]
    cfg.actions_shout = ["MoveTo"]
    context = MagicMock()
    context.npcs_in_conversation.contains_multiple_npcs.return_value = has_more_npcs
    assert names(shout(cfg).npc_actions(context)) == ["MoveTo"]


# Carrying over the old Actions list (Other tab)

def test_someone_who_had_follow_on_keeps_it_in_one_on_one_group_and_shout(loaded: list[Action]):
    old = ["Follow", "Inventory"]
    for kind in (ONE_ON_ONE, GROUP, SHOUT):
        ticks = carried_over_ticks(loaded, kind, old)
        assert "Follow" in ticks and "Inventory" in ticks, kind
        assert "Wait" not in ticks and "Unfollow" not in ticks, kind # New to them: off, as in the original
        assert "Attack" not in ticks and "StandDown" not in ticks, kind # Offended was left out: stays off
        assert "Barter" in ticks and "Flee" in ticks, kind # New to them: on, as in the original
    for kind in (NPC_EVENT, DIRECTOR, RADIANT):
        assert "Follow" not in carried_over_ticks(loaded, kind, old), kind


def test_offended_and_forgiven_become_attack_and_stand_down(loaded: list[Action]):
    for kind in ACTION_KINDS:
        ticks = carried_over_ticks(loaded, kind, ["Offended", "Forgiven"])
        assert "Attack" in ticks and "StandDown" in ticks, kind


def test_another_mods_action_stays_as_it_was():
    sex = Action("mantella_npc_sex", "Sex", "Sex", "", "...", False, False, True, False, False) # Actions For Mantella's, one-on-one only
    assert "Sex" in carried_over_ticks([sex], ONE_ON_ONE, ["Sex"])
    assert "Sex" not in carried_over_ticks([sex], ONE_ON_ONE, ["Follow"])
    assert "Sex" not in carried_over_ticks([sex], GROUP, ["Sex"])


def write_config(folder, text: str):
    with open(os.path.join(folder, "config.ini"), "w", encoding="utf-8") as f:
        f.write(text)


def read_config(folder) -> str:
    with open(os.path.join(folder, "config.ini"), encoding="utf-8") as f:
        return f.read()


def test_the_old_list_is_carried_over_once_with_the_old_keywords_changed(tmp_path):
    write_config(tmp_path, "[Other]\nactive_actions = Follow, Offended\n[Language]\noffended_npc_response = Offended\nforgiven_npc_response = Pardon\n")
    config = ConfigLoader(str(tmp_path))
    assert "Follow" in config.actions_one_on_one and "Follow" in config.actions_shout and "Follow" not in config.actions_director
    assert "Attack" in config.actions_group and "Inventory" not in config.actions_group
    keywords = {a.name: a.keyword for a in config.all_actions}
    assert keywords["Attack"] == "Attack" # Was the old default
    assert keywords["StandDown"] == "Pardon" # Their own word stays
    text = read_config(tmp_path)
    assert "active_actions" not in text and "actions_one_on_one = " in text

    # Changed afterwards, it stays as changed: the old list isn't carried over again
    write_config(tmp_path, text.replace("actions_one_on_one = ", "actions_one_on_one = Wait, "))
    assert "Wait" in ConfigLoader(str(tmp_path)).actions_one_on_one


def test_a_fresh_install_starts_from_the_original_defaults(tmp_path):
    config = ConfigLoader(str(tmp_path))
    original = [a for a in config.all_actions if a.name in ORIGINAL_ACTIONS]
    for kind in ACTION_KINDS:
        assert sorted(n for n in getattr(config, actions_key(kind)) if n in ORIGINAL_ACTIONS) == sorted(default_ticks(original, kind)), kind


def test_a_fresh_install_has_advanced_actions_off_and_a_separate_model_that_can_pick_actions(tmp_path):
    config = ConfigLoader(str(tmp_path))
    assert config.advanced_actions_enabled is False
    assert config.advanced_actions_own_model is False
    # The other tasks' default model can't pick actions: with it, turning on the separate model would make no action happen
    assert config.advanced_actions_llm == ADVANCED_ACTIONS_DEFAULT_MODEL

def test_an_action_whose_file_is_gone_is_dropped_without_failing_the_settings(tmp_path):
    write_config(tmp_path, "[Conversation]\nactions_one_on_one = Wait, Juggle\n")
    config = ConfigLoader(str(tmp_path))
    assert config.actions_one_on_one == ["Wait"]
    assert not [f for f in os.listdir(tmp_path) if f.startswith("config_backup")]


@pytest.mark.parametrize("identifier, key", [
    ("mantella_npc_follow", "follow_npc_response"),
    ("mantella_npc_offended", "offended_npc_response"),
    ("mantella_npc_teleport", "teleport_npc_response"),
    ("mantella_end_conversation", "end_conversation_npc_response")]) # Was d_conversation_npc_response: the start was taken off letter by letter
def test_each_action_has_its_own_keyword_setting(identifier: str, key: str):
    action = Action(identifier, "", "", "", "", False, False, True, True, True)
    assert LanguageDefinitions.action_keyword_key(action) == key


# What the NPC says

def parse(text: str, allowed: list[Action], every: list[Action]) -> SentenceContent:
    npc = MagicMock()
    npc.name = "Lydia"
    content = SentenceContent(npc, text, SentenceTypeEnum.SPEECH)
    actions_parser(allowed, every).modify_sentence_content(content, None, sentence_generation_settings(npc))
    return content


def test_an_action_the_npc_can_use_happens_and_its_name_isnt_said(loaded: list[Action]):
    wait = [a for a in loaded if a.name == "Wait"]
    content = parse("Wait: As you wish.", wait, loaded)
    assert content.text == "As you wish." and content.actions == [{"identifier": "mantella_npc_wait"}]


def test_an_action_switched_off_here_doesnt_happen_and_its_name_isnt_said(loaded: list[Action]):
    wait = [a for a in loaded if a.name == "Wait"]
    content = parse("Brawl: Come on, then!", wait, loaded)
    assert content.text == "Come on, then!" and content.actions == []


def test_an_action_named_twice_in_a_line_happens_once(loaded: list[Action]):
    wait = [a for a in loaded if a.name == "Wait"]
    content = parse("Wait: Fine. Wait: I said fine.", wait, loaded)
    assert content.actions == [{"identifier": "mantella_npc_wait"}] and "Wait:" not in content.text


def test_a_made_up_action_does_nothing(loaded: list[Action]):
    content = parse("Dance: Look at me go!", loaded, loaded)
    assert content.actions == []


# The default prompts list the actions in every kind of conversation

@pytest.mark.parametrize("get_prompt", [PromptDefinitions.get_skyrim_prompt_config_value, PromptDefinitions.get_skyrim_multi_npc_prompt_config_value,
                                        PromptDefinitions.get_skyrim_multi_npc_director_prompt_config_value,
                                        PromptDefinitions.get_skyrim_npc_initiative_prompt_config_value,
                                        PromptDefinitions.get_skyrim_radiant_prompt_config_value])
def test_each_default_prompt_has_the_actions(get_prompt):
    assert "{actions}" in get_prompt().default_value


@pytest.mark.parametrize("conditions", [set(), {"director"}, {"shout"}, {"shout", "director"}, {"npc_event"}])
def test_the_game_master_npc_prompt_has_the_actions_once_in_every_situation(conditions: set[str]):
    text = PromptDefinitions.apply_prompt_conditions("skyrim_game_master_npc_prompt",
                                                     PromptDefinitions.get_skyrim_game_master_npc_prompt_config_value().default_value, conditions)
    assert text.count("{actions}") == 1, conditions


# Advanced Actions: reading the model's picks

def test_share_conversation_remembers_who_to_share_it_with_once_it_is_sent(loaded_all: list[Action]):
    from src.actions.function_manager import FunctionManager
    from src.characters_manager import Characters
    import json
    speaker = MagicMock(is_player_character=False)
    speaker.name = "Lydia"
    characters = Characters()
    characters.add_or_update_character(speaker)
    game = MagicMock()
    game.resolve_npc_refid_by_name.return_value = "01A66E"
    share = [a for a in loaded_all if a.name == "ShareConversation"]
    call = {"id": "1", "type": "function", "function": {"name": "ShareConversation", "arguments": json.dumps({"source": "Someone", "recipient": "Hulda"})}}
    parsed = FunctionManager.parse_tool_calls([call], speaker, characters, game, share, True)
    assert parsed[0]["arguments"]["source"] == "Lydia" # Only the speaker shares
    assert characters.get_pending_shares() == [] # Not when read: an interrupted reply never sends it
    for _ in range(2):
        FunctionManager.record_shares(parsed, characters) # Sent to the game
    assert characters.get_pending_shares() == [("Lydia", "Hulda", "01A66E")]
    assert "already" in FunctionManager.parse_tool_calls([call], speaker, characters, game, share, True)[0]["arguments"]["debug_message"]
