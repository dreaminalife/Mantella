import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from src.config.definitions.game_definitions import GameEnum
from src.conversation.context import build_bios_and_summaries_text
from src.conversation.conversation_type import pc_to_npc
from src.conversation.npc_initiative import DECISION_QUESTION, NOBODY, InitiativeCandidate, build_question, choose_initiator, label_candidates

API_KEY = "sk-or-secret"
BIOS = "[Hulda's bio] Hulda runs the Bannered Mare.\n\n==========\n\n[Lydia's bio] Lydia is a housecarl."


def config(use_decision_model: bool = True, threshold: float = 0.6):
    cfg = MagicMock()
    cfg.game_master_use_decision_model = use_decision_model
    cfg.npc_initiative_threshold = threshold
    cfg.npc_initiative_decision_instructions = ("{player_name} has just come near these people in {location}. The time is {time} {time_group}.\n\n{bios_and_summaries}\n\n"
                                                "Does one of {names} have to talk to {player_name}?")
    cfg.npc_initiative_decision_npc_criteria = "{name} has a reason to talk to {player_name}."
    cfg.npc_initiative_decision_nobody_criteria = "Nobody has a reason to talk to {player_name}."
    cfg.prompt_profile_manager.resolve_prompt.side_effect = lambda _prompt_type, text: text
    cfg.llm_response_timeout = 30
    return cfg


def client(service: str = "OpenRouter"):
    c = MagicMock()
    c.service_name = service
    c.model_name = "typesafe/jev-1.13"
    c.api_key = API_KEY
    return c


def decision_response(probabilities: dict[str, float], status: int = 200):
    response = MagicMock()
    response.status_code = status
    if status == 200:
        top = max(probabilities, key=lambda label: probabilities[label])
        body = {"answers": {DECISION_QUESTION: {"type": "choice", "choice": top, "confidence": probabilities[top], "probabilities": probabilities}}}
    else:
        body = {"error": {"code": status, "message": "Invalid request parameters"}}
    response.json.return_value = body
    response.text = json.dumps(body)
    return response


def candidates():
    return [InitiativeCandidate("Hulda", 0x00013BA3), InitiativeCandidate("Lydia", -16775166)]


def choose(cfg, ai_client=None):
    return choose_initiator(cfg, ai_client or client(), candidates(), "Dragonborn", "Whiterun", 20, BIOS)


# Decision model

@patch("src.conversation.game_master.requests.post")
def test_top_candidate_at_threshold_is_chosen(post):
    post.return_value = decision_response({"Hulda": 0.6, "Lydia": 0.3, NOBODY: 0.1})
    chosen = choose(config())
    assert chosen and chosen.name == "Hulda"
    body = post.call_args.kwargs["json"]
    question = body["questions"][DECISION_QUESTION]
    assert question["instructions"] == (f"Dragonborn has just come near these people in Whiterun. The time is 8 in the late evening.\n\n{BIOS}\n\n"
                                        "Does one of Hulda, Lydia have to talk to Dragonborn?")
    assert list(question["criteria"]) == ["Hulda", "Lydia", NOBODY]
    assert body["state"] == {"scene": "", "conversation": []}
    assert post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {API_KEY}"
    assert post.call_args.kwargs["timeout"] == 10 # Capped, so a slow check does not hold up the player


@patch("src.conversation.game_master.requests.post")
def test_below_threshold_returns_none(post):
    post.return_value = decision_response({"Hulda": 0.5, "Lydia": 0.3, NOBODY: 0.2})
    assert choose(config()) is None


@patch("src.conversation.game_master.requests.post")
def test_skip_threshold_chooses_top_candidate_below_threshold(post):
    post.return_value = decision_response({"Hulda": 0.5, "Lydia": 0.3, NOBODY: 0.2})
    chosen = choose_initiator(config(), client(), candidates(), "Dragonborn", "Whiterun", 20, BIOS, skip_threshold=True)
    assert chosen and chosen.name == "Hulda"


@patch("src.conversation.game_master.requests.post")
def test_skip_threshold_still_respects_nobody_on_top(post):
    post.return_value = decision_response({"Hulda": 0.2, "Lydia": 0.1, NOBODY: 0.7})
    assert choose_initiator(config(), client(), candidates(), "Dragonborn", "Whiterun", 20, BIOS, skip_threshold=True) is None


