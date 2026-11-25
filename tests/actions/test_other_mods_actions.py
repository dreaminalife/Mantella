"""Other mods' actions written for Advanced Actions (e.g. Actions For Mantella - Expanded Sex Actions): their game scripts only listen for
actions sent with details (who does it, who it is with), so those details reach the game both when the AI model picks the action and when an
NPC starts its line with its name. The original Actions For Mantella's files, without details, still go as before"""
import json
import logging
from unittest.mock import MagicMock

import pytest

from src.actions import function_manager
from src.actions.function_manager import FunctionManager
from src.characters_manager import Characters
from src.conversation.action import Action
from src.llm.output.actions_parser import actions_parser
from src.llm.output.output_parser import sentence_generation_settings
from src.llm.sentence_content import SentenceContent, SentenceTypeEnum

SEX_RULES = ("This tool executes the sex animation engine. Fire this tool ONLY IF the player explicitly says sex is starting.\n\n"
             "If the player explicitly initiates the start of sex, begin your response with '{key}:'.")
# As in Expanded Sex Actions, trimmed
SEX_WITH_PLAYER = {"identifier": "mantella_npc_sex", "name": "SexWithPlayer", "description": "Initiate a sex scene with the player.",
                   "key": "SexWithPlayer", "prompt": SEX_RULES, "one-on-one": True, "multi-npc": True, "radiant": False,
                   "parameters": {"source": {"type": "array", "description": "The NPC initiating the sex scene.", "items": {"type": "string"},
                                             "scope": "conversation", "maxItems": 1},
                                  "target": {"type": "string", "description": "The player character receiving the action."}},
                   "required": ["source", "target"]}
SEX_WITH_NPC = {"identifier": "mantella_npc_sex_npc", "name": "SexWithNPC", "description": "Initiate a sex scene with another NPC.",
                "key": "SexWithNPC", "prompt": "If the NPCs start having sex, begin your response with '{key}:'.", "one-on-one": False,
                "multi-npc": True, "radiant": True,
                "parameters": {"source": {"type": "array", "description": "The NPC initiating the sex scene.", "items": {"type": "string"},
                                          "scope": "conversation", "maxItems": 1},
                               "target": {"type": "string", "description": "The target NPC receiving the action.", "scope": "conversation"}},
                "required": ["source", "target"]}
GROUP_SEX = {"identifier": "mantella_npc_sex_group", "name": "GroupSexWithPlayer", "description": "Initiate a group sex scene (2-4 NPCs).",
             "key": "GroupSexWithPlayer", "prompt": "If group sex starts, begin your response with '{key}:'.", "one-on-one": False,
             "multi-npc": True, "radiant": False,
             "parameters": {"source": {"type": "array", "description": "The NPCs taking part (2-4 NPCs).", "items": {"type": "string"},
                                       "scope": "conversation", "minItems": 2, "maxItems": 4}},
             "required": ["source"]}
# As in the original Actions For Mantella: no details
OLD_SEX = {"identifier": "mantella_npc_sex_old", "name": "Sex", "description": "The keyword used by the NPC when they are willing have sex.",
           "key": "Sex", "prompt": "If the player asks you to have sex with them, and you are thoroughly convinced to do so, begin your response with '{key}:'.",
           "one-on-one": True, "multi-npc": False, "radiant": False}
# One of the original Mantella's actions with details: still sent without them when an NPC starts its line with it
ATTACK = {"identifier": "mantella_npc_offended", "name": "Attack", "description": "Attack someone.", "key": "Attack",
          "prompt": "If you are offended, begin your response with '{key}:'.", "one-on-one": True, "multi-npc": True, "radiant": True,
          "parameters": {"target": {"type": "string", "description": "Who to attack.", "scope": "conversation_w_player"}}, "required": ["target"]}


@pytest.fixture(autouse=True)
def mod_actions(tmp_path):
    """Only these actions are loaded, and the ones loaded before come back afterwards"""
    before = dict(FunctionManager._actions)
    for action in (SEX_WITH_PLAYER, SEX_WITH_NPC, GROUP_SEX, OLD_SEX, ATTACK):
        (tmp_path / f"{action['identifier']}.json").write_text(json.dumps(action), encoding="utf-8")
    FunctionManager.load_all_actions(actions_dir=str(tmp_path))
    yield
    FunctionManager._actions.clear()
    FunctionManager._actions.update(before)


