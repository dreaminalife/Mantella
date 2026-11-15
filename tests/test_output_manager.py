import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock
from src.output_manager import ChatManager
from src.config.config_loader import ConfigLoader
from src.config.definitions.llm_definitions import NarrationHandlingEnum
from src.tts.piper import Piper
from src.llm.sentence_queue import SentenceQueue
from src.llm.message_thread import message_thread
from src.characters_manager import Characters
from src.character_manager import Character
from src.llm.sentence_content import SentenceTypeEnum, SentenceContent
from src.llm.sentence import Sentence
from src.conversation.action import Action
import time

class MockAIClient:
    """Mock AIClient for testing that simulates different response patterns"""
    def __init__(self, response_pattern=None, error_on_call=False, delay=0.01):
        self.response_pattern = response_pattern if response_pattern is not None else ["Hello there."]
        self.error_on_call = error_on_call
        self.delay = delay
        
    async def streaming_call(self, messages=None, is_multi_npc=False, response_started_at=None):
        """Simulates streaming call with configurable response patterns"""
        if self.error_on_call:
            raise Exception("Simulated API error")
            
        for chunk in self.response_pattern:
            yield chunk
            await asyncio.sleep(self.delay)  # Small delay to simulate streaming
    
    def get_count_tokens(self, text):
        """Mock token counting"""
        return len(str(text).split())

    def is_too_long(self, messages, token_limit_percent):
        return False

@pytest.fixture
def mock_ai_client():
    """Fixture providing a default MockAIClient instance"""
    return MockAIClient()

@pytest.fixture
def mock_queue() -> SentenceQueue:
    """Provides an empty sentence queue for collecting results"""
    return SentenceQueue()

@pytest.fixture
def mock_messages(default_config: ConfigLoader) -> message_thread:
    """Provides an empty message thread"""
    return message_thread(default_config, None)

@pytest.fixture
def mock_actions() -> list[Action]:
    """Provides a list of mock actions"""
    action = Action(
        identifier="wave", 
        name="Wave",
        keyword="Wave",
        description="Waves at the player",
        prompt_text="If the player asks you to wave, begin your response with 'Wave:'.",
        requires_response=False,
        is_interrupting=False,
        one_on_one=True,
        multi_npc=False,
        radiant=False,
    )
    return [action]

@pytest.fixture
def output_manager(default_config: ConfigLoader, piper: Piper, mock_ai_client: MockAIClient, monkeypatch) -> ChatManager:
    """Creates a ChatManager instance with mocked TTS and AI client"""
    # Mock the TTS synthesize method to avoid actual audio generation and file system interaction
    piper.synthesize = MagicMock(return_value="mock_audio_file.wav")
    # Mock get_audio_duration as well
    monkeypatch.setattr('src.utils.get_audio_duration', lambda *args, **kwargs: 1.0)
    
    manager = ChatManager(default_config, piper, mock_ai_client)
    return manager

def get_sentence_list_from_queue(queue: SentenceQueue) -> list[Sentence]:
    """Helper function to extract sentences from the queue"""
    sentences = []
    while True:
        sentence = queue.get_next_sentence()
        if not sentence:
            break
        sentences.append(sentence)
    return sentences


@pytest.mark.asyncio
async def test_process_response_api_error(output_manager: ChatManager, example_skyrim_npc_character: Character, example_characters_pc_to_npc: Characters, mock_queue: SentenceQueue, mock_messages: message_thread, mock_actions: list[Action], monkeypatch):
    """Test handling of a simulated API error during streaming"""
    output_manager._ChatManager__client.error_on_call = True
    monkeypatch.setattr("src.utils.play_error_sound", lambda *a, **kw: None)
    monkeypatch.setattr(time, "sleep", lambda *a, **kw: None) # Skip sleeping between retries
    
    await output_manager.process_response(example_skyrim_npc_character, mock_queue, mock_messages, example_characters_pc_to_npc, mock_actions)

    output_sentences = get_sentence_list_from_queue(mock_queue)

    # There should be a number of output sentences based on the number of retries, but just take the first as all should have the same message
    error_sentence = output_sentences[0]
    assert "I can't find the right words at the moment" in error_sentence.content.text
    assert error_sentence.content.is_system_generated_sentence is True
    assert error_sentence.content.speaker == example_skyrim_npc_character


