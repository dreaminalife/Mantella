"""What the LLM receives, for every situation of the prompts that are merged with conditional blocks
(Game Master, Game Master NPC, Event Generator, Game Master Decision Instructions / Player Criteria, Decision Nearby Criteria).

The snapshot files in tests/prompt_snapshots were recorded from the code before the prompts were merged. Each test only calls
public functions, so it runs unchanged after the merge: if it passes, every situation still sends exactly the same text.
Set the environment variable UPDATE_PROMPT_SNAPSHOTS=1 to record the files again"""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.character_manager import Character
from src.config.config_loader import ConfigLoader
from src.conversation.bystanders import EventPerson, ShoutState
from src.conversation.context import context as Context
from src.conversation.conversation_type import multi_npc_game_master, npc_event, shout
from src.conversation.game_master import GameMaster, resolve_decision_prompts
from src.conversation.npc_event import NpcEvent, generate_conversation_event
from src.games.equipment import Equipment
from src.llm.messages import SystemMessage, UserMessage
from src.config.definitions.conversation_definitions import MAX_NPC_TURNS_KINDS, max_npc_turns_identifier

SNAPSHOT_FOLDER = Path(__file__).parent / "prompt_snapshots"
UPDATE = os.environ.get("UPDATE_PROMPT_SNAPSHOTS") == "1"


def check_snapshot(name: str, text: str):
    path = SNAPSHOT_FOLDER / f"{name}.txt"
    if UPDATE:
        SNAPSHOT_FOLDER.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    assert path.exists(), f"No snapshot {path.name}. Record it with UPDATE_PROMPT_SNAPSHOTS=1"
    assert text == path.read_text(encoding="utf-8"), f"The text sent for '{name}' changed"


def character(name: str, ref_id: str, is_player: bool = False) -> Character:
    return Character(base_id=ref_id, ref_id=ref_id, name=name, gender=1, race="Nord", is_player_character=is_player,
                     bio=f"{name} is a person of Whiterun.", is_in_combat=False, is_enemy=False, relationship_rank=0,
                     is_generic_npc=False, ingame_voice_model="FemaleEvenToned", tts_voice_model="FemaleEvenToned",
                     csv_in_game_voice_model="FemaleEvenToned", advanced_voice_model="FemaleEvenToned", voice_accent="en",
                     equipment=Equipment({}), custom_character_values={})


PLAYER = character("Dragonborn", "000014", is_player=True)
YSOLDA = EventPerson("Ysolda", 103, "000103")
BELETHOR = EventPerson("Belethor", 104, "000104")
PLAYER_PERSON = EventPerson("Dragonborn", 20, "000014")


def lorebook_echo(search_texts: list[str]) -> str:
    """Stands in for the lorebook: shows which texts were searched, so the snapshot records what the lorebook scan read"""
    return "LOREBOOK SEARCHED: " + " | ".join(sorted(search_texts))


def build_context(default_config: ConfigLoader, llm_client, default_rememberer, english_language_info, npcs: list[str],
                  with_player: bool = True) -> Context:
    context = Context("1", default_config, llm_client, default_rememberer, english_language_info)
    context._context__lorebook_manager = SimpleNamespace(render_lorebook_entries=lorebook_echo)
    context.location = "Whiterun"
    if with_player:
        context.npcs_in_conversation.add_or_update_character(PLAYER)
    for number, name in enumerate(npcs):
        context.npcs_in_conversation.add_or_update_character(character(name, f"00020{number}"))
    return context


def shout_state() -> ShoutState:
    state = ShoutState(nearby=[YSOLDA, BELETHOR])
    state.characters = {YSOLDA.refid: character("Ysolda", YSOLDA.ref_id), BELETHOR.refid: character("Belethor", BELETHOR.ref_id)}
    return state


def watched_event(joined: bool = False) -> NpcEvent:
    scene = NpcEvent(id=7, text="Lydia and Faendal argue about a stolen bow.", player=PLAYER_PERSON, player_noticeable=True, npc_lines=10)
    scene.player_joined = joined
    return scene


