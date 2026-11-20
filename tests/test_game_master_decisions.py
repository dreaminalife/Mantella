import json
import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from src import utils
from src.conversation.conversation import Conversation
from src.conversation.conversation_type import multi_npc, multi_npc_game_master
from src.conversation.game_master import (DECISION_INSTRUCTIONS, DECISION_NPC_CRITERIA, DECISION_PLAYER_CRITERIA, DECISION_QUESTION,
                                          GameMaster, GameMasterTurns, ReplyQueue, add_reply_to_history, build_decision_request,
                                          format_decision_request, format_response_text, game_master_history_lines, history_text,
                                          parse_decision)
from src.llm.message_thread import message_thread
from src.llm.messages import AssistantMessage, UserMessage, join_message, leave_message
from src.llm.sentence import Sentence
from src.llm.sentence_content import SentenceContent, SentenceTypeEnum
from src.llm.sentence_queue import SentenceQueue
from src.prompt_profile_manager import PromptSectionError
from src.game_manager import GameStateManager
from src.llm.client_base import ClientBase
from src.config.definitions.conversation_definitions import MAX_NPC_TURNS_KINDS, max_npc_turns_identifier

API_KEY = "sk-or-secret"


def npc(name: str):
    character = MagicMock()
    character.name = name
    character.is_player_character = False
    return character


def config(use_decision_model: bool = True):
    cfg = MagicMock()
    cfg.game_master_use_decision_model = use_decision_model
    cfg.game_master_decision_instructions = "{if after_event}Who reacts first to what happened, {names} or {player_name}?{else}Who speaks next of {names}, or {player_name}?{end if}"
    cfg.game_master_decision_npc_criteria = "{name} was asked something by {player_name}."
    cfg.game_master_decision_player_criteria = "{if after_event}\nIt happened to {player_name} alone.\n{else}\n{player_name} was addressed.\n{end if}"
    cfg.prompt_profile_manager.resolve_prompt.side_effect = lambda _prompt_type, text: text
    # Asked about whoever an NPC's line could be spoken to, even one person: with the default talk settings they answer it next
    cfg.shout_decision_addressed_criteria = "{speaker} spoke to {name}."
    cfg.shout_decision_not_addressed_criteria = "{speaker} did not speak to {name}."
    cfg.game_master_addressed_threshold = 0.5
    for each_kind in MAX_NPC_TURNS_KINDS: # Each kind of conversation has its own
        setattr(cfg, max_npc_turns_identifier(each_kind), 3)
    cfg.multi_conversation_director_mode = False
    cfg.llm_response_timeout = 10
    cfg.game_master_history_message_count = 0
    cfg.game_master_include_turn_thoughts = False
    return cfg


def client(service: str = "OpenRouter"):
    c = MagicMock()
    c.service_name = service
    c.model_name = "typesafe/jev-1.13"
    c.api_key = API_KEY
    c.token_limit = 32000
    c.get_count_tokens = lambda text: len(text.split())
    return c


def game_master(cfg, npcs, ai_client):
    context = MagicMock()
    context.config = cfg
    context.npc_event = None
    context.get_characters_excluding_player.return_value.get_all_characters.return_value = npcs
    player = MagicMock()
    player.name = "Dragonborn"
    context.npcs_in_conversation.get_player_character.return_value = player
    context.get_character_names_as_text.return_value = "Lydia, Ulfric and Dragonborn"
    conversation_type = MagicMock()
    conversation_type.generate_game_master_prompt.return_value = "You are the game master."
    conversation_type.is_director_mode.return_value = False
    return GameMaster(context, conversation_type, ai_client)


def decision_response(choice: str, status: int = 200):
    response = MagicMock()
    response.status_code = status
    if status == 200:
        body = {"answers": {DECISION_QUESTION: {"type": "choice", "choice": choice, "confidence": 0.8,
                                                "probabilities": {choice: 0.9, "Ulfric": 0.1}}}}
    else:
        body = {"error": {"code": status, "message": "Invalid request parameters"}}
    response.json.return_value = body
    response.text = json.dumps(body)
    return response


def count_words(text: str) -> int:
    return len(text.split())


PROMPTS = {
    DECISION_INSTRUCTIONS: "Who speaks next of {names}, or {player_name}?",
    DECISION_NPC_CRITERIA: "{name} was asked something by {player_name}.",
    DECISION_PLAYER_CRITERIA: "{player_name} was addressed.",
}


# build_decision_request

def test_build_decision_request_fills_prompts_and_adds_player_when_allowed():
    body = build_decision_request(PROMPTS, "typesafe/jev-1.13", "Scene", ["Lydia: Hi."], ["Lydia", "Ulfric"], "Dragonborn", True, count_words, 32000)
    question = body["questions"][DECISION_QUESTION]
    assert body["model"] == "typesafe/jev-1.13"
    assert body["state"] == {"scene": "Scene", "conversation": ["Lydia: Hi."]}
    assert question["type"] == "choice"
    assert question["instructions"] == "Who speaks next of Lydia, Ulfric, or Dragonborn?"
    assert question["criteria"] == {
        "Lydia": "Lydia was asked something by Dragonborn.",
        "Ulfric": "Ulfric was asked something by Dragonborn.",
        "Dragonborn": "Dragonborn was addressed.",
    }


def test_build_decision_request_leaves_out_player_when_not_allowed():
    body = build_decision_request(PROMPTS, "m", "Scene", [], ["Lydia", "Ulfric"], "Dragonborn", False, count_words, 32000)
    assert list(body["questions"][DECISION_QUESTION]["criteria"]) == ["Lydia", "Ulfric"]


def test_build_decision_request_drops_oldest_lines_to_fit():
    lines = [f"Lydia: line {i} " + "word " * 20 for i in range(10)]
    full = build_decision_request(PROMPTS, "m", "Scene", lines, ["Lydia"], "Dragonborn", True, count_words, 100_000)
    assert full["state"]["conversation"] == lines

    trimmed = build_decision_request(PROMPTS, "m", "Scene", lines, ["Lydia"], "Dragonborn", True, count_words, 150)
    kept = trimmed["state"]["conversation"]
    assert 0 < len(kept) < len(lines)
    assert kept == lines[-len(kept):]