@pytest.mark.asyncio
async def test_process_response_actions(output_manager: ChatManager, example_skyrim_npc_character: Character, example_characters_pc_to_npc: Characters, mock_queue: SentenceQueue, mock_messages: message_thread, mock_actions: list[Action]):
    """Test processing of actions embedded in the response"""
    output_manager._ChatManager__client.response_pattern = ["Wave: ", "See ", "you ", "later."]
    
    await output_manager.process_response(example_skyrim_npc_character, mock_queue, mock_messages, example_characters_pc_to_npc, mock_actions)
    
    output_sentences = get_sentence_list_from_queue(mock_queue)
    assert len(output_sentences) == 2 # Action+Speech, Empty
    
    sentence = output_sentences[0]
    assert sentence.content.text.strip() == "See you later."
    assert sentence.content.actions # Should have actions
    assert {"identifier": "wave"} in sentence.content.actions # Check if the specific action identifier is present


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_pattern, narration_handling, max_sentences, min_words, expected_texts, expected_types",
    [
        # Basic speech
        (
            ["Hello ", "world."],
            NarrationHandlingEnum.CUT_NARRATIONS,
            None,
            None,
            ["Hello world.", ""],
            [SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH],
        ),
        # Narration kept
        (
            ["(The ", "guard ", "sighs.) ", "Okay, ", "move ", "along."],
            NarrationHandlingEnum.USE_NARRATOR,
            None,
            None,
            ["The guard sighs.", "Okay, move along.", ""],
            [SentenceTypeEnum.NARRATION, SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH],
        ),
        # Cut narration
        (
            ["(The ", "guard ", "sighs.) ", "Okay, ", "move ", "along."],
            NarrationHandlingEnum.CUT_NARRATIONS,
            None,
            None,
            ["Okay, move along.", ""],
            [SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH],
        ),
        # Speech mixed with narration
        (
            ["Hello ", "there. ", "(He ", "nods.) ", "Need ", "something?"],
            NarrationHandlingEnum.USE_NARRATOR,
            None,
            None,
            ["Hello there.", "He nods.", "Need something?", ""],
            [SentenceTypeEnum.SPEECH, SentenceTypeEnum.NARRATION, SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH],
        ),
        # Partial words + limit to 1 sentence
        (
            ["Parti", "al ", "sent", "ence.", " Next."],
            NarrationHandlingEnum.CUT_NARRATIONS,
            1,
            1,
            ["Partial sentence.", ""],
            [SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH],
        ),
        # Short sentences merging: two one-word sentences, min_words=2 should merge
        (
            ["Hi.", " Yo."] ,
            NarrationHandlingEnum.CUT_NARRATIONS,
            None,
            2,
            ["Hi. Yo.", ""],
            [SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH],
        ),
        # Non-western punctuation
        (
            ["你好！", "再见。"],
            NarrationHandlingEnum.CUT_NARRATIONS,
            None,
            None,
            ["你好！再见。", ""],
            [SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH],
        ),
        # Character change parser: change speaker and speak
        (
            ["Guard: ", "Watch ", "out!"] ,
            NarrationHandlingEnum.CUT_NARRATIONS,
            None,
            None,
            ["Watch out!", ""],
            [SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH],
        ),
        # Action embedded in response
        (
            ["Wave: ", "See ", "you ", "later."] ,
            NarrationHandlingEnum.CUT_NARRATIONS,
            None,
            None,
            ["See you later.", ""],
            [SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH],
        ),
        # Limit sentences: only two allowed
        (
            ["One.", " Two.", " Three."] ,
            NarrationHandlingEnum.CUT_NARRATIONS,
            2,
            1,
            ["One.", "Two.", ""],
            [SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH],
        ),
        # Clean parser removes 'As a XYZ,' prefix
        (
            ["As a hunter, I hunt.", " Thanks."] ,
            NarrationHandlingEnum.CUT_NARRATIONS,
            None,
            1,
            ["I hunt.", "Thanks.", ""],
            [SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH],
        ),
        # Handle LLMs which output whole sentences as tokens
        (
            [
                "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. ", 
                "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. ", 
                "Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. ", 
                "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum."
            ] ,
            NarrationHandlingEnum.CUT_NARRATIONS,
            None,
            1,
            [
                "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.", 
                "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.", 
                "Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur.", 
                "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.", 
                ""
            ],
            [SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH, SentenceTypeEnum.SPEECH],
        ),
    ],
)
async def test_process_response_param(
    output_manager: ChatManager,
    example_skyrim_npc_character: Character,
    example_characters_pc_to_npc: Characters,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
    response_pattern,
    narration_handling,
    max_sentences,
    min_words,
    expected_texts,
    expected_types,
):
    client = output_manager._ChatManager__client
    config = output_manager._ChatManager__config

    client.response_pattern = response_pattern
    config.narration_handling = narration_handling
    if max_sentences is not None:
        config.max_response_sentences_single = max_sentences
    if min_words is not None:
        config.number_words_tts = min_words

    await output_manager.process_response(
        example_skyrim_npc_character,
        mock_queue,
        mock_messages,
        example_characters_pc_to_npc,
        mock_actions,
    )

    actual = []
    actual_types = []
    while True:
        sent = mock_queue.get_next_sentence()
        if not sent:
            break
        actual.append(sent.content.text.strip())
        actual_types.append(sent.content.sentence_type)

    assert actual == expected_texts
    assert actual_types == expected_types


