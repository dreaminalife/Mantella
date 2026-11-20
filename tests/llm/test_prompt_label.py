from copy import deepcopy

from src.config.definitions.llm_definitions import NarrationIndicatorsEnum
from src.llm.client_base import describe_request
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage
from src.prompt_label import LabeledPrompt, describe_prompt, label_of, label_prompt, label_text


class _Config:
    def __init__(self):
        self.narration_indicators = NarrationIndicatorsEnum.PARANTHESES
        self.prompt_profile_manager = None


class _Profiles:
    def __init__(self, active: dict[str, str]):
        self.active = active

    def get_active_name(self, prompt_type: str):
        return self.active.get(prompt_type)


def test_describe_prompt_names_the_prompt_profile_speaker_and_kept_parts():
    profiles = _Profiles({"skyrim_game_master_npc_prompt": "Gritty"})
    # 'say_to_room' is not a condition the Game Master NPC Prompt knows, so it is left out
    label = describe_prompt(profiles, "skyrim_game_master_npc_prompt", {"shout", "director", "say_to_room"}, "Lydia")
    assert label == 'Skyrim Game Master NPC Prompt, profile "Gritty", for Lydia, with {if director}, {if shout}'


def test_describe_prompt_without_profile_or_conditions_is_just_the_name():
    assert describe_prompt(_Profiles({}), "skyrim_prompt") == "Skyrim Prompt"
    assert describe_prompt(None, "memory_prompt", set()) == "Memory Prompt"


def test_label_is_kept_when_text_is_added_and_when_copied():
    prompt = label_prompt("You are Lydia.", None, "skyrim_prompt")
    prompt += "\n\nAnswer as a group."
    assert prompt == "You are Lydia.\n\nAnswer as a group."
    assert label_of(prompt) == "Skyrim Prompt"
    assert label_of(deepcopy(prompt)) == "Skyrim Prompt"
    assert label_of("plain text") == ""


def test_request_names_the_prompt_of_the_thread_and_its_copies():
    cfg = _Config()
    thread = message_thread(cfg, label_prompt("You are Lydia.", None, "skyrim_multi_npc_prompt"))
    thread.add_message(UserMessage(cfg, "Hello.", "Prisoner"))
    openai_messages = thread.get_openai_messages()
    assert describe_request(thread, openai_messages) == "LLM request for Skyrim Multi-NPC Prompt"
    assert thread.copy_with([]).prompt_label == "Skyrim Multi-NPC Prompt"
    thread.modify_messages(label_prompt("You are Lydia and Aela.", None, "skyrim_game_master_prompt"), True)
    assert thread.prompt_label == "Skyrim Game Master Prompt"


def test_request_without_a_named_prompt_and_with_a_picture():
    cfg = _Config()
    thread = message_thread(cfg, "You are Lydia.")
    openai_messages = thread.get_openai_messages() + [{"role": "user", "content": [
        {"type": "text", "text": "Look."},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
    ]}]
    assert describe_request(thread, openai_messages) == "LLM request, with a picture"
    message = UserMessage(cfg, label_text("Write a bio.", "the Bio Editor"))
    assert describe_request(message, [message.get_openai_message()]) == "LLM request for the Bio Editor"


def test_labeled_prompt_is_the_text():
    prompt = LabeledPrompt("abc", "Skyrim Prompt")
    assert isinstance(prompt, str) and prompt == "abc" and prompt.strip() == "abc"
