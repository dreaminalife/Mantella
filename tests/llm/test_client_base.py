from src.llm.client_base import ClientBase
from src.llm.llm_model_list import LLMModelList
import pytest
from src.config.config_loader import ConfigLoader
import logging
from pathlib import Path
from unittest.mock import patch
import pytest
import time

def test_key_found_in_mod_parent(tmp_path, monkeypatch):
    """Test finding API key in mod parent folder"""
    # Setup mock mod_parent_folder structure
    mock_mod_path = tmp_path / "a" / "b" / "c"
    mock_mod_path.mkdir(parents=True)
    monkeypatch.setattr('src.utils.resolve_path', lambda: mock_mod_path)
    
    key_file = "test_key.txt"
    (tmp_path / key_file).write_text("sk-123")
    
    assert ClientBase._get_api_key([key_file], False) == "sk-123"


def test_key_found_locally(tmp_path, monkeypatch):
    """Test finding API key in local directory"""
    # Setup empty mod_parent_folder
    mock_mod_path = tmp_path / "empty" / "b" / "c"
    mock_mod_path.mkdir(parents=True)
    monkeypatch.setattr('src.utils.resolve_path', lambda: mock_mod_path)
    
    # Create key in current working directory
    key_file = "local_key.txt"
    (tmp_path / key_file).write_text("sk-456")
    
    assert ClientBase._get_api_key([key_file], False) == "sk-456"


def test_key_not_found(tmp_path, monkeypatch, caplog):
    """Test missing key file handling"""
    mock_mod_path = tmp_path / "a" / "b" / "c"
    mock_mod_path.mkdir(parents=True)
    monkeypatch.setattr('src.utils.resolve_path', lambda: mock_mod_path)
    
    result = ClientBase._get_api_key(["missing.txt"], False)
    
    assert result is None


def test_string_argument(tmp_path, monkeypatch):
    """Test string instead of list argument (strings should be converted to lists in the function itself)"""
    mock_mod_path = tmp_path / "a" / "b" / "c"
    mock_mod_path.mkdir(parents=True)
    monkeypatch.setattr('src.utils.resolve_path', lambda: mock_mod_path)
    
    key_file = "single_key.txt"
    (tmp_path / key_file).write_text("sk-789")
    
    assert ClientBase._get_api_key(key_file, False) == "sk-789"


def test_empty_key_file(tmp_path, monkeypatch):
    """Test empty key file skips to next candidate"""
    mock_mod_path = tmp_path / "a" / "b" / "c"
    mock_mod_path.mkdir(parents=True)
    monkeypatch.setattr('src.utils.resolve_path', lambda: mock_mod_path)
    
    # Create empty key file
    empty_key = "empty.txt"
    (tmp_path / empty_key).write_text("  \n")
    
    # Create valid local key
    valid_key = "valid.txt"
    (tmp_path / valid_key).write_text("sk-abc")
    
    assert ClientBase._get_api_key([empty_key, valid_key], False) == "sk-abc"


def test_key_priority(tmp_path, monkeypatch):
    """Test that the first key in the list is prioritized"""
    mock_mod_path = tmp_path / "a" / "b" / "c"
    mock_mod_path.mkdir(parents=True)
    monkeypatch.setattr('src.utils.resolve_path', lambda: mock_mod_path)
    
    first_key = "first.txt"
    (tmp_path / first_key).write_text("sk-abc")
    
    second_key = "second.txt"
    (tmp_path / second_key).write_text("sk-def")
    
    assert ClientBase._get_api_key([first_key, second_key], False) == "sk-abc"


def test_unknown_service_returns_custom():
    result = ClientBase.get_model_list("Unknown", "key.txt")
    assert result.available_models == [("Custom model", "Custom model")]
    assert result.allows_manual_model_input is True


def test_openai_happy_path():
    """Test OpenAI service with successful model fetch"""
    result = ClientBase.get_model_list("OpenAI", "key.txt")
    assert any("gpt-4" in opt[1] for opt in result.available_models)
    assert result.allows_manual_model_input is True


