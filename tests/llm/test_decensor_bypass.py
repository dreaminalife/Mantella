from unittest.mock import MagicMock, patch

from src.config.definitions.llm_definitions import DecensorPlacementEnum, NarrationIndicatorsEnum
from src.llm.client_base import ClientBase
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage
from src.llm.sonnet_cache_connector import SonnetCacheConnector
from src.prompt_profile_manager import PromptProfileManager, PromptSectionError


class _Config:
    def __init__(self, handshake: str = "Acknowledge the scene.", commitment: str = "I will stay in character."):
        self.narration_indicators = NarrationIndicatorsEnum.PARANTHESES
        self.enable_decensor_bypass = True
        self.decensor_handshake_prompt = handshake
        self.decensor_assistant_prompt = commitment
        self.decensor_assistant_placement = DecensorPlacementEnum.AFTER_SYSTEM_PROMPT
        self.prompt_profile_manager = None


def test_live_thread_inserts_handshake_then_commitment_after_system():
    cfg = _Config()
    thread = message_thread(cfg, "You are Lydia.", include_decensor_bypass=True)
    thread.add_message(UserMessage(cfg, "Draw your sword.", "Prisoner"))

    chat = thread.get_openai_messages()

    assert [message["role"] for message in chat] == ["system", "user", "assistant", "user"]
    assert chat[0]["content"] == "You are Lydia."
    assert chat[1]["content"] == "Acknowledge the scene."
    assert chat[2]["content"] == "I will stay in character."
    assert chat[3]["content"] == "Draw your sword."


def test_bypass_stays_out_of_stored_history():
    cfg = _Config()
    thread = message_thread(cfg, "You are Lydia.", include_decensor_bypass=True)
    thread.add_message(UserMessage(cfg, "Draw your sword.", "Prisoner"))

    stored = message_thread.transform_to_openai_messages(thread.get_persistent_messages())

    assert [message["role"] for message in stored] == ["user"]
    assert stored[0]["content"] == "Draw your sword."


def test_other_threads_do_not_insert_the_pair_when_the_toggle_is_on():
    cfg = _Config()
    thread = message_thread(cfg, "Summarize this conversation.")

    chat = thread.get_openai_messages()

    assert [message["role"] for message in chat] == ["system"]
    assert chat[0]["content"] == "Summarize this conversation."


def test_disabled_toggle_leaves_the_request_unchanged():
    cfg = _Config()
    cfg.enable_decensor_bypass = False
    thread = message_thread(cfg, "You are Lydia.", include_decensor_bypass=True)
    thread.add_message(UserMessage(cfg, "Hello.", "Prisoner"))

    chat = thread.get_openai_messages()

    assert [message["role"] for message in chat] == ["system", "user"]


def test_empty_commitment_skips_both_messages():
    cfg = _Config(commitment="   ")
    thread = message_thread(cfg, "You are Lydia.", include_decensor_bypass=True)

    chat = thread.get_openai_messages()

    assert [message["role"] for message in chat] == ["system"]


def test_active_profile_section_is_expanded_in_the_commitment(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    assert manager.save_section("commitment_section", "Stay in character.")[0]
    assert manager.save_profile("decensor_assistant_prompt", "Direct", "{commitment_section}")[0]
    assert manager.set_active("decensor_assistant_prompt", "Direct")[0]

    cfg = _Config(commitment="{commitment_section}")
    cfg.prompt_profile_manager = manager
    thread = message_thread(cfg, "You are Lydia.", include_decensor_bypass=True)

    chat = thread.get_openai_messages()

    assert chat[2]["role"] == "assistant"
    assert chat[2]["content"] == "Stay in character."


def test_broken_profile_section_skips_the_pair(tmp_path):
    manager = PromptProfileManager(str(tmp_path / "prompt_profiles.json"))
    assert manager.save_section("handshake_section", "Acknowledge the scene.")[0]
    assert manager.save_profile("decensor_handshake_prompt", "Broken", "{handshake_section}")[0]
    assert manager.set_active("decensor_handshake_prompt", "Broken")[0]
    assert manager.delete_section("handshake_section")[0]

    cfg = _Config(handshake="{handshake_section}")
    cfg.prompt_profile_manager = manager
    thread = message_thread(cfg, "You are Lydia.", include_decensor_bypass=True)

    try:
        chat = thread.get_openai_messages()
    except PromptSectionError:
        raise AssertionError("A broken decensor profile should skip injection instead of raising.")

    assert [message["role"] for message in chat] == ["system"]


def _end_thread(placement: DecensorPlacementEnum, handshake: str = "Acknowledge the scene.") -> message_thread:
    cfg = _Config(handshake=handshake)
    cfg.decensor_assistant_placement = placement
    thread = message_thread(cfg, "You are Lydia.", include_decensor_bypass=True)
    thread.add_message(UserMessage(cfg, "Draw your sword.", "Prisoner"))
    return thread


def test_end_placement_appends_only_the_commitment_after_the_latest_input():
    thread = _end_thread(DecensorPlacementEnum.END_AS_ASSISTANT)

    chat = thread.append_decensor_commitment(thread.get_openai_messages())

    assert [message["role"] for message in chat] == ["system", "user", "assistant"]
    assert chat[1]["content"] == "Draw your sword."
    assert chat[2]["content"] == "I will stay in character."


def test_end_placement_as_system_sends_the_commitment_as_a_system_message():
    thread = _end_thread(DecensorPlacementEnum.END_AS_SYSTEM)

    chat = thread.append_decensor_commitment(thread.get_openai_messages())

    assert [message["role"] for message in chat] == ["system", "user", "system"]
    assert chat[2]["content"] == "I will stay in character."


def test_end_placement_does_not_need_the_handshake():
    thread = _end_thread(DecensorPlacementEnum.END_AS_SYSTEM, handshake="")

    chat = thread.append_decensor_commitment(thread.get_openai_messages())

    assert [message["role"] for message in chat] == ["system", "user", "system"]


def test_after_system_placement_appends_nothing_at_the_end():
    cfg = _Config()
    thread = message_thread(cfg, "You are Lydia.", include_decensor_bypass=True)
    thread.add_message(UserMessage(cfg, "Draw your sword.", "Prisoner"))

    chat = thread.append_decensor_commitment(thread.get_openai_messages())

    assert [message["role"] for message in chat] == ["system", "user", "assistant", "user"]


def test_request_ends_with_the_commitment_after_cache_breakpoints():
    thread = _end_thread(DecensorPlacementEnum.END_AS_SYSTEM)
    client = ClientBase("https://openrouter.ai/api/v1", "google/gemini-3-flash", {"max_tokens": 10}, 4096, ["missing.txt"])
    client._sonnet_cache_connector = SonnetCacheConnector(True)
    sync_client = MagicMock()
    sync_client.chat.completions.create.return_value.choices = [MagicMock()]
    sync_client.chat.completions.create.return_value.choices[0].message.content = "As you wish."

    with patch.object(client, "generate_sync_client", return_value=sync_client):
        assert client.request_call(thread) == "As you wish."

    sent = sync_client.chat.completions.create.call_args.kwargs["messages"]
    assert [message["role"] for message in sent] == ["system", "user", "system"]
    assert "cache_control" in sent[0]["content"][-1] # The breakpoint still lands, even though the latest input is no longer last
    assert sent[2] == {"role": "system", "content": "I will stay in character."}


def test_config_loads_after_system_placement_by_default(default_config):
    assert default_config.decensor_assistant_placement == DecensorPlacementEnum.AFTER_SYSTEM_PROMPT