def _spoken_texts_from_queue(queue: SentenceQueue) -> list[str]:
    return [sentence.content.text.strip() for sentence in get_sentence_list_from_queue(queue)]


@pytest.mark.asyncio
async def test_drop_last_sentence_single_npc(
    output_manager: ChatManager,
    example_skyrim_npc_character: Character,
    example_characters_pc_to_npc: Characters,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
):
    client = output_manager._ChatManager__client
    config = output_manager._ChatManager__config
    client.response_pattern = ["One. ", "Two. ", "Three."]
    config.number_words_tts = 1
    config.drop_last_sentences_single = 1

    await output_manager.process_response(
        example_skyrim_npc_character,
        mock_queue,
        mock_messages,
        example_characters_pc_to_npc,
        mock_actions,
    )

    assert _spoken_texts_from_queue(mock_queue) == ["One.", "Two.", ""]


@pytest.mark.asyncio
async def test_drop_last_sentence_keeps_at_least_one(
    output_manager: ChatManager,
    example_skyrim_npc_character: Character,
    example_characters_pc_to_npc: Characters,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
):
    client = output_manager._ChatManager__client
    config = output_manager._ChatManager__config
    client.response_pattern = ["Only one sentence."]
    config.number_words_tts = 1
    config.drop_last_sentences_single = 1

    await output_manager.process_response(
        example_skyrim_npc_character,
        mock_queue,
        mock_messages,
        example_characters_pc_to_npc,
        mock_actions,
    )

    assert _spoken_texts_from_queue(mock_queue) == ["Only one sentence.", ""]


@pytest.mark.asyncio
async def test_drop_last_sentences_keeps_one_when_drop_exceeds_count(
    output_manager: ChatManager,
    example_skyrim_npc_character: Character,
    example_characters_pc_to_npc: Characters,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
):
    client = output_manager._ChatManager__client
    config = output_manager._ChatManager__config
    client.response_pattern = ["One. ", "Two."]
    config.number_words_tts = 1
    config.drop_last_sentences_single = 3

    await output_manager.process_response(
        example_skyrim_npc_character,
        mock_queue,
        mock_messages,
        example_characters_pc_to_npc,
        mock_actions,
    )

    assert _spoken_texts_from_queue(mock_queue) == ["One.", ""]


@pytest.mark.asyncio
async def test_drop_last_sentence_also_drops_its_action(
    output_manager: ChatManager,
    example_skyrim_npc_character: Character,
    example_characters_pc_to_npc: Characters,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
):
    client = output_manager._ChatManager__client
    config = output_manager._ChatManager__config
    client.response_pattern = ["Hello there. ", "Wave: See you later."]
    config.number_words_tts = 1
    config.drop_last_sentences_single = 1

    await output_manager.process_response(
        example_skyrim_npc_character,
        mock_queue,
        mock_messages,
        example_characters_pc_to_npc,
        mock_actions,
    )

    output_sentences = get_sentence_list_from_queue(mock_queue)
    assert [sentence.content.text.strip() for sentence in output_sentences] == ["Hello there.", ""]
    assert not output_sentences[0].content.actions