@patch("src.conversation.game_master.requests.post")
def test_nobody_on_top_returns_none(post):
    post.return_value = decision_response({"Hulda": 0.1, "Lydia": 0.1, NOBODY: 0.8})
    assert choose(config()) is None


@patch("src.conversation.game_master.requests.post")
def test_negative_refid_comes_back_unchanged(post):
    post.return_value = decision_response({"Hulda": 0.1, "Lydia": 0.8, NOBODY: 0.1})
    chosen = choose(config())
    assert chosen and chosen.refid == -16775166


@patch("src.conversation.game_master.requests.post")
def test_duplicate_names_map_to_the_right_candidates(post):
    guards = [InitiativeCandidate("Whiterun Guard", 1), InitiativeCandidate("Whiterun Guard", 2)]
    post.return_value = decision_response({"Whiterun Guard": 0.1, "Whiterun Guard (2)": 0.8, NOBODY: 0.1})
    chosen = choose_initiator(config(), client(), guards, "Dragonborn", "Whiterun", 12, "")
    assert chosen and chosen.refid == 2
    assert list(label_candidates(guards)) == ["Whiterun Guard", "Whiterun Guard (2)"]


@patch("src.conversation.game_master.requests.post")
def test_unsupported_service_returns_none(post):
    assert choose(config(), client("OpenAI")) is None
    post.assert_not_called()


@patch("src.conversation.game_master.requests.post")
def test_http_error_returns_none(post):
    post.return_value = decision_response({}, status=400)
    assert choose(config()) is None


@patch("src.conversation.game_master.requests.post", side_effect=requests.Timeout("timed out"))
def test_timeout_returns_none(_post):
    assert choose(config()) is None


@patch("src.conversation.game_master.requests.post")
def test_cancelled_check_sends_nothing(post):
    assert choose_initiator(config(), client(), candidates(), "Dragonborn", "Whiterun", 12, BIOS, is_cancelled=lambda: True) is None
    post.assert_not_called()


@patch("src.conversation.game_master.requests.post")
def test_check_cancelled_while_waiting_does_not_retry_and_returns_none(post):
    cancelled = []
    def server_error(*_args, **_kwargs):
        cancelled.append(True) # The player starts a conversation while the request is running
        return decision_response({}, status=502)
    post.side_effect = server_error
    assert choose_initiator(config(), client(), candidates(), "Dragonborn", "Whiterun", 12, BIOS, is_cancelled=lambda: bool(cancelled)) is None
    assert post.call_count == 1


@patch("src.conversation.game_master.requests.post")
def test_answer_arriving_after_cancel_is_dropped(post):
    cancelled = []
    def answer(*_args, **_kwargs):
        cancelled.append(True)
        return decision_response({"Hulda": 0.9, "Lydia": 0.05, NOBODY: 0.05})
    post.side_effect = answer
    assert choose_initiator(config(), client(), candidates(), "Dragonborn", "Whiterun", 12, BIOS, is_cancelled=lambda: bool(cancelled)) is None


def test_no_client_or_candidates_returns_none():
    assert choose_initiator(config(), None, candidates(), "Dragonborn", "Whiterun", 12, BIOS) is None
    assert choose_initiator(config(), client(), [], "Dragonborn", "Whiterun", 12, "") is None


# Instructions

def test_instructions_leave_variables_in_the_bios_alone():
    instructions = build_question(config(), ["Hulda"], "Dragonborn", "Whiterun", 12, "Hulda says {location} a lot.")["instructions"]
    assert "Hulda says {location} a lot.\n\n" in instructions
    assert "in Whiterun. The time is 12 " in instructions


def test_instructions_use_the_active_prompt_profile():
    cfg = config()
    cfg.prompt_profile_manager.resolve_prompt.side_effect = lambda _prompt_type, text: "At {location}: {bios_and_summaries}" if "{names}" in text else text
    assert build_question(cfg, ["Hulda"], "Dragonborn", "Whiterun", 12, BIOS)["instructions"] == f"At Whiterun: {BIOS}"