def test_build_decision_request_returns_none_when_scene_is_too_long():
    scene = "word " * 500
    assert build_decision_request(PROMPTS, "m", scene, ["Lydia: Hi."], ["Lydia"], "Dragonborn", True, count_words, 100) is None


def test_format_decision_request_is_readable():
    body = build_decision_request(PROMPTS, "typesafe/jev-1.13", "You are the game master.\nKeep it short.", ["Lydia: Hi.", "Dragonborn: Hello."],
                                  ["Lydia"], "Dragonborn", True, count_words, 32000)
    assert format_decision_request(body) == "\n".join([
        "Model: typesafe/jev-1.13",
        "",
        "===== Scene =====",
        "You are the game master.",
        "Keep it short.",
        "",
        "===== Conversation (2 lines) =====",
        "Lydia: Hi.",
        "Dragonborn: Hello.",
        "",
        f"===== Question: {DECISION_QUESTION} (choice) =====",
        "Who speaks next of Lydia, or Dragonborn?",
        "",
        "Criteria:",
        "  - Lydia: Lydia was asked something by Dragonborn.",
        "  - Dragonborn: Dragonborn was addressed.",
    ])


def test_format_response_text_indents_json_and_keeps_other_text():
    assert format_response_text('{"error": {"message": "Bad"}}') == '{\n  "error": {\n    "message": "Bad"\n  }\n}'
    assert format_response_text("Bad Gateway") == "Bad Gateway"


# GameMaster.choose_next_speaker with a decision model

@patch("src.conversation.game_master.requests.post")
def test_decision_model_returns_chosen_npc(post):
    lydia, ulfric = npc("Lydia"), npc("Ulfric")
    post.return_value = decision_response("Ulfric")
    cfg = config()
    history = [UserMessage(cfg, "What do you think, Ulfric?", "Lydia", False)]
    speaker = game_master(cfg, [lydia, ulfric], client()).choose_next_speaker(MagicMock(), history, True)

    assert speaker is ulfric
    body = post.call_args.kwargs["json"]
    assert body["state"]["conversation"] == ["Lydia: What do you think, Ulfric?"]
    assert "turns" not in body["state"] # The NPC turn limit is only enforced in code
    assert post.call_args.args[0] == "https://openrouter.ai/api/alpha/decisions"
    assert post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {API_KEY}"


@patch("src.conversation.game_master.requests.post")
def test_decision_model_asks_who_reacts_first_only_after_an_event(post):
    post.return_value = decision_response("Lydia")
    gm = game_master(config(), [npc("Lydia")], client())

    gm.choose_next_speaker(MagicMock(), [], True, after_event=True)
    question = post.call_args.kwargs["json"]["questions"][DECISION_QUESTION]
    assert question["instructions"] == "Who reacts first to what happened, Lydia or Dragonborn?"
    assert question["criteria"] == {"Lydia": "Lydia was asked something by Dragonborn.", "Dragonborn": "It happened to Dragonborn alone."}

    gm.choose_next_speaker(MagicMock(), [], True)
    question = post.call_args.kwargs["json"]["questions"][DECISION_QUESTION]
    assert question["instructions"] == "Who speaks next of Lydia, or Dragonborn?"
    assert question["criteria"]["Dragonborn"] == "Dragonborn was addressed."


@patch("src.conversation.game_master.requests.post")
def test_decision_model_uses_nanogpt_decisions_api(post):
    lydia = npc("Lydia")
    post.return_value = decision_response("Lydia")
    assert game_master(config(), [lydia], client("NanoGPT")).choose_next_speaker(MagicMock(), [], False) is lydia
    assert post.call_args.args[0] == "https://nano-gpt.com/api/v1/decisions"
    assert post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {API_KEY}"


@patch("src.conversation.game_master.requests.post")
def test_decision_model_player_choice_returns_none(post):
    post.return_value = decision_response("Dragonborn")
    assert game_master(config(), [npc("Lydia")], client()).choose_next_speaker(MagicMock(), [], True) is None


@patch("src.conversation.game_master.requests.post")
def test_decision_model_logs_full_request_without_api_key(post, caplog):
    post.return_value = decision_response("Lydia")
    with caplog.at_level(15): # The full request is only logged in debug mode
        game_master(config(), [npc("Lydia")], client()).choose_next_speaker(MagicMock(), [], False)

    logged = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Game Master decision request:")]
    assert len(logged) == 1
    assert logged[0].split("\n", 1)[1] == format_decision_request(post.call_args.kwargs["json"])
    assert API_KEY not in caplog.text
    assert "Lydia speaks next (confidence 0.80: Lydia 0.90, Ulfric 0.10)" in caplog.text
    assert ("Game Master decision request for Skyrim Game Master Prompt and Skyrim Game Master Decision Instructions: "
            "model='typesafe/jev-1.13', url='https://openrouter.ai/api/alpha/decisions'") in caplog.text


@patch("src.conversation.game_master.requests.post")
def test_decision_model_errors_return_none(post, caplog):
    gm = game_master(config(), [npc("Lydia")], client())

    post.return_value = decision_response("", status=400)
    assert gm.choose_next_speaker(MagicMock(), [], False) is None
    assert "Invalid request parameters" in caplog.text

    post.side_effect = requests.Timeout("timed out")
    assert gm.choose_next_speaker(MagicMock(), [], False) is None
    assert post.call_count == 2 # Neither a 4xx nor a read timeout is retried

    post.side_effect = None
    post.return_value = decision_response("Nazeem")
    assert gm.choose_next_speaker(MagicMock(), [], False) is None