def person(name: str, is_player: bool = False) -> MagicMock:
    character = MagicMock(is_player_character=is_player)
    character.name = name
    return character


def conversation(*npcs: str, with_player: bool = True) -> Characters:
    characters = Characters()
    if with_player:
        characters.add_or_update_character(person("Yuji", is_player=True))
    for name in npcs:
        characters.add_or_update_character(person(name))
    return characters


def actions(*names: str) -> list[Action]:
    return [a for a in FunctionManager.get_all_actions() if a.name in names]


def tool(name: str, characters: Characters, include_player: bool = True, several_speakers: bool = False) -> dict | None:
    tools = FunctionManager.generate_tools(actions(name), characters, None, "Skyrim", include_player, several_speakers=several_speakers)
    return tools[0]['function'] if tools else None


def call(name: str, **arguments) -> dict:
    return {"id": "1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


# Two mods adding the same action

def test_two_files_for_the_same_action_are_named_in_the_log(tmp_path, caplog):
    """Actions For Mantella and its Expanded Sex Actions both add Sex: only one file is used, and only one mod's game script runs"""
    caplog.set_level(logging.WARNING, logger=function_manager.log.name)
    folder = tmp_path / "both_mods"
    folder.mkdir()
    (folder / "Sex.json").write_text(json.dumps({**OLD_SEX, "identifier": "mantella_npc_sex"}), encoding="utf-8")
    (folder / "mantella_npc_sex.json").write_text(json.dumps(SEX_WITH_PLAYER), encoding="utf-8")
    FunctionManager.load_all_actions(actions_dir=str(folder))
    [warning] = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    used = FunctionManager._actions["mantella_npc_sex"]["_file"]
    assert "Sex.json" in warning and "mantella_npc_sex.json" in warning and f"'{used}' is used" in warning
    assert "turn off one of the two mods" in warning


def test_one_file_per_action_writes_no_warning(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger=function_manager.log.name)
    FunctionManager.load_all_actions(actions_dir=str(tmp_path)) # The five actions of mod_actions, each in its own file
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


# Advanced Actions: the tools

def test_the_model_reads_the_rules_another_mods_file_gives_the_npc():
    description = tool("SexWithPlayer", conversation("Lydia"))['description']
    assert description.startswith("Initiate a sex scene with the player.")
    assert "ONLY IF the player explicitly says sex is starting" in description
    assert "If the player explicitly initiates the start of sex, use this tool." in description and "{key}" not in description


def test_an_original_action_keeps_its_own_description():
    assert tool("Attack", conversation("Lydia"))['description'] == "Attack someone."


def test_a_detail_about_the_player_gives_the_players_name():
    assert "The player is Yuji." in tool("SexWithPlayer", conversation("Lydia"))['parameters']['properties']['target']['description']


@pytest.mark.parametrize("include_player, with_player", [(False, True), (True, False)])
def test_an_action_that_needs_the_player_isnt_offered_without_the_player(include_player: bool, with_player: bool):
    assert tool("SexWithPlayer", conversation("Lydia", with_player=with_player), include_player=include_player) is None


@pytest.mark.parametrize("several_speakers", [False, True])
def test_the_model_names_everyone_taking_part_in_an_action_done_together(several_speakers: bool):
    source = tool("GroupSexWithPlayer", conversation("Lydia", "Ysolda", "Hulda"), several_speakers=several_speakers)['parameters']['properties']['source']
    assert source['description'].startswith("The NPCs taking part") and "Ysolda" in source['description']


# Advanced Actions: reading the model's picks

@pytest.mark.parametrize("named", ["player", "Player", "you", "Yuji"])
def test_the_player_is_sent_by_name(named: str):
    characters = conversation("Lydia")
    picked = FunctionManager.parse_tool_calls([call("SexWithPlayer", source=["Hulda"], target=named)], characters.get_character_by_name("Lydia"),
                                              characters, None, actions("SexWithPlayer"), True)
    assert picked == [{"identifier": "mantella_npc_sex", "arguments": {"source": ["Lydia"], "target": "Yuji"}}] # The speaker does it


def test_everyone_the_model_names_takes_part_with_the_speaker():
    characters = conversation("Lydia", "Ysolda", "Hulda")
    picked = FunctionManager.parse_tool_calls([call("GroupSexWithPlayer", source=["ysolda", "Hulda"])], characters.get_character_by_name("Lydia"),
                                              characters, None, actions("GroupSexWithPlayer"), True)
    assert picked[0]["arguments"]["source"] == ["Lydia", "Ysolda", "Hulda"]


def test_an_action_done_together_takes_at_most_as_many_people_as_its_file_says():
    characters = conversation("Lydia", "Ysolda", "Hulda", "Mikael", "Carlotta")
    picked = FunctionManager.parse_tool_calls([call("GroupSexWithPlayer", source=["Ysolda", "Hulda", "Mikael", "Carlotta"])],
                                              characters.get_character_by_name("Lydia"), characters, None, actions("GroupSexWithPlayer"), True)
    assert picked[0]["arguments"]["source"] == ["Lydia", "Ysolda", "Hulda", "Mikael"]


def test_an_action_done_together_with_too_few_people_does_nothing():
    characters = conversation("Lydia", "Ysolda")
    picked = FunctionManager.parse_tool_calls([call("GroupSexWithPlayer", source=["Nobody"])], characters.get_character_by_name("Lydia"),
                                              characters, None, actions("GroupSexWithPlayer"), True)
    assert picked == []


# An NPC starting its line with the action's name

def say(text: str, characters: Characters, speaker: str = "Lydia", *names: str) -> SentenceContent:
    allowed = actions(*names) if names else FunctionManager.get_all_actions()
    content = SentenceContent(characters.get_character_by_name(speaker), text, SentenceTypeEnum.SPEECH)
    actions_parser(allowed, FunctionManager.get_all_actions(), characters).modify_sentence_content(content, None,
                                                                                                   sentence_generation_settings(content.speaker))
    return content


def test_the_speaker_starts_a_scene_with_the_player():
    content = say("SexWithPlayer: Come here.", conversation("Lydia"))
    assert content.text == "Come here."
    assert content.actions == [{"identifier": "mantella_npc_sex", "arguments": {"source": ["Lydia"], "target": "Yuji"}}]


def test_the_only_other_npc_is_who_its_with():
    content = say("SexWithNPC: Ysolda, come here.", conversation("Lydia", "Ysolda"))
    assert content.actions == [{"identifier": "mantella_npc_sex_npc", "arguments": {"source": ["Lydia"], "target": "Ysolda"}}]


def test_with_several_other_npcs_who_its_with_isnt_clear_so_it_goes_without_details():
    content = say("SexWithNPC: Come here.", conversation("Lydia", "Ysolda", "Hulda"))
    assert content.actions == [{"identifier": "mantella_npc_sex_npc"}]


def test_everyone_in_the_conversation_takes_part_in_an_action_done_together():
    content = say("GroupSexWithPlayer: Everyone, come here.", conversation("Ysolda", "Lydia", "Hulda"))
    assert content.actions == [{"identifier": "mantella_npc_sex_group", "arguments": {"source": ["Lydia", "Ysolda", "Hulda"]}}]


def test_a_name_inside_a_longer_name_is_not_used():
    content = say("GroupSexWithPlayer: Everyone, come here.", conversation("Lydia", "Ysolda"))
    assert [a["identifier"] for a in content.actions] == ["mantella_npc_sex_group"] and content.text == "Everyone, come here."


def test_an_action_done_together_needs_enough_people():
    content = say("GroupSexWithPlayer: Come here.", conversation("Lydia"))
    assert content.actions == [{"identifier": "mantella_npc_sex_group"}]


@pytest.mark.parametrize("text, identifier", [("Sex: Fine.", "mantella_npc_sex_old"), ("Attack: How dare you!", "mantella_npc_offended")])
def test_the_original_actions_for_mantella_and_the_original_mantellas_actions_go_as_before(text: str, identifier: str):
    assert say(text, conversation("Lydia")).actions == [{"identifier": identifier}]