@pytest.mark.asyncio
async def test_drop_last_sentences_does_not_apply_to_multi_npc(
    output_manager: ChatManager,
    example_skyrim_player_character: Character,
    example_skyrim_npc_character: Character,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
):
    second_npc = Character(
        base_id="1",
        ref_id="1",
        name="Lydia",
        gender=1,
        race=example_skyrim_npc_character.race,
        is_player_character=False,
        bio="You are Lydia.",
        is_in_combat=False,
        is_enemy=False,
        relationship_rank=0,
        is_generic_npc=True,
        ingame_voice_model="FemaleEvenToned",
        tts_voice_model="FemaleEvenToned",
        csv_in_game_voice_model="FemaleEvenToned",
        advanced_voice_model="FemaleEvenToned",
        voice_accent="en",
        equipment=None,
        custom_character_values=None,
    )
    characters = Characters()
    characters.add_or_update_character(example_skyrim_player_character)
    characters.add_or_update_character(example_skyrim_npc_character)
    characters.add_or_update_character(second_npc)

    client = output_manager._ChatManager__client
    config = output_manager._ChatManager__config
    client.response_pattern = ["One. ", "Two. ", "Three."]
    config.number_words_tts = 1
    config.drop_last_sentences_single = 1

    await output_manager.process_response(
        example_skyrim_npc_character,
        mock_queue,
        mock_messages,
        characters,
        mock_actions,
    )

    assert _spoken_texts_from_queue(mock_queue) == ["One.", "Two.", "Three.", ""]


@pytest.mark.asyncio
async def test_drop_last_sentences_applies_to_game_master_reply(
    output_manager: ChatManager,
    example_skyrim_player_character: Character,
    example_skyrim_npc_character: Character,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
):
    second_npc = Character(
        base_id="1",
        ref_id="1",
        name="Lydia",
        gender=1,
        race=example_skyrim_npc_character.race,
        is_player_character=False,
        bio="You are Lydia.",
        is_in_combat=False,
        is_enemy=False,
        relationship_rank=0,
        is_generic_npc=True,
        ingame_voice_model="FemaleEvenToned",
        tts_voice_model="FemaleEvenToned",
        csv_in_game_voice_model="FemaleEvenToned",
        advanced_voice_model="FemaleEvenToned",
        voice_accent="en",
        equipment=None,
        custom_character_values=None,
    )
    characters = Characters()
    characters.add_or_update_character(example_skyrim_player_character)
    characters.add_or_update_character(example_skyrim_npc_character)
    characters.add_or_update_character(second_npc)

    client = output_manager._ChatManager__client
    config = output_manager._ChatManager__config
    client.response_pattern = ["One. ", "Two. ", "Three."]
    config.number_words_tts = 1
    config.drop_last_sentences_single = 1

    # Game Master Mode locks the reply to one speaker
    await output_manager.process_response(
        example_skyrim_npc_character,
        mock_queue,
        mock_messages,
        characters,
        mock_actions,
        lock_speaker=True,
    )

    assert _spoken_texts_from_queue(mock_queue) == ["One.", "Two.", ""]


def _enable_sequential_one_on_one(config: ConfigLoader, models: list[str]):
    config.sequential_llm_one_on_one_per_request_enabled = True
    config.sequential_llm_pool_one_on_one = [
        {"service": "OpenRouter", "model": model} for model in models
    ]
    config.apply_profile_one_on_one = False


def _patch_pool_clients(output_manager: ChatManager, monkeypatch, clients_by_model: dict[str, MockAIClient]):
    created: list[str] = []

    def fake_create(selection):
        created.append(selection.model)
        client = clients_by_model[selection.model]
        client.model_name = selection.model
        return client

    monkeypatch.setattr(output_manager, "_get_or_create_random_client", fake_create)
    return created


@pytest.mark.asyncio
async def test_sequential_pool_uses_models_in_order(
    output_manager: ChatManager,
    example_skyrim_npc_character: Character,
    example_characters_pc_to_npc: Characters,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
    monkeypatch,
):
    config = output_manager._ChatManager__config
    _enable_sequential_one_on_one(config, ["model-a", "model-b"])
    created = _patch_pool_clients(
        output_manager,
        monkeypatch,
        {
            "model-a": MockAIClient(response_pattern=["From A."]),
            "model-b": MockAIClient(response_pattern=["From B."]),
        },
    )

    await output_manager.process_response(
        example_skyrim_npc_character, mock_queue, mock_messages, example_characters_pc_to_npc, mock_actions
    )
    first = _spoken_texts_from_queue(mock_queue)
    await output_manager.process_response(
        example_skyrim_npc_character, mock_queue, mock_messages, example_characters_pc_to_npc, mock_actions
    )
    second = _spoken_texts_from_queue(mock_queue)

    assert created == ["model-a", "model-b"]
    assert "From A." in first
    assert "From B." in second