@patch("src.conversation.game_master.requests.post")
def test_decision_model_retries_once_after_connection_reset(post):
    lydia = npc("Lydia")
    post.side_effect = [requests.ConnectionError("Connection aborted."), decision_response("Lydia")]
    assert game_master(config(), [lydia], client()).choose_next_speaker(MagicMock(), [], False) is lydia
    assert post.call_count == 2


@patch("src.conversation.game_master.requests.post")
def test_decision_model_retries_once_after_server_error(post):
    lydia = npc("Lydia")
    post.side_effect = [decision_response("", status=529), decision_response("Lydia")]
    assert game_master(config(), [lydia], client()).choose_next_speaker(MagicMock(), [], False) is lydia
    assert post.call_count == 2


@patch("src.conversation.game_master.requests.post")
def test_decision_model_gives_up_after_one_retry(post, caplog):
    post.side_effect = requests.ConnectionError("Connection aborted.")
    assert game_master(config(), [npc("Lydia")], client()).choose_next_speaker(MagicMock(), [], False) is None
    assert post.call_count == 2
    assert "Retrying once" in caplog.text
    assert "Game Master decision call failed: Connection aborted." in caplog.text


@patch("src.conversation.game_master.requests.post")
def test_decision_model_broken_prompt_profile_returns_none(post):
    cfg = config()
    cfg.prompt_profile_manager.resolve_prompt.side_effect = PromptSectionError("bio")
    assert game_master(cfg, [npc("Lydia")], client()).choose_next_speaker(MagicMock(), [], False) is None
    post.assert_not_called()


@patch("src.conversation.game_master.requests.post")
def test_decision_model_needs_openrouter_or_nanogpt(post):
    assert game_master(config(), [npc("Lydia")], client("OpenAI")).choose_next_speaker(MagicMock(), [], False) is None
    post.assert_not_called()


@patch("src.conversation.game_master.requests.post")
def test_chat_model_path_unchanged_when_toggle_off(post):
    lydia = npc("Lydia")
    ai_client = client()
    ai_client.request_call.return_value = '{"next": "Lydia", "reason": "asked"}'
    assert game_master(config(use_decision_model=False), [lydia], ai_client).choose_next_speaker(MagicMock(), [], False) is lydia
    ai_client.request_call.assert_called_once()
    post.assert_not_called()


def test_parse_decision_unchanged():
    lydia, ulfric = npc("Lydia"), npc("Ulfric Stormcloak")
    assert parse_decision('{"next": "Lydia", "reason": "asked"}', [lydia, ulfric], "Dragonborn", True) == (True, lydia, "asked")
    assert parse_decision('{"next": "ulfric"}', [lydia, ulfric], "Dragonborn", True) == (True, ulfric, "")
    assert parse_decision('{"next": "Player"}', [lydia], "Dragonborn", False) == (False, None, "")
    assert parse_decision('{"next": "Dragonborn"}', [lydia], "Dragonborn", True) == (True, None, "")
    assert parse_decision('{"next": ""}', [lydia], "Dragonborn", True) == (False, None, "")
    assert parse_decision("no json", [lydia], "Dragonborn", True) == (False, None, "")


# Decision model list

@patch("src.llm.client_base.requests.get")
def test_decision_model_list(get):
    get.return_value.json.return_value = {"data": [
        {"id": "typesafe/jev-1.13", "context_length": 32000, "pricing": {"prompt": "0.000000042", "completion": "0"}},
        {"id": "respan/span-01", "context_length": 0, "pricing": {"prompt": "0.00000002", "completion": "0"}},
    ]}
    ClientBase.api_token_limits.pop("span-01", None)

    model_list = ClientBase.get_decision_model_list("OpenRouter", "GPT_SECRET_KEY.txt")

    assert [value for _, value in model_list.available_models] == ["typesafe/jev-1.13", "respan/span-01"]
    assert model_list.available_models[0][0].startswith("typesafe/jev-1.13 | Context: ")
    assert "Context: unknown" in model_list.available_models[1][0]
    assert model_list.default_model == "typesafe/jev-1.13"
    assert ClientBase.api_token_limits["jev-1.13"] == 32000
    assert "span-01" not in ClientBase.api_token_limits
    assert get.call_args.args[0] == "https://openrouter.ai/api/v1/models?output_modalities=decisions"


@patch("src.llm.client_base.requests.get")
def test_nanogpt_decision_model_list_keeps_only_decision_models(get):
    get.return_value.json.return_value = {"data": [
        {"id": "typesafe/jev-1.13", "context_length": 32000, "architecture": {"output_modalities": ["decisions"]},
         "pricing": {"prompt": 0.042, "completion": 0, "unit": "per_million_tokens"}},
        {"id": "openai/gpt-4o-mini", "context_length": 128000, "architecture": {"output_modalities": ["text"]},
         "pricing": {"prompt": 0.15, "completion": 0.6, "unit": "per_million_tokens"}},
        {"id": "no-architecture", "context_length": 8000, "pricing": {"prompt": 1, "completion": 1}},
    ]}

    model_list = ClientBase.get_decision_model_list("NanoGPT", "GPT_SECRET_KEY.txt")

    assert get.call_args.args[0] == "https://nano-gpt.com/api/v1/models?detailed=true"
    assert [value for _, value in model_list.available_models] == ["typesafe/jev-1.13"]
    # NanoGPT prices are already per 1M tokens
    assert model_list.available_models[0][0].endswith(f"Prompt: {utils.format_price(0.042)}")


@patch("src.llm.client_base.requests.get", side_effect=requests.ConnectionError("offline"))
def test_decision_model_list_error_keeps_saved_model(_get):
    model_list = ClientBase.get_decision_model_list("OpenRouter", "GPT_SECRET_KEY.txt")
    assert model_list.allows_manual_model_input
    assert model_list.is_model_in_list("liquid/d1")


# Client creation