def test_bios_and_summaries_use_the_multi_npc_layout_for_a_single_npc():
    hulda = SimpleNamespace(name="Hulda", bio="Hulda runs the Bannered Mare. She knows {player_name}.")
    rememberer = MagicMock()
    long_memory = "old " * 400 + "Dragonborn owes Hulda 50 gold."
    rememberer.get_character_summary.return_value = long_memory
    rememberer.get_character_private_thought.return_value = "I hope {player_name} comes back."
    rememberer.get_character_personal_reflection.return_value = "Travelers never pay on time."
    text = build_bios_and_summaries_text([hulda], SimpleNamespace(), rememberer, "world", "Dragonborn",  # type: ignore[list-item]
                                         contains_player=True, always_multi_npc=True)
    bio = text.split("Hulda's bio,")[1]
    memory = text.split("Hulda's memory,")[1]
    assert "She knows Dragonborn." in bio
    assert "Travelers never pay on time." in bio # The personal reflection goes with the bio
    assert long_memory in memory # Not cut
    assert "I hope Dragonborn comes back." in memory # The private thought is appended to the memory


# LLM fallback

def test_llm_fallback_parses_json_choice():
    ai_client = client()
    ai_client.request_call.return_value = 'Sure: {"next": "lydia", "reason": "the promised sword"}'
    chosen = choose(config(use_decision_model=False), ai_client)
    assert chosen and chosen.name == "Lydia"


def test_llm_fallback_nobody_or_invalid_returns_none():
    ai_client = client()
    ai_client.request_call.return_value = '{"next": "Nobody", "reason": "nothing to say"}'
    assert choose(config(use_decision_model=False), ai_client) is None
    ai_client.request_call.return_value = "I think Hulda."
    assert choose(config(use_decision_model=False), ai_client) is None


def test_client_exception_returns_none():
    ai_client = client()
    ai_client.request_call.side_effect = RuntimeError("boom")
    assert choose(config(use_decision_model=False), ai_client) is None


# pc_to_npc

def pc_to_npc_context(npc_initiative: bool, automatic_greeting: bool = False):
    cfg = MagicMock()
    cfg.game = GameEnum.SKYRIM
    cfg.automatic_greeting = automatic_greeting
    cfg.npc_initiative_opener = "{name} approaches {player_name}."
    cfg.prompt = "normal prompt"
    cfg.npc_initiative_prompt = "initiative prompt"
    cfg.actions = []
    cfg.prompt_profile_manager.resolve_prompt.side_effect = lambda _prompt_type, text: text
    player, hulda = MagicMock(), MagicMock()
    player.name, player.is_player_character = "Dragonborn", True
    hulda.name, hulda.is_player_character = "Hulda", False
    context = MagicMock()
    context.config = cfg
    context.npc_initiative = npc_initiative
    context.language = {"hello": "Hello"}
    context.npcs_in_conversation.get_player_character.return_value = player
    context.npcs_in_conversation.get_all_characters.return_value = [player, hulda]
    context.get_characters_excluding_player.return_value.get_all_characters.return_value = [hulda]
    return cfg, context


def one_message():
    messages = MagicMock()
    messages.__len__.return_value = 1
    return messages


def test_opener_replaces_greeting_and_keeps_the_flag():
    cfg, context = pc_to_npc_context(npc_initiative=True)
    message = pc_to_npc(cfg).get_user_message(context, one_message())
    assert message is not None
    assert message.text == "Hulda approaches Dragonborn."
    assert message.is_director_message
    assert not message.is_multi_npc_message
    assert context.npc_initiative # The system prompt still needs it for the first reply


def test_normal_greeting_without_the_flag():
    cfg, context = pc_to_npc_context(npc_initiative=False, automatic_greeting=True)
    message = pc_to_npc(cfg).get_user_message(context, one_message())
    assert message is not None
    assert message.text == "Hello Hulda."
    assert not message.is_director_message


def test_initiative_prompt_is_used_while_the_flag_is_set():
    cfg, context = pc_to_npc_context(npc_initiative=True)
    conversation_type = pc_to_npc(cfg)
    conversation_type.generate_prompt(context)
    assert context.generate_system_message.call_args.args[0] == "initiative prompt"
    assert context.generate_system_message.call_args.args[4] == "skyrim_npc_initiative_prompt"
    context.npc_initiative = False
    conversation_type.generate_prompt(context)
    assert context.generate_system_message.call_args.args[0] == "normal prompt"
    assert context.generate_system_message.call_args.args[4] == "skyrim_prompt"