@pytest.mark.asyncio
async def test_sequential_pool_skips_to_next_on_failure(
    output_manager: ChatManager,
    example_skyrim_npc_character: Character,
    example_characters_pc_to_npc: Characters,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
    monkeypatch,
):
    monkeypatch.setattr("src.utils.play_error_sound", lambda *a, **kw: None)
    monkeypatch.setattr(time, "sleep", lambda *a, **kw: None)
    config = output_manager._ChatManager__config
    _enable_sequential_one_on_one(config, ["model-a", "model-b"])
    created = _patch_pool_clients(
        output_manager,
        monkeypatch,
        {
            "model-a": MockAIClient(error_on_call=True),
            "model-b": MockAIClient(response_pattern=["From B."]),
        },
    )

    await output_manager.process_response(
        example_skyrim_npc_character, mock_queue, mock_messages, example_characters_pc_to_npc, mock_actions
    )
    spoken = _spoken_texts_from_queue(mock_queue)

    assert created == ["model-a", "model-b"]
    assert "From B." in spoken
    assert not any("I can't find the right words" in text for text in spoken)


@pytest.mark.asyncio
async def test_sequential_pool_takes_priority_over_random(
    output_manager: ChatManager,
    example_skyrim_npc_character: Character,
    example_characters_pc_to_npc: Characters,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
    monkeypatch,
):
    config = output_manager._ChatManager__config
    _enable_sequential_one_on_one(config, ["model-seq"])
    config.random_llm_one_on_one_per_request_enabled = True
    config.llm_pool_one_on_one = [{"service": "OpenRouter", "model": "model-random"}]
    created = _patch_pool_clients(
        output_manager,
        monkeypatch,
        {
            "model-seq": MockAIClient(response_pattern=["From sequential."]),
            "model-random": MockAIClient(response_pattern=["From random."]),
        },
    )

    await output_manager.process_response(
        example_skyrim_npc_character, mock_queue, mock_messages, example_characters_pc_to_npc, mock_actions
    )
    spoken = _spoken_texts_from_queue(mock_queue)

    assert created == ["model-seq"]
    assert "From sequential." in spoken
    assert "From random." not in spoken


def test_pool_selection_does_not_reuse_client_that_only_matches_config_default(output_manager: ChatManager, monkeypatch):
    """Per-conversation random can replace __client; config.ini matching must not reuse it."""
    from src.random_llm_selector import LLMSelection

    config = output_manager._ChatManager__config
    config.llm_api = "OpenRouter"
    config.llm = "config-default"
    config.llm_params = {}

    live_client = MockAIClient()
    live_client.service_name = "OpenRouter"
    live_client.model_name = "per-conversation-random"
    live_client._request_params = {}
    output_manager._ChatManager__client = live_client

    created: list[str] = []

    def fake_create(selection):
        created.append(selection.model)
        replacement = MockAIClient()
        replacement.service_name = selection.service
        replacement.model_name = selection.model
        return replacement

    monkeypatch.setattr(output_manager, "_get_or_create_random_client", fake_create)

    selection = LLMSelection(
        service="OpenRouter",
        model="config-default",
        parameters={},
        token_count=2048,
        from_profile=False,
    )
    chosen = output_manager._get_client_for_pool_selection(selection, False)

    assert created == ["config-default"]
    assert chosen is not live_client
    assert chosen.model_name == "config-default"


def test_pool_selection_reuses_live_client_when_it_is_the_selected_model(output_manager: ChatManager, monkeypatch):
    from src.random_llm_selector import LLMSelection

    live_client = MockAIClient()
    live_client.service_name = "OpenRouter"
    live_client.model_name = "pool-model"
    live_client._request_params = {"temperature": 0.7}
    output_manager._ChatManager__client = live_client

    def fail_if_created(selection):
        raise AssertionError(f"Should reuse live client, not create {selection.model}")

    monkeypatch.setattr(output_manager, "_get_or_create_random_client", fail_if_created)

    selection = LLMSelection(
        service="OpenRouter",
        model="pool-model",
        parameters={"temperature": 0.7},
        token_count=2048,
        from_profile=False,
    )
    chosen = output_manager._get_client_for_pool_selection(selection, False)

    assert chosen is live_client