@pytest.mark.parametrize("service, key_files", [
    ("OpenRouter", ["GPT_SECRET_KEY.txt"]),
    ("NanoGPT", ["NANOGPT_SECRET_KEY.txt", "GPT_SECRET_KEY.txt"]),
])
@patch("src.llm.client_base.ClientBase.get_decision_model_list")
@patch("src.llm.client_base.ClientBase.__init__", return_value=None)
def test_create_game_master_client_uses_the_decision_service(init, model_list, service, key_files):
    cfg = MagicMock()
    cfg.multi_npc_game_master_mode = True
    cfg.game_master_use_decision_model = True
    cfg.game_master_decision_service = service
    cfg.game_master_decision_model = "typesafe/jev-1.13"
    cfg.game_master_custom_token_count = 4096
    cfg.llm_response_timeout = 10

    result = GameStateManager.create_game_master_client(cfg, "GPT_SECRET_KEY.txt")

    assert isinstance(result, ClientBase)
    assert model_list.call_args.args[0] == service
    args = init.call_args.args
    assert args[0] == service
    assert args[1] == "typesafe/jev-1.13"
    assert args[2] is None
    assert args[4] == key_files



# Prompt profiles

def test_decision_prompts_are_profile_types_and_overlay_the_prompts_tab(tmp_path):
    from src.config.config_loader import ConfigLoader
    from src.config.definitions.game_definitions import GameEnum
    from src.conversation.game_master import resolve_decision_prompts
    from src.prompt_profile_manager import PromptProfileManager

    for display_name in ["Skyrim Game Master Decision Instructions", "Skyrim Game Master Decision NPC Criteria", "Skyrim Game Master Decision Player Criteria"]:
        assert display_name in PromptProfileManager.display_names()
    # Merged into the two above, with {if after_event}
    for display_name in ["Skyrim Game Master Decision Event Instructions", "Skyrim Game Master Decision Event Player Criteria"]:
        assert display_name not in PromptProfileManager.display_names()

    profiles_path = tmp_path / "prompt_profiles.json"
    manager = PromptProfileManager(str(profiles_path))
    assert manager.save_section("rules", "Prefer whoever was named.")[0]
    assert manager.save_profile(DECISION_INSTRUCTIONS, "Short", "Who talks next? {rules}")[0]
    assert manager.set_active(DECISION_INSTRUCTIONS, "Short")[0]
    assert manager.save_profile(DECISION_NPC_CRITERIA, "Short", "{name} was named.")[0]
    assert manager.set_active(DECISION_NPC_CRITERIA, "Short")[0]
    assert not manager.validate_text(DECISION_PLAYER_CRITERIA, "{player_name} and {name}").is_success

    cfg = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(profiles_path))
    prompts = resolve_decision_prompts(cfg)
    assert prompts[DECISION_INSTRUCTIONS] == "Who talks next? Prefer whoever was named."
    assert prompts[DECISION_NPC_CRITERIA] == "{name} was named."
    assert prompts[DECISION_PLAYER_CRITERIA] == ("{player_name} gets the turn: someone asked {player_name} something or addressed them, "
                                                 "and no one wants to react to the events that just happened, if any")

    # After an event: {if after_event} is true in the question and the player criteria. The NPC criteria are the same
    assert manager.save_profile(DECISION_PLAYER_CRITERIA, "Short", "{if after_event}{player_name} was hit.{else}{player_name} was asked.{end if}")[0]
    assert manager.set_active(DECISION_PLAYER_CRITERIA, "Short")[0]
    cfg = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(profiles_path))
    prompts = resolve_decision_prompts(cfg, after_event=True)
    assert prompts[DECISION_INSTRUCTIONS] == "Who talks next? Prefer whoever was named." # The profile has no {if after_event}
    assert prompts[DECISION_NPC_CRITERIA] == "{name} was named."
    assert prompts[DECISION_PLAYER_CRITERIA] == "{player_name} was hit."
    assert resolve_decision_prompts(cfg)[DECISION_PLAYER_CRITERIA] == "{player_name} was asked."


@patch("src.conversation.game_master.requests.post")
def test_game_master_request_time_is_logged(post, caplog):
    post.return_value = decision_response("Lydia")
    with caplog.at_level(23):
        game_master(config(), [npc("Lydia")], client()).choose_next_speaker(MagicMock(), [], False)
    assert "Game Master request took" in caplog.text

    caplog.clear()
    chat_client = client()
    chat_client.request_call.side_effect = RuntimeError("down")
    with caplog.at_level(23):
        assert game_master(config(use_decision_model=False), [npc("Lydia")], chat_client).choose_next_speaker(MagicMock(), [], False) is None
    assert "Game Master request took" in caplog.text


def test_chat_model_drops_oldest_lines_to_fit_its_token_limit():
    cfg = config(use_decision_model=False)
    texts = [f"line {i} " + "word " * 19 + "word" for i in range(10)]
    lines = [f"Lydia: {text}" for text in texts]
    history = [UserMessage(cfg, text, "Lydia", False) for text in texts]
    ai_client = client()
    ai_client.token_limit = 150
    ai_client.request_call.return_value = '{"next": "Lydia", "reason": "asked"}'
    game_master(cfg, [npc("Lydia")], ai_client).choose_next_speaker(MagicMock(), history, False)

    request_text = ai_client.request_call.call_args.args[0].get_last_message().text
    kept = [line for line in lines if line in request_text]
    assert 0 < len(kept) < len(lines)
    assert kept == lines[-len(kept):]
    assert "NPC replies since" not in request_text # The NPC turn limit is only enforced in code


def test_chat_model_prompt_too_long_returns_none(caplog):
    ai_client = client()
    ai_client.token_limit = 3
    assert game_master(config(use_decision_model=False), [npc("Lydia")], ai_client).choose_next_speaker(MagicMock(), [], False) is None
    ai_client.request_call.assert_not_called()
    assert "too long for the Game Master model" in caplog.text


@patch("src.conversation.game_master.requests.post")
def test_decision_model_null_confidence_keeps_the_choice(post, caplog):
    lydia = npc("Lydia")
    response = decision_response("Lydia")
    response.json.return_value["answers"][DECISION_QUESTION].update({"confidence": None, "probabilities": {"Lydia": "high"}})
    post.return_value = response
    with caplog.at_level(23):
        assert game_master(config(), [lydia], client()).choose_next_speaker(MagicMock(), [], False) is lydia
    assert "Lydia speaks next (confidence None, probabilities {'Lydia': 'high'})" in caplog.text


