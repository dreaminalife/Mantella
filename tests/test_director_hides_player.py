"""Director Mode: the player directs and is not part of the scene. Nothing Mantella adds tells the NPCs or the Game Master about the player:
no event about the player, no wheel event about them, no NPC Event noticing them, and memories say they were not there.
Outside Director Mode, all of it works as before. The prompt variables are left as they are: the prompts decide what to use"""
import copy
from pathlib import Path

from unittest.mock import MagicMock

import pytest

from src.character_manager import Character
from src.config.definitions.prompt_definitions import PromptDefinitions
from src.conversation.bystanders import EventPerson, ShoutState
from src.conversation.context import context as Context
from src.conversation.npc_event import NpcEvent
from src.http.communication_constants import communication_constants
from src.llm.message_thread import message_thread
from src.llm.messages import AssistantMessage, UserMessage, join_message
from src.remember.summaries import Summaries
from src.characters_manager import Characters
from tests.test_context_name_variables import _npc

FIXTURES = Path(__file__).parent / "prompt_merge_fixtures"
VISION = {communication_constants.KEY_CONTEXT_CUSTOMVALUES_VISION_HINTSNAMEARRAY: "[Lydia]",
          communication_constants.KEY_CONTEXT_CUSTOMVALUES_VISION_HINTSDISTANCEARRAY: "[100.0]"}


def context_with(default_context: Context, player: Character, directed: bool) -> Context:
    default_context.npcs_in_conversation.add_or_update_character(player)
    default_context.npcs_in_conversation.add_or_update_character(_npc("Lydia", "0A2C94"))
    default_context.hides_player = lambda: directed
    return default_context


@pytest.mark.parametrize("director, started_by_npc, expected", [(True, False, True), (False, False, False), (True, True, False)])
def test_the_conversation_tells_its_context_whether_the_player_directs(director, started_by_npc, expected):
    from tests.test_prompt_conditions_by_situation import config, conversation, conversation_type
    talk = conversation(config(director=director), npcs=2, started_by_npc=started_by_npc)
    talk._Conversation__conversation_type = conversation_type(talk)
    assert talk.is_directed() is expected
    assert talk.context.hides_player() is expected


@pytest.mark.parametrize("director", [True, False])
def test_watching_an_npc_event_hides_the_player_with_director_mode_on(director):
    """The scene isn't directed, as it runs on its own, but nothing about the player watching reaches it"""
    from tests.test_prompt_conditions_by_situation import config, conversation, conversation_type, scene
    talk = conversation(config(game_master_mode=False, director=director), npcs=2, has_player=False, shout_state=ShoutState(), event=scene())
    talk._Conversation__conversation_type = conversation_type(talk)
    assert not talk.is_directed()
    assert talk.context.hides_player() is director


# Mantella's own events

@pytest.mark.parametrize("directed", [True, False])
def test_the_players_combat_attack_and_relationship_events_are_only_made_when_not_directing(default_context, example_skyrim_player_character, directed):
    context = context_with(default_context, example_skyrim_player_character, directed)
    context.clear_context_ingame_events()
    lydia = copy.deepcopy(context.npcs_in_conversation.get_character_by_name("Lydia"))
    lydia.is_enemy = True
    lydia.relationship_rank = 3
    player = copy.deepcopy(example_skyrim_player_character)
    player.is_in_combat = True
    context.add_or_update_characters([player, lydia])
    events = context.get_context_ingame_events()
    if directed:
        assert events == []
    else:
        assert events[0] == "The player is now in combat!"
        assert events[1].startswith("Lydia is attacking Dragonborn.") and events[2].startswith("Dragonborn is now ")


def test_an_npcs_own_combat_is_still_an_event_while_directing(default_context, example_skyrim_player_character):
    context = context_with(default_context, example_skyrim_player_character, directed=True)
    context.clear_context_ingame_events()
    lydia = copy.deepcopy(context.npcs_in_conversation.get_character_by_name("Lydia"))
    lydia.is_in_combat = True
    context.add_or_update_characters([example_skyrim_player_character, lydia])
    assert context.get_context_ingame_events() == ["Lydia is now in combat!"]


@pytest.mark.parametrize("directed", [True, False])
def test_who_the_player_can_see_is_only_an_event_when_not_directing(default_context, example_skyrim_player_character, directed):
    context = context_with(default_context, example_skyrim_player_character, directed)
    context.update_context(None, None, None, "", VISION)
    seen = [event for event in context.get_context_ingame_events() if event.startswith("Characters currently in view")]
    assert seen == ([] if directed else ["Characters currently in view: Lydia (very close)"])


# Shout's limits and the NPC Events the player watches