@pytest.fixture
def make_context(default_config, llm_client, default_rememberer, english_language_info):
    default_config.multi_conversation_director_mode = False
    def make(npcs: list[str], with_player: bool = True) -> Context:
        return build_context(default_config, llm_client, default_rememberer, english_language_info, npcs, with_player)
    return make


def speaker(context: Context, name: str) -> Character:
    return context.npcs_in_conversation.get_character_by_name(name)


# Game Master Prompt and Game Master NPC Prompt

def test_group(make_context, default_config):
    context = make_context(["Lydia", "Faendal"])
    conversation_type = multi_npc_game_master(default_config)
    check_snapshot("game_master_group", conversation_type.generate_game_master_prompt(context))
    check_snapshot("npc_group", conversation_type.generate_npc_prompt(context, speaker(context, "Lydia")))


def test_director(make_context, default_config):
    default_config.multi_conversation_director_mode = True
    context = make_context(["Lydia", "Faendal"])
    conversation_type = multi_npc_game_master(default_config)
    check_snapshot("game_master_director", conversation_type.generate_game_master_prompt(context))
    check_snapshot("npc_director", conversation_type.generate_npc_prompt(context, speaker(context, "Lydia")))


def test_shout_with_a_group(make_context, default_config):
    context = make_context(["Lydia", "Faendal"])
    context.shout = shout_state()
    conversation_type = shout(default_config)
    check_snapshot("game_master_shout", conversation_type.generate_game_master_prompt(context))
    check_snapshot("npc_shout_group", conversation_type.generate_npc_prompt(context, speaker(context, "Lydia")))


def test_shout_with_one_npc(make_context, default_config):
    context = make_context(["Lydia"])
    context.shout = shout_state()
    conversation_type = shout(default_config)
    check_snapshot("game_master_shout_one_npc", conversation_type.generate_game_master_prompt(context))
    check_snapshot("npc_shout_one_npc", conversation_type.generate_npc_prompt(context, speaker(context, "Lydia")))


def test_say_to_room(make_context, default_config):
    context = make_context([])
    context.shout = shout_state()
    check_snapshot("game_master_say_to_room", shout(default_config).generate_game_master_prompt(context))


def test_watching_an_npc_event(make_context, default_config):
    context = make_context(["Lydia", "Faendal"], with_player=False)
    context.npc_event = watched_event()
    context.shout = shout_state()
    conversation_type = npc_event(default_config)
    check_snapshot("game_master_npc_event", conversation_type.generate_game_master_prompt(context))
    check_snapshot("npc_npc_event", conversation_type.generate_npc_prompt(context, speaker(context, "Lydia")))


def test_shout_after_joining_an_npc_event(make_context, default_config):
    context = make_context(["Lydia", "Faendal"])
    context.npc_event = watched_event(joined=True)
    context.shout = shout_state()
    conversation_type = shout(default_config)
    check_snapshot("game_master_shout_after_npc_event", conversation_type.generate_game_master_prompt(context))
    check_snapshot("npc_shout_after_npc_event", conversation_type.generate_npc_prompt(context, speaker(context, "Lydia")))


# Event Generator Prompt

@pytest.mark.parametrize("kind", ["conversation", "nearby", "random"])
def test_event_generator(kind, default_config):
    client = MagicMock()
    client.request_call.return_value = '{"event": "A cart loses a wheel."}'
    generate_conversation_event(default_config, client, kind, ["Lydia", "Faendal"], "Dragonborn", "A tall Nord.", "Whiterun", 15, "It is raining.",
                                "Lydia is a housecarl. Faendal is a hunter.", "something funny", ["Lydia: Hello.", "Faendal: Hi."])
    request = client.request_call.call_args.args[0]
    check_snapshot(f"event_generator_{kind}", request.get_messages_of_type(SystemMessage)[0].text + "\n\n=== REQUEST ===\n" + request.get_last_message().text)


# Game Master Decision Instructions and Player Criteria

@pytest.mark.parametrize("after_event", [False, True])
def test_decision_prompts(after_event, default_config):
    prompts = resolve_decision_prompts(default_config, after_event=after_event)
    check_snapshot(f"decision_prompts_{'after_event' if after_event else 'normal'}", json.dumps(prompts, indent=2, ensure_ascii=False))