def test_play_next_reply_stops_when_interrupted():
    turns = GameMasterTurns(MagicMock(), MagicMock())
    turns.start_turn()
    started_at = time.time()
    assert not turns.play_next_reply(10, lambda: True)
    assert time.time() - started_at < 1


# Replies generated ahead

LYDIA = SimpleNamespace(name="Lydia", ref_id="lydia", is_player_character=False)
ULFRIC = SimpleNamespace(name="Ulfric", ref_id="ulfric", is_player_character=False)


def sentence(speaker, text: str, is_system: bool = False) -> Sentence:
    return Sentence(SentenceContent(speaker, text, SentenceTypeEnum.SPEECH, is_system), "", 0)


def queued_texts(queue: SentenceQueue) -> list[str]:
    """The texts of the sentences in the queue, once the queue expects nothing more"""
    texts = []
    while (next_sentence := queue.get_next_sentence()) is not None:
        if next_sentence.text:
            texts.append(next_sentence.text)
    return texts


def wait_until(condition) -> bool:
    deadline = time.time() + 2
    while not condition():
        if time.time() >= deadline:
            return False
        time.sleep(0.01)
    return True


class TurnSetup:
    """A GameMasterTurns with a fake LLM that answers each request with one line, and a Game Master that makes the given picks"""
    def __init__(self, picks: list, max_npc_turns: int = 3, director: bool = False, director_max_npc_turns: int = 6):
        cfg = config(use_decision_model=False)
        for each_kind in MAX_NPC_TURNS_KINDS: # Each kind of conversation has its own
            setattr(cfg, max_npc_turns_identifier(each_kind), max_npc_turns)
        cfg.multi_npc_game_master_director_max_npc_turns = director_max_npc_turns
        context = MagicMock()
        context.config = cfg
        context.npc_event = None
        context.npcs_in_conversation.last_added_character = LYDIA
        context.npcs_in_conversation.contains_multiple_npcs.return_value = True
        context.get_character_names_as_text.return_value = "Lydia, Ulfric and Dragonborn"
        self.sentences = SentenceQueue()
        self.turns = GameMasterTurns(context, self.sentences)
        self.requests: list[tuple[str, str]] = [] # (speaker, the conversation sent with the request)
        self.output_manager = self.new_output_manager()
        self.conversation_type = self.new_conversation_type(director)
        self.live_messages = MagicMock()
        self.live_messages.get_persistent_messages.return_value = []
        self.__patch = patch("src.conversation.game_master.GameMaster")
        self.game_master_class = self.__patch.start()
        self.game_master = self.game_master_class.return_value
        self.game_master.choose_next_speaker.side_effect = picks

    def new_output_manager(self) -> MagicMock:
        output_manager = MagicMock()
        output_manager.generate_response.side_effect = self.__generate_response
        return output_manager

    @staticmethod
    def new_conversation_type(director: bool = False) -> MagicMock:
        conversation_type = MagicMock(spec=multi_npc_game_master)
        conversation_type.generate_npc_prompt.return_value = "NPC prompt"
        conversation_type.is_director_mode.return_value = director
        return conversation_type

    def __generate_response(self, request, _characters, queue, _actions, single_speaker):
        history = [m for m in request.get_persistent_messages() if isinstance(m, AssistantMessage)]
        self.requests.append((single_speaker.name, history_text(history, False)))
        queue.put(sentence(single_speaker, f"{single_speaker.name} line {len(self.requests)}."))
        queue.is_more_to_come = False
        queue.put(sentence(single_speaker, "", is_system=True))

    def start(self) -> threading.Thread:
        turn_id = self.turns.start_turn()
        self.sentences.is_more_to_come = True
        # Read through self, so a test can replace them mid-turn the way a settings change does
        thread = threading.Thread(target=self.turns.run_turn, args=(turn_id, lambda: self.conversation_type, lambda: self.output_manager, self.live_messages))
        thread.start()
        return thread

    def stop(self, thread: threading.Thread):
        self.turns.cancel()
        thread.join(2)
        self.__patch.stop()


def test_reply_queue_holds_sentences_until_released():
    shared = SentenceQueue()
    reply = ReplyQueue(shared, False)
    reply.put(sentence(LYDIA, "One."))
    assert queued_texts(shared) == []
    assert reply.release()
    assert shared.is_more_to_come
    reply.put(sentence(LYDIA, "Two."))
    reply.is_more_to_come = False
    assert queued_texts(shared) == ["One.", "Two."]
    assert [s.text for s in reply.sentences] == ["One.", "Two."]


def test_discarded_reply_queue_passes_nothing_on():
    shared = SentenceQueue()
    reply = ReplyQueue(shared, False)
    reply.put(sentence(LYDIA, "One."))
    reply.discard()
    reply.put(sentence(LYDIA, "Two."))
    assert not reply.release()
    assert queued_texts(shared) == []


def test_add_reply_to_history_continues_the_last_assistant_message():
    cfg = config()
    history = [AssistantMessage(cfg)]
    add_reply_to_history(history, [sentence(LYDIA, "Hello.")], cfg, True)
    assert len(history) == 1
    assert "Lydia: Hello." in history_text(history, False)


def test_game_master_history_lines_keeps_only_the_last_messages():
    cfg = config()
    history = [UserMessage(cfg, f"Line {i}.", "Dragonborn", False) for i in range(5)]
    assert len(game_master_history_lines(history, cfg)) == 5

    cfg.game_master_history_message_count = 2
    lines = game_master_history_lines(history, cfg)
    assert len(lines) == 2
    assert "Line 3." in lines[0] and "Line 4." in lines[1]


