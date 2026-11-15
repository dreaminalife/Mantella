from src.llm.sonnet_cache_connector import SonnetCacheConnector


def _messages(turn_count: int):
    messages = [{"role": "system", "content": "stable system prompt"}]
    for turn in range(1, turn_count + 1):
        messages.append({"role": "user", "content": f"user {turn}"})
        if turn != turn_count:
            messages.append({"role": "assistant", "content": f"assistant {turn}"})
    return messages


def _cached_texts(messages):
    result = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if any(part.get("cache_control") for part in content if isinstance(part, dict)):
            result.append(content[0]["text"])
    return result


def test_transform_messages_marks_system_on_first_request():
    connector = SonnetCacheConnector(True)

    transformed = connector.transform_messages(_messages(1))

    assert _cached_texts(transformed) == ["stable system prompt"]


def test_transform_messages_keeps_previous_breakpoint_on_third_request():
    connector = SonnetCacheConnector(True)

    second_request = connector.transform_messages(_messages(2))
    third_request = connector.transform_messages(_messages(3))

    assert _cached_texts(second_request) == ["stable system prompt", "user 1"]
    assert _cached_texts(third_request) == ["stable system prompt", "user 1", "user 2"]


def test_transform_messages_caps_breakpoints_to_system_and_three_recent_users():
    connector = SonnetCacheConnector(True)

    transformed = connector.transform_messages(_messages(5))

    assert _cached_texts(transformed) == [
        "stable system prompt",
        "user 2",
        "user 3",
        "user 4",
    ]


def test_transform_messages_keeps_non_breakpoint_messages_normalized():
    connector = SonnetCacheConnector(True)

    transformed = connector.transform_messages(_messages(5))

    assert transformed[1]["content"] == [{"type": "text", "text": "user 1"}]


def test_get_cache_target_indices_for_gemini_keeps_system_as_final_breakpoint():
    connector = SonnetCacheConnector(True)

    indices = connector._get_cache_target_indices(
        _messages(5),
        model_name="google/gemini-2.5-pro",
    )

    assert indices == [3, 5, 7, 0]