# Decision Nearby Criteria: what the decision model is asked about the people nearby

def decision_config(real: ConfigLoader) -> MagicMock:
    cfg = MagicMock()
    cfg.prompt_profile_manager.resolve_prompt.side_effect = lambda _prompt_type, text: text
    cfg.game_master_use_decision_model = True
    cfg.game_master_history_message_count = 0
    cfg.game_master_include_turn_thoughts = False
    for each_kind in MAX_NPC_TURNS_KINDS: # Each kind of conversation has its own
        setattr(cfg, max_npc_turns_identifier(each_kind), 3)
    cfg.multi_npc_game_master_mode = True
    cfg.llm_response_timeout = 10
    cfg.game_master_addressed_threshold = 0.5
    cfg.npc_event_player_notice_delay = 3
    cfg.npc_event_max_lines_without_player = 30
    cfg.npc_event_wind_down_lines = 0
    cfg.multi_conversation_director_mode = False
    for attribute in ("game_master_decision_instructions", "game_master_decision_npc_criteria", "game_master_decision_player_criteria",
                      "game_master_decision_event_instructions", "game_master_decision_event_player_criteria",
                      "shout_decision_nearby_criteria", "npc_event_decision_nearby_criteria", "shout_decision_nobody_criteria",
                      "shout_decision_addressed_criteria", "shout_decision_not_addressed_criteria",
                      "shout_decision_event_part_criteria", "shout_decision_event_not_part_criteria", "shout_group_answer_note"):
        setattr(cfg, attribute, getattr(real, attribute, None)) # Absorbed into a merged prompt: no longer on the config
    return cfg


def decision_client() -> MagicMock:
    client = MagicMock()
    client.service_name = "OpenRouter"
    client.model_name = "decider"
    client.api_key = "key"
    client.get_count_tokens.side_effect = lambda text: len(text) // 4
    client.token_limit = 100000
    return client


def npc(name: str, ref_id: str):
    return SimpleNamespace(name=name, ref_id=ref_id, is_player_character=False)


def decision_game_master(cfg, npcs: list, event: NpcEvent | None, player) -> GameMaster:
    context = MagicMock()
    context.config = cfg
    context.npc_event = event
    context.shout = ShoutState(nearby=[YSOLDA, BELETHOR])
    context.get_characters_excluding_player.return_value.get_all_characters.return_value = npcs
    context.npcs_in_conversation.get_player_character.return_value = player
    context.get_character_names_as_text.return_value = ", ".join(n.name for n in npcs)
    conversation_type = MagicMock(spec=npc_event if event and not event.player_joined else shout)
    conversation_type.generate_game_master_prompt.return_value = "You are the game master."
    conversation_type.is_director_mode.return_value = False
    return GameMaster(context, conversation_type, decision_client())


def decision_request(gm: GameMaster, history: list) -> str:
    response = MagicMock(status_code=200, text="")
    response.json.return_value = {"answers": {"next_speaker": {"choice": "Nobody", "probabilities": {}}}}
    with patch("src.conversation.game_master.post_decision_with_retry", return_value=response) as post:
        gm.choose_next_speaker(MagicMock(), history, False, allow_nearby=True)
    return json.dumps(post.call_args.args[2], indent=2, ensure_ascii=False, sort_keys=True)


@pytest.mark.parametrize("situation", ["shout", "say_to_room", "npc_event"])
def test_nearby_criteria(situation, default_config):
    cfg = decision_config(default_config)
    player = SimpleNamespace(name="Dragonborn", ref_id="000014", is_player_character=True)
    line = [UserMessage(cfg, "Hello there, Ysolda!", "Dragonborn", False)]
    if situation == "shout":
        gm = decision_game_master(cfg, [npc("Lydia", "000200")], None, player)
    elif situation == "say_to_room":
        gm = decision_game_master(cfg, [], None, player)
    else:
        gm = decision_game_master(cfg, [npc("Lydia", "000200"), npc("Faendal", "000201")], watched_event(), None)
        line = [UserMessage(cfg, "Ysolda, tell him!", "Lydia", False)]
    check_snapshot(f"nearby_criteria_{situation}", decision_request(gm, line))