def test_game_master_history_lines_counts_each_npc_reply_as_one_message():
    cfg = config()
    history = [UserMessage(cfg, "Fight!", "Dragonborn", False)]
    add_reply_to_history(history, [sentence(LYDIA, "Lydia 1.")], cfg, True)
    add_reply_to_history(history, [sentence(ULFRIC, "Ulfric 1."), sentence(ULFRIC, "Ulfric 2.")], cfg, True)
    add_reply_to_history(history, [sentence(LYDIA, "Lydia 2.")], cfg, True)
    assert len(history) == 2 # The NPC replies in a row share one message
    assert game_master_history_lines(history, cfg)[1:] == ["Lydia: Lydia 1.", "Ulfric: Ulfric 1. Ulfric 2.", "Lydia: Lydia 2."]

    cfg.game_master_history_message_count = 2
    assert game_master_history_lines(history, cfg) == ["Ulfric: Ulfric 1. Ulfric 2.", "Lydia: Lydia 2."]


def test_game_master_history_lines_count_only_what_was_said():
    cfg = config()
    hulda, sven = SimpleNamespace(name="Hulda"), SimpleNamespace(name="Sven")
    events_only = UserMessage(cfg, "", "Dragonborn", False) # A continue ("."): only carries events
    events_only.is_director_message = True
    events_only.add_event(["Sven stood up from a(n) Chair"])
    scene_continues = UserMessage(cfg, "The scene continues.", "Dragonborn", True)
    scene_continues.is_director_message = True
    event_scene = UserMessage(cfg, "Hulda and Sven argue about the bard's song.", "", False) # An NPC Event's scene
    event_scene.is_director_message = True
    with_events = UserMessage(cfg, "Could we have a word?", "Dragonborn", False)
    with_events.add_event(["Sven has left the conversation."])
    history = [UserMessage(cfg, "Too old.", "Dragonborn", False), join_message(hulda, cfg), UserMessage(cfg, "Hello.", "Dragonborn", False),
               leave_message(hulda, cfg), leave_message(sven, cfg), events_only, scene_continues, event_scene, with_events]
    cfg.game_master_history_message_count = 2
    lines = game_master_history_lines(history, cfg)
    assert len(lines) == 7
    assert "Hello." in lines[0] and "Could we have a word?" in lines[-1]
    assert not any("Too old." in line or "Hulda has joined" in line for line in lines) # Before the oldest kept line


def test_a_direction_the_player_types_in_director_mode_counts_toward_the_limit():
    cfg = config()
    first = UserMessage(cfg, "Lydia draws her sword.", "Dragonborn", False)
    first.is_director_message = True
    second = UserMessage(cfg, "Ulfric laughs.", "Dragonborn", False)
    second.is_director_message = True
    cfg.game_master_history_message_count = 1
    assert game_master_history_lines([first, second], cfg) == ["(Ulfric laughs.)"]


def test_game_master_history_lines_includes_turn_thoughts_only_when_enabled():
    cfg = config()
    history: list = []
    add_reply_to_history(history, [sentence(LYDIA, "Hello."), Sentence(SentenceContent(LYDIA, "He looks tired.", SentenceTypeEnum.THOUGHT, False), "", 0)], cfg, True)
    add_reply_to_history(history, [sentence(ULFRIC, "Greetings.")], cfg, True)
    assert game_master_history_lines(history, cfg) == ["Lydia: Hello.", "Ulfric: Greetings."]

    cfg.game_master_include_turn_thoughts = True
    assert game_master_history_lines(history, cfg) == ["Lydia: Hello. <<thought Lydia>> He looks tired.", "Ulfric: Greetings."]


@patch("src.conversation.game_master.requests.post")
def test_decision_model_gets_only_the_last_messages(post):
    cfg = config()
    cfg.game_master_history_message_count = 1
    history = [UserMessage(cfg, "First.", "Dragonborn", False), UserMessage(cfg, "Second.", "Dragonborn", False)]
    post.return_value = decision_response("Lydia")
    game_master(cfg, [npc("Lydia")], client()).choose_next_speaker(MagicMock(), history, False)
    conversation = post.call_args.kwargs["json"]["state"]["conversation"]
    assert len(conversation) == 1 and "Second." in conversation[0]


def test_add_reply_to_history_starts_a_new_assistant_message_after_the_player():
    cfg = config()
    history = [UserMessage(cfg, "Hi there.", "Dragonborn", False)]
    add_reply_to_history(history, [sentence(LYDIA, "Hello.")], cfg, True)
    assert len(history) == 2
    assert isinstance(history[1], AssistantMessage) and history[1].thoughts_in_place


def test_next_reply_is_generated_while_the_last_one_plays():
    setup = TurnSetup([LYDIA, ULFRIC, None])
    thread = setup.start()
    try:
        assert wait_until(lambda: len(setup.requests) == 2)
        # Ulfric's reply is held while Lydia's is played, and Ulfric's request already has Lydia's whole reply
        assert queued_texts(setup.sentences) == ["Lydia line 1."]
        assert "Lydia line 1." in setup.requests[1][1]
        assert setup.turns.play_next_reply(1)
        assert queued_texts(setup.sentences) == ["Ulfric line 2."]
        assert not setup.turns.play_next_reply(1)
    finally:
        setup.stop(thread)


def test_generation_stays_one_reply_ahead():
    setup = TurnSetup([LYDIA, ULFRIC, LYDIA, None])
    thread = setup.start()
    try:
        assert wait_until(lambda: len(setup.requests) == 2)
        time.sleep(0.3)
        assert len(setup.requests) == 2
        assert setup.turns.play_next_reply(1)
        assert wait_until(lambda: len(setup.requests) == 3)
    finally:
        setup.stop(thread)