@patch('src.llm.client_base.ClientBase._get_api_key')
def test_openrouter_missing_key(mock_get_key):
    """Test OpenRouter with missing API key"""
    mock_get_key.return_value = None
    result = ClientBase.get_model_list("OpenRouter", "missing.txt")
    assert "No secret key found" in result.available_models[0][0]


def test_openrouter_success():
    """Test successful OpenRouter model list retrieval"""
    result = ClientBase.get_model_list("OpenRouter", "key.txt")
    assert any("google/gemma-2-9b-it:free" in opt[1] for opt in result.available_models)
    assert result.allows_manual_model_input is False


def test_nanogpt_success():
    """Test successful NanoGPT model list retrieval"""
    result = ClientBase.get_model_list("NanoGPT", "key.txt", "gpt-4o-mini")
    assert result.default_model == "gpt-4o-mini"
    assert result.allows_manual_model_input is True  # NanoGPT allows manual input


def test_is_vision_filtering():
    """Test vision model filtering"""
    result = ClientBase.get_model_list("OpenRouter", "key.txt", is_vision=True)
    assert all("Vision Available" in opt[0] for opt in result.available_models)


def test_the_vision_list_keeps_every_model_that_reads_images_also_those_taking_more():
    """Gemini models also take files, audio and video. They read images all the same, so they are in the Custom Vision Model list"""
    def model(model_id, modality, inputs=None):
        architecture = {"modality": modality}
        if inputs is not None:
            architecture["input_modalities"] = inputs
        return type("Model", (), {"id": model_id, "model_extra": {"context_length": 1000, "pricing": {"prompt": "0", "completion": "0"},
                                                                  "architecture": architecture}})()
    models = type("Page", (), {"data": [
        model("deepseek/deepseek-v4.1-flash", "text+image->text", ["text", "image"]),
        model("google/gemini-3.1-flash-lite", "text+image+file+audio+video->text", ["text", "image", "video", "file", "audio"]),
        model("old/listed-without-inputs", "text+image+audio->text"),
        model("text/only", "text->text", ["text"]),
        model("draws/pictures", "text->text+image", ["text"]),
    ]})()
    with patch("src.llm.client_base.ClientBase._get_api_key", return_value="sk-test"), \
            patch("src.llm.client_base.OpenAI") as openai:
        openai.return_value.models.list.return_value = models
        result = ClientBase.get_model_list("OpenRouter", "key.txt", is_vision=True)
    assert [model_id for _, model_id in result.available_models] == \
        ["deepseek/deepseek-v4.1-flash", "google/gemini-3.1-flash-lite", "old/listed-without-inputs"]


def test_response_timeout_keeps_five_second_connect_limit():
    client = ClientBase(
        "http://127.0.0.1:5001/v1",
        "local-model",
        {"timeout": 30, "max_tokens": 10},
        4096,
        ["missing.txt"],
        response_timeout=120,
    )
    timeout = client._http_timeout()
    assert timeout.connect == 5.0
    assert timeout.read == 120
    assert timeout.write == 600.0
    assert timeout.pool == 600.0

    params = client._completion_request_params(client._request_params)
    assert "timeout" not in params
    assert params["max_tokens"] == 10
    assert client._request_params["timeout"] == 30


def test_first_token_deadline_rejects_a_late_reply():
    client = ClientBase(
        "http://127.0.0.1:5001/v1",
        "local-model",
        {"max_tokens": 10},
        4096,
        ["missing.txt"],
        response_timeout=10,
    )
    from src.llm.client_base import FirstTokenTimeout
    try:
        client._raise_if_first_token_late(time.time() - 1)
        raised = False
    except FirstTokenTimeout:
        raised = True
    assert raised