def test_the_player_takes_no_place_in_the_conversation_while_directing():
    state = ShoutState(max_people=3)
    assert state.free_places(npc_count=2, has_player=True) == 0
    assert state.free_places(npc_count=2, has_player=False) == 1


@pytest.mark.parametrize("after_event, ignores_max_people, is_direction, expected", [
    (False, False, False, False),
    (True, False, False, False), # A Talk Event: within the limit
    (True, True, False, True), # A Nearby or Random Event
    (False, False, True, True), # A direction
])
def test_the_people_a_direction_is_about_join_past_most_people_like_an_event(after_event, ignores_max_people, is_direction, expected):
    state = ShoutState(max_people=1)
    state.event_ignores_max_people = ignores_max_people
    state.nearby = [EventPerson("Ysolda", 103, "000067")]
    assert state.is_past_limit_allowed(after_event, is_direction) is expected
    assert state.free_places(npc_count=1, has_player=False, after_event=after_event, is_direction=is_direction) == (1 if expected else 0)


@pytest.mark.parametrize("director", [True, False])
def test_the_people_in_an_npc_event_never_notice_the_player_with_director_mode_on(default_config, director):
    default_config.multi_conversation_director_mode = director
    default_config.npc_event_player_notice_delay = 0
    player = EventPerson("Dragonborn", 20, "000014")
    scene = NpcEvent(id=7, text="Lydia and Faendal argue.", player=player, player_noticeable=True)
    assert scene.noticed_player(default_config, ["Lydia", "Faendal"]) is (None if director else player)


# Prompts

@pytest.mark.parametrize("kind", ["talk_event", "nearby_event", "random_event"])
def test_a_wheel_event_while_directing_leaves_the_player_out(kind):
    prompt = PromptDefinitions.get_skyrim_conversation_event_generator_prompt_config_value().default_value
    directed = PromptDefinitions.apply_prompt_conditions("skyrim_conversation_event_generator_prompt", prompt, {kind, "director"})
    assert "is in a conversation" not in directed and "{player_description}" not in directed and "can happen to {player_name}" not in directed
    assert "- {player_name} is not around: leave {player_name} out of the event." in directed
    assert "{hint}" in directed


@pytest.mark.parametrize("conditions", [{"director"}, {"director", "shout"}])
def test_directed_npcs_react_to_what_is_in_brackets(conditions):
    """Directions are shown in brackets, so the NPC prompt never says bracketed text is only informational, nor that the player does things"""
    prompt = PromptDefinitions.get_skyrim_game_master_npc_prompt_config_value().default_value
    directed = PromptDefinitions.apply_prompt_conditions("skyrim_game_master_npc_prompt", prompt, conditions)
    assert "(A door slams shut). They are what is happening now: react to them" in directed
    assert "(The player picks up an item)" not in directed
    assert "(The player picks up an item)" in PromptDefinitions.apply_prompt_conditions("skyrim_game_master_npc_prompt", prompt, conditions - {"director"})


def summary_prompt(skyrim, cfg, llm_client, language: str, npc, monkeypatch, was_directed: bool) -> str:
    """The prompt the memory of a conversation is written with. The player only sent "." (Director Mode), or spoke"""
    monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
    client = MagicMock()
    client.request_call.return_value = "They talked."
    client.token_limit, client.get_count_tokens.return_value = 4096, 1
    thread = message_thread(cfg, None)
    thread.add_message(join_message(npc, cfg))
    for i in range(5):
        thread.add_message(UserMessage(cfg, "", "Dragonborn", False)) # "." only: no direction typed
        thread.add_message(AssistantMessage(cfg, f"Line {i}."))
    characters = Characters()
    characters.add_or_update_character(npc)
    Summaries(skyrim, cfg, llm_client, language, summary_client=client).save_conversation_state(thread, characters, "directed-memory", save_timestamp=1,
                                                                                                was_directed=was_directed)
    return client.request_call.call_args[0][0].get_openai_messages()[0]["content"]


@pytest.mark.parametrize("was_directed", [True, False])
def test_a_directed_conversation_is_remembered_without_the_player_even_with_only_continues(skyrim, default_config, llm_client, english_language_info,
                                                                                          example_skyrim_npc_character, monkeypatch, was_directed):
    prompt = summary_prompt(skyrim, default_config, llm_client, english_language_info["language"], example_skyrim_npc_character, monkeypatch, was_directed)
    assert ("Dragonborn was not there and took no part in it" in prompt) is was_directed
    assert ("the player (the user)" in prompt) is not was_directed


def test_the_memory_prompt_without_director_mode_is_unchanged():
    memory = PromptDefinitions.get_memory_prompt_config_value().default_value
    assert PromptDefinitions.apply_prompt_conditions("memory_prompt", memory, set()) == (FIXTURES / "memory_prompt.txt").read_text(encoding="utf-8")