def test_replies_of_a_turn_share_one_screenshot_and_a_new_turn_takes_a_new_one():
    setup = TurnSetup([LYDIA, ULFRIC, None, LYDIA, None])
    captures = []
    generate_response = setup.output_manager.generate_response.side_effect
    def record_capture(request, *args, **kwargs):
        captures.append(request.vision_capture)
        return generate_response(request, *args, **kwargs)
    setup.output_manager.generate_response.side_effect = record_capture
    first_turn = setup.start()
    second_turn = None
    try:
        assert wait_until(lambda: len(captures) == 2)
        assert captures[0] is not None and captures[0] is captures[1]
        assert setup.turns.play_next_reply(1)
        first_turn.join(2)
        # The player speaks, which starts a new turn
        setup.turns.on_player_input()
        second_turn = setup.start()
        assert wait_until(lambda: len(captures) == 3)
        assert captures[2] is not None and captures[2] is not captures[0]
    finally:
        setup.stop(second_turn or first_turn)
        first_turn.join(2)


def test_no_reply_is_generated_when_the_player_is_picked():
    setup = TurnSetup([LYDIA, None])
    thread = setup.start()
    try:
        thread.join(2)
        assert len(setup.requests) == 1
        assert not setup.turns.play_next_reply(1)
    finally:
        setup.stop(thread)


def test_no_reply_is_generated_after_the_npc_turn_limit():
    setup = TurnSetup([LYDIA, ULFRIC], max_npc_turns=1)
    thread = setup.start()
    try:
        thread.join(2)
        assert len(setup.requests) == 1
        assert setup.game_master.choose_next_speaker.call_count == 1
        assert not setup.turns.play_next_reply(1)
    finally:
        setup.stop(thread)


@pytest.mark.parametrize("director", [False, True])
def test_the_player_is_only_offered_outside_director_mode(director):
    setup = TurnSetup([LYDIA, None], director=director)
    thread = setup.start()
    try:
        thread.join(2)
        first_speaker_call, next_speaker_call = setup.game_master.choose_next_speaker.call_args_list
        assert first_speaker_call.kwargs["allow_player"] is False
        assert next_speaker_call.kwargs["allow_player"] is not director
    finally:
        setup.stop(thread)


def test_director_mode_uses_the_director_turn_limit():
    setup = TurnSetup([LYDIA, ULFRIC], max_npc_turns=3, director=True, director_max_npc_turns=1)
    thread = setup.start()
    try:
        thread.join(2)
        assert len(setup.requests) == 1
        assert setup.game_master.choose_next_speaker.call_count == 1
        assert not setup.turns.play_next_reply(1)
    finally:
        setup.stop(thread)


def test_no_reply_is_generated_after_the_game_master_times_out():
    def slow_picks():
        yield LYDIA
        time.sleep(0.5) # The Game Master is slow to decide who follows Lydia
        yield ULFRIC
    picks = slow_picks()
    setup = TurnSetup([])
    setup.game_master.choose_next_speaker.side_effect = lambda *_args, **_kwargs: next(picks)
    thread = setup.start()
    try:
        assert wait_until(lambda: len(setup.requests) == 1)
        assert not setup.turns.play_next_reply(0.1)
        thread.join(2)
        assert not thread.is_alive()
        assert len(setup.requests) == 1
    finally:
        setup.stop(thread)


def conversation_in_game_master_mode(turns) -> tuple[Conversation, MagicMock]:
    context = MagicMock()
    context.npc_event = None
    context.config.end_conversation_keyword = "goodbye"
    context.npcs_in_conversation.active_character_count.return_value = 3
    old_chat_manager = MagicMock()
    conversation = Conversation(context, old_chat_manager, MagicMock(), MagicMock(), None, False, False)
    conversation._Conversation__conversation_type = multi_npc_game_master(context.config)
    conversation._Conversation__game_master_turns = turns
    return conversation, old_chat_manager


def settings(game_master_mode: bool) -> MagicMock:
    cfg = MagicMock()
    cfg.multi_npc_game_master_mode = game_master_mode
    return cfg


def test_hot_swap_keeps_the_game_master_turn_running():
    turns = MagicMock()
    conversation, old_chat_manager = conversation_in_game_master_mode(turns)
    new_chat_manager = MagicMock()
    assert conversation.hot_swap_settings(settings(True), MagicMock(), new_chat_manager, MagicMock())
    assert isinstance(conversation._Conversation__conversation_type, multi_npc_game_master)
    turns.log_prompts_again.assert_called_once()
    turns.drop_lookahead.assert_not_called()
    turns.stop_generation.assert_not_called()
    old_chat_manager.stop_generation.assert_not_called()
    new_chat_manager.stop_generation.assert_not_called()


def test_hot_swap_that_turns_game_master_mode_off_stops_the_held_reply():
    turns = MagicMock()
    turns.drop_lookahead.return_value = True
    conversation, _old_chat_manager = conversation_in_game_master_mode(turns)
    assert conversation.hot_swap_settings(settings(False), MagicMock(), MagicMock(), MagicMock())
    assert not isinstance(conversation._Conversation__conversation_type, multi_npc_game_master)
    turns.drop_lookahead.assert_called_once()
    turns.stop_generation.assert_called_once()


def test_hot_swap_that_turns_game_master_mode_off_keeps_the_reply_being_played_generating():
    turns = MagicMock()
    turns.drop_lookahead.return_value = False
    conversation, old_chat_manager = conversation_in_game_master_mode(turns)
    assert conversation.hot_swap_settings(settings(False), MagicMock(), MagicMock(), MagicMock())
    turns.drop_lookahead.assert_called_once()
    turns.stop_generation.assert_not_called()
    old_chat_manager.stop_generation.assert_not_called()


def test_stopping_generation_also_stops_the_chat_manager_of_the_game_master_turn():
    turns = MagicMock()
    conversation, chat_manager = conversation_in_game_master_mode(turns)
    conversation._Conversation__stop_generation()
    chat_manager.stop_generation.assert_called_once()
    turns.stop_generation.assert_called_once()


def conversation_with_history(messages: list) -> Conversation:
    conversation, _chat_manager = conversation_in_game_master_mode(MagicMock())
    thread = message_thread(conversation._Conversation__context.config, None)
    for message in messages:
        thread.add_message(message)
    conversation._Conversation__messages = thread
    return conversation