def test_streaming_call_retries_a_connect_timeout(default_config: ConfigLoader):
    import asyncio
    import httpx
    from types import SimpleNamespace
    from openai import APITimeoutError
    from src.llm.messages import UserMessage
    client = ClientBase(
        "http://127.0.0.1:5001/v1",
        "local-model",
        {"max_tokens": 10},
        4096,
        ["missing.txt"],
        response_timeout=10,
    )

    async def chunks():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello"))])

    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            request = httpx.Request("POST", "http://127.0.0.1:5001/v1/chat/completions")
            try:
                raise httpx.ConnectTimeout("handshake timed out")
            except httpx.ConnectTimeout as e:
                raise APITimeoutError(request=request) from e
        return chunks()

    async def close():
        pass

    stream_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    async_client = SimpleNamespace(with_options=lambda **kwargs: stream_client, close=close)

    async def collect():
        return [c async for c in client.streaming_call(UserMessage(default_config, "Hi"), False)]

    with patch.object(ClientBase, "generate_async_client", return_value=async_client):
        assert asyncio.run(collect()) == ["Hello"]
    assert len(calls) == 2


def test_hot_swap_updates_response_timeout_only():
    client = ClientBase(
        "http://127.0.0.1:5001/v1",
        "local-model",
        {"max_tokens": 10},
        4096,
        ["missing.txt"],
        response_timeout=120,
    )
    assert client.hot_swap_settings(
        "http://127.0.0.1:5001/v1",
        "local-model",
        {"max_tokens": 10},
        4096,
        ["missing.txt"],
        response_timeout=45,
    )
    timeout = client._http_timeout()
    assert timeout.read == 45
    assert timeout.connect == 5.0


def test_config_loads_llm_response_timeout_default(default_config: ConfigLoader):
    assert default_config.llm_response_timeout == 120

def test_format_openai_messages_is_readable():
    from src.llm.client_base import format_openai_messages
    messages = [
        {"role": "system", "content": "You are Lydia.\nBe brief."},
        {"role": "user", "content": [{"type": "text", "text": "Look at this."}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]},
    ]
    assert format_openai_messages(messages) == "\n".join([
        "===== system =====",
        "You are Lydia.",
        "Be brief.",
        "",
        "===== user =====",
        "Look at this.",
        "[image_url]",
    ])


def test_request_call_logs_request_and_response(caplog):
    from unittest.mock import MagicMock
    from src.llm.messages import UserMessage
    client = ClientBase("http://127.0.0.1:5001/v1", "local-model", {"max_tokens": 10}, 4096, ["missing.txt"])
    sync_client = MagicMock()
    sync_client.chat.completions.create.return_value.choices = [MagicMock()]
    sync_client.chat.completions.create.return_value.choices[0].message.content = "Hello there."
    message = MagicMock(spec=UserMessage)
    message.get_openai_message.return_value = {"role": "user", "content": "Hi Lydia."}
    with patch.object(client, "generate_sync_client", return_value=sync_client), caplog.at_level(15): # The full request and response are only logged in debug mode
        assert client.request_call(message) == "Hello there."
    assert "LLM request to local-model:\n===== user =====\nHi Lydia." in caplog.text
    assert "LLM response from local-model:\nHello there." in caplog.text


def _stream_client_with_error(chunks_before_error: list[str]):
    """A fake async client whose stream yields the given text and then an error sent inside the stream"""
    import httpx
    from types import SimpleNamespace
    from openai import APIError

    async def chunks():
        for text in chunks_before_error:
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])
        raise APIError("An error occurred during streaming", httpx.Request("POST", "http://127.0.0.1:5001/v1/chat/completions"), body={"message": "PROHIBITED_CONTENT"})

    async def create(**kwargs):
        return chunks()

    async def close():
        pass

    stream_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return SimpleNamespace(with_options=lambda **kwargs: stream_client, close=close)


def test_streaming_call_raises_provider_error_before_any_text(default_config: ConfigLoader, caplog, monkeypatch):
    import asyncio
    from src.llm.client_base import ProviderError
    from src.llm.messages import UserMessage
    monkeypatch.setattr("src.utils.play_error_sound", lambda *a, **kw: None)
    client = ClientBase("http://127.0.0.1:5001/v1", "local-model", {"max_tokens": 10}, 4096, ["missing.txt"])

    async def collect():
        return [c async for c in client.streaming_call(UserMessage(default_config, "Hi"), False)]

    with patch.object(ClientBase, "generate_async_client", return_value=_stream_client_with_error([])):
        with pytest.raises(ProviderError):
            asyncio.run(collect())
    assert "PROHIBITED_CONTENT" in caplog.text