def test_pool_selection_does_not_reuse_live_client_when_params_differ(output_manager: ChatManager, monkeypatch):
    from src.random_llm_selector import LLMSelection

    live_client = MockAIClient()
    live_client.service_name = "OpenRouter"
    live_client.model_name = "pool-model"
    live_client._request_params = {}
    output_manager._ChatManager__client = live_client

    created: list[str] = []

    def fake_create(selection):
        created.append(selection.model)
        replacement = MockAIClient()
        replacement.model_name = selection.model
        return replacement

    monkeypatch.setattr(output_manager, "_get_or_create_random_client", fake_create)

    selection = LLMSelection(
        service="OpenRouter",
        model="pool-model",
        parameters={"temperature": 0.9},
        token_count=2048,
        from_profile=True,
    )
    chosen = output_manager._get_client_for_pool_selection(selection, False)

    assert created == ["pool-model"]
    assert chosen is not live_client



class ProviderErrorClient(MockAIClient):
    """The LLM service rejects every request before any text"""
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def streaming_call(self, messages=None, is_multi_npc=False, response_started_at=None):
        from src.llm.client_base import ProviderError
        self.calls += 1
        raise ProviderError("An error occurred during streaming")
        yield


def _enable_fallback(output_manager: ChatManager, monkeypatch, fallback: MockAIClient) -> list[str]:
    config = output_manager._ChatManager__config
    config.fallback_llm_enabled = True
    config.apply_profile_fallback = False
    config.fallback_llm_api = "OpenRouter"
    config.fallback_llm = "fallback-model"
    return _patch_pool_clients(output_manager, monkeypatch, {"fallback-model": fallback})


@pytest.mark.asyncio
async def test_fallback_model_answers_when_the_service_rejects_the_reply(
    output_manager: ChatManager,
    example_skyrim_npc_character: Character,
    example_characters_pc_to_npc: Characters,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
    monkeypatch,
):
    output_manager._ChatManager__client = ProviderErrorClient()
    created = _enable_fallback(output_manager, monkeypatch, MockAIClient(response_pattern=["From fallback."]))

    await output_manager.process_response(
        example_skyrim_npc_character, mock_queue, mock_messages, example_characters_pc_to_npc, mock_actions
    )

    assert created == ["fallback-model"]
    assert _spoken_texts_from_queue(mock_queue) == ["From fallback.", ""]


@pytest.mark.asyncio
async def test_fallback_model_is_asked_only_once(
    output_manager: ChatManager,
    example_skyrim_npc_character: Character,
    example_characters_pc_to_npc: Characters,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
    monkeypatch,
):
    output_manager._ChatManager__client = ProviderErrorClient()
    fallback = ProviderErrorClient()
    _enable_fallback(output_manager, monkeypatch, fallback)

    await output_manager.process_response(
        example_skyrim_npc_character, mock_queue, mock_messages, example_characters_pc_to_npc, mock_actions
    )

    assert fallback.calls == 1
    assert _spoken_texts_from_queue(mock_queue) == [""]


@pytest.mark.asyncio
async def test_rejected_reply_stays_empty_without_fallback(
    output_manager: ChatManager,
    example_skyrim_npc_character: Character,
    example_characters_pc_to_npc: Characters,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
):
    client = ProviderErrorClient()
    output_manager._ChatManager__client = client
    output_manager._ChatManager__config.fallback_llm_enabled = False

    await output_manager.process_response(
        example_skyrim_npc_character, mock_queue, mock_messages, example_characters_pc_to_npc, mock_actions
    )

    assert client.calls == 1
    assert _spoken_texts_from_queue(mock_queue) == [""]


class NotAnsweringClient(MockAIClient):
    """The LLM takes long to send its first token"""
    async def streaming_call(self, messages=None, is_multi_npc=False, response_started_at=None):
        await asyncio.sleep(30)
        yield "Too late."


@pytest.mark.asyncio
async def test_stopping_does_not_wait_for_the_llm_to_answer(
    output_manager: ChatManager,
    example_skyrim_npc_character: Character,
    example_characters_pc_to_npc: Characters,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
):
    output_manager._ChatManager__client = NotAnsweringClient()
    response = asyncio.create_task(output_manager.process_response(
        example_skyrim_npc_character, mock_queue, mock_messages, example_characters_pc_to_npc, mock_actions
    ))
    await asyncio.sleep(0.1)
    output_manager._ChatManager__stop_generation.set()

    await asyncio.wait_for(response, timeout=2)

    assert _spoken_texts_from_queue(mock_queue) == [""]