def npc_turns_message(cfg, turns: list[tuple]) -> AssistantMessage:
    message = AssistantMessage(cfg)
    message.is_multi_npc_message = True
    for speaker, text in turns:
        message.add_sentence(sentence(speaker, text))
    return message


def test_undo_npc_reply_in_game_master_mode_removes_only_the_last_npc_turn():
    cfg = config()
    direction = UserMessage(cfg, "Start a brawl.", "Dragonborn", False)
    replies = npc_turns_message(cfg, [(LYDIA, "Lydia 1."), (ULFRIC, "Ulfric 1."), (ULFRIC, "Ulfric 2."), (LYDIA, "Lydia 2.")])
    conversation = conversation_with_history([direction, replies])

    assert conversation.undo_last_npc_reply()
    persistent = conversation._Conversation__messages.get_persistent_messages()
    assert len(persistent) == 2
    assert persistent[1].get_formatted_content() == "Lydia: Lydia 1. Ulfric: Ulfric 1. Ulfric 2."

    assert conversation.undo_last_npc_reply()
    persistent = conversation._Conversation__messages.get_persistent_messages()
    assert persistent[1].get_formatted_content() == "Lydia: Lydia 1."


def test_undo_npc_reply_in_game_master_mode_drops_the_message_once_its_last_turn_is_removed():
    cfg = config()
    direction = UserMessage(cfg, "Start a brawl.", "Dragonborn", False)
    conversation = conversation_with_history([direction, npc_turns_message(cfg, [(LYDIA, "Lydia 1.")])])

    assert conversation.undo_last_npc_reply()
    persistent = conversation._Conversation__messages.get_persistent_messages()
    assert len(persistent) == 1 and persistent[0].text == "Start a brawl."
    assert not conversation.undo_last_npc_reply()


def wait_for_held_reply(setup: TurnSetup):
    """Waits until Ulfric's reply is generated and held while Lydia's is played, and the Game Master has picked who follows Ulfric"""
    assert wait_until(lambda: len(setup.requests) == 2 and setup.game_master.choose_next_speaker.call_count == 3)
    assert queued_texts(setup.sentences) == ["Lydia line 1."]


def test_settings_change_mid_turn_plays_the_held_reply_and_uses_the_new_settings_after_it():
    setup = TurnSetup([LYDIA, ULFRIC, LYDIA, None], max_npc_turns=4)
    thread = setup.start()
    try:
        wait_for_held_reply(setup)
        old_output_manager, old_conversation_type = setup.output_manager, setup.conversation_type
        setup.output_manager, setup.conversation_type = setup.new_output_manager(), setup.new_conversation_type()

        assert setup.turns.play_next_reply(1)
        assert queued_texts(setup.sentences) == ["Ulfric line 2."]
        assert wait_until(lambda: len(setup.requests) == 3)
        assert old_output_manager.generate_response.call_count == 2
        assert setup.output_manager.generate_response.call_count == 1
        assert old_conversation_type.generate_npc_prompt.call_count == 2
        assert setup.conversation_type.generate_npc_prompt.call_count == 1
        # The decision after the new reply is made with the new Game Master client
        assert wait_until(lambda: setup.game_master.choose_next_speaker.call_count == 4)
        latest_game_master_args = setup.game_master_class.call_args.args
        assert latest_game_master_args[1] is setup.conversation_type
        assert latest_game_master_args[2] is setup.output_manager.game_master_client

        assert setup.turns.play_next_reply(1)
        assert queued_texts(setup.sentences) == ["Lydia line 3."]
        assert not setup.turns.play_next_reply(1)
    finally:
        setup.stop(thread)


def test_turning_game_master_mode_off_mid_turn_generates_no_more_replies():
    setup = TurnSetup([LYDIA, ULFRIC, LYDIA, None])
    thread = setup.start()
    try:
        wait_for_held_reply(setup)
        setup.conversation_type = MagicMock(spec=multi_npc)
        assert setup.turns.play_next_reply(1)
        assert queued_texts(setup.sentences) == ["Ulfric line 2."]
        thread.join(2)
        assert not thread.is_alive()
        assert len(setup.requests) == 2
    finally:
        setup.stop(thread)


def test_npc_prompt_is_logged_once_until_logged_again(caplog):
    setup = TurnSetup([LYDIA, None, LYDIA, None, LYDIA, None])
    try:
        with caplog.at_level(23):
            for log_again in [False, False, True]:
                if log_again:
                    setup.turns.log_prompts_again()
                thread = setup.start()
                thread.join(2)
                assert not thread.is_alive()
        assert caplog.text.count("Game Master Mode prompt for Lydia =====") == 2
    finally:
        setup.stop(thread)


def test_turn_stop_generation_stops_the_chat_manager_the_latest_reply_used():
    setup = TurnSetup([LYDIA, None])
    thread = setup.start()
    try:
        thread.join(2)
        old_output_manager = setup.output_manager
        setup.output_manager = setup.new_output_manager()
        setup.turns.stop_generation()
        old_output_manager.stop_generation.assert_called_once()
        setup.output_manager.stop_generation.assert_not_called()
    finally:
        setup.stop(thread)


def test_cancel_throws_away_the_held_reply():
    setup = TurnSetup([LYDIA, ULFRIC, None])
    thread = setup.start()
    try:
        assert wait_until(lambda: len(setup.requests) == 2)
        setup.turns.cancel()
        assert not setup.turns.play_next_reply(1)
        assert "Ulfric line 2." not in queued_texts(setup.sentences)
    finally:
        setup.stop(thread)


def test_drop_lookahead_keeps_the_playing_reply_and_hands_the_turn_to_the_player():
    setup = TurnSetup([LYDIA, ULFRIC, None])
    thread = setup.start()
    try:
        assert wait_until(lambda: len(setup.requests) == 2)
        assert setup.turns.drop_lookahead()
        assert not setup.turns.play_next_reply(1)
        assert queued_texts(setup.sentences) == ["Lydia line 1."]
    finally:
        setup.stop(thread)