def test_streaming_call_keeps_text_when_the_error_comes_after_it(default_config: ConfigLoader, monkeypatch):
    import asyncio
    from src.llm.messages import UserMessage
    monkeypatch.setattr("src.utils.play_error_sound", lambda *a, **kw: None)
    client = ClientBase("http://127.0.0.1:5001/v1", "local-model", {"max_tokens": 10}, 4096, ["missing.txt"])

    async def collect():
        return [c async for c in client.streaming_call(UserMessage(default_config, "Hi"), False)]

    with patch.object(ClientBase, "generate_async_client", return_value=_stream_client_with_error(["Hello"])):
        assert asyncio.run(collect()) == ["Hello"]


BRAILLE_REPLY = "Hello⠀there.⠀How are you?"


def test_clean_response_text_leaves_braille_blanks_when_disabled(monkeypatch):
    monkeypatch.setattr(ClientBase, "replace_braille_blank", False)
    assert ClientBase.clean_response_text(BRAILLE_REPLY) == BRAILLE_REPLY


def test_clean_response_text_replaces_braille_blanks_when_enabled(monkeypatch):
    monkeypatch.setattr(ClientBase, "replace_braille_blank", True)
    assert ClientBase.clean_response_text(BRAILLE_REPLY) == "Hello there. How are you?"
    assert ClientBase.clean_response_text("No blanks here.") == "No blanks here."
    assert ClientBase.clean_response_text("⠀⠀") == "  "


def _stream_client(chunks_text: list[str]):
    """A fake async client whose stream yields the given text chunks"""
    from types import SimpleNamespace

    async def chunks():
        for text in chunks_text:
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])

    async def create(**kwargs):
        return chunks()

    async def close():
        pass

    stream_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return SimpleNamespace(with_options=lambda **kwargs: stream_client, close=close)


@pytest.mark.parametrize("enabled, expected", [
    (True, ["Hello there.", " How", " are you?"]),
    (False, ["Hello⠀there.", "⠀How", "⠀are you?"]),
])
def test_streaming_call_replaces_braille_blanks_only_when_enabled(default_config: ConfigLoader, monkeypatch, enabled, expected):
    """The stream is cleaned before it reaches sentence splitting and text-to-speech"""
    import asyncio
    from src.llm.messages import UserMessage
    monkeypatch.setattr(ClientBase, "replace_braille_blank", enabled)
    client = ClientBase("http://127.0.0.1:5001/v1", "local-model", {"max_tokens": 10}, 4096, ["missing.txt"])

    async def collect():
        return [c async for c in client.streaming_call(UserMessage(default_config, "Hi"), False)]

    with patch.object(ClientBase, "generate_async_client", return_value=_stream_client(["Hello⠀there.", "⠀How", "⠀are you?"])):
        assert asyncio.run(collect()) == expected


@pytest.mark.parametrize("enabled, expected", [
    (True, "Hello there. How are you?"),
    (False, BRAILLE_REPLY),
])
def test_request_call_replaces_braille_blanks_only_when_enabled(monkeypatch, enabled, expected):
    from unittest.mock import MagicMock
    from src.llm.messages import UserMessage
    monkeypatch.setattr(ClientBase, "replace_braille_blank", enabled)
    client = ClientBase("http://127.0.0.1:5001/v1", "local-model", {"max_tokens": 10}, 4096, ["missing.txt"])
    sync_client = MagicMock()
    sync_client.chat.completions.create.return_value.choices = [MagicMock()]
    sync_client.chat.completions.create.return_value.choices[0].message.content = BRAILLE_REPLY
    message = MagicMock(spec=UserMessage)
    message.get_openai_message.return_value = {"role": "user", "content": "Hi Lydia."}
    with patch.object(client, "generate_sync_client", return_value=sync_client):
        assert client.request_call(message) == expected
