from types import SimpleNamespace

from src.sequential_llm_selector import SequentialLLMSelector


def _config(one_on_one=None, multi_npc=None, apply_one_on_one=False, apply_multi_npc=False):
    return SimpleNamespace(
        sequential_llm_pool_one_on_one=one_on_one or [],
        sequential_llm_pool_multi_npc=multi_npc or [],
        apply_profile_one_on_one=apply_one_on_one,
        apply_profile_multi_npc=apply_multi_npc,
    )


def _fallback():
    return {
        "fallback_service": "OpenRouter",
        "fallback_model": "fallback/model",
        "fallback_params": {},
        "fallback_token_count": 2048,
    }


def test_selects_models_in_listed_order():
    selector = SequentialLLMSelector()
    config = _config(one_on_one=[
        {"service": "OpenRouter", "model": "model-a"},
        {"service": "OpenRouter", "model": "model-b"},
        {"service": "OpenAI", "model": "model-c"},
    ])

    first = selector.select_next_from_one_on_one_pool(config, **_fallback())
    second = selector.select_next_from_one_on_one_pool(config, **_fallback())
    third = selector.select_next_from_one_on_one_pool(config, **_fallback())

    assert first.model == "model-a"
    assert second.model == "model-b"
    assert third.model == "model-c"


def test_wraps_around_after_last_model():
    selector = SequentialLLMSelector()
    config = _config(one_on_one=[
        {"service": "OpenRouter", "model": "model-a"},
        {"service": "OpenRouter", "model": "model-b"},
    ])

    selector.select_next_from_one_on_one_pool(config, **_fallback())
    selector.select_next_from_one_on_one_pool(config, **_fallback())
    wrapped = selector.select_next_from_one_on_one_pool(config, **_fallback())

    assert wrapped.model == "model-a"


def test_skips_invalid_entries_and_continues():
    selector = SequentialLLMSelector()
    config = _config(one_on_one=[
        {"service": "OpenRouter", "model": "model-a"},
        {"not_a_model": True},
        {"service": "OpenRouter", "model": "model-b"},
    ])

    first = selector.select_next_from_one_on_one_pool(config, **_fallback())
    second = selector.select_next_from_one_on_one_pool(config, **_fallback())

    assert first.model == "model-a"
    assert second.model == "model-b"


def test_empty_pool_returns_none():
    selector = SequentialLLMSelector()
    config = _config(one_on_one=[])

    assert selector.select_next_from_one_on_one_pool(config, **_fallback()) is None


def test_one_on_one_and_multi_npc_indices_are_independent():
    selector = SequentialLLMSelector()
    config = _config(
        one_on_one=[
            {"service": "OpenRouter", "model": "one-a"},
            {"service": "OpenRouter", "model": "one-b"},
        ],
        multi_npc=[
            {"service": "OpenRouter", "model": "multi-a"},
            {"service": "OpenRouter", "model": "multi-b"},
        ],
    )

    assert selector.select_next_from_one_on_one_pool(config, **_fallback()).model == "one-a"
    assert selector.select_next_from_multi_npc_pool(config, **_fallback()).model == "multi-a"
    assert selector.select_next_from_one_on_one_pool(config, **_fallback()).model == "one-b"
    assert selector.select_next_from_multi_npc_pool(config, **_fallback()).model == "multi-b"


def test_reset_returns_to_first_model():
    selector = SequentialLLMSelector()
    config = _config(one_on_one=[
        {"service": "OpenRouter", "model": "model-a"},
        {"service": "OpenRouter", "model": "model-b"},
        {"service": "OpenRouter", "model": "model-c"},
    ], multi_npc=[
        {"service": "OpenRouter", "model": "multi-a"},
        {"service": "OpenRouter", "model": "multi-b"},
    ])

    selector.select_next_from_one_on_one_pool(config, **_fallback())
    selector.select_next_from_one_on_one_pool(config, **_fallback())
    selector.select_next_from_multi_npc_pool(config, **_fallback())
    selector.reset()

    assert selector.select_next_from_one_on_one_pool(config, **_fallback()).model == "model-a"
    assert selector.select_next_from_multi_npc_pool(config, **_fallback()).model == "multi-a"
