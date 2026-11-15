import json
from types import SimpleNamespace
from unittest.mock import patch

from src.model_profile_manager import ModelProfileManager
from src.random_llm_selector import RandomLLMSelector


def write_legacy_profile(path):
    data = {
        "or:test/model": {
            "name": "model",
            "service": "or",
            "service_display_name": "OpenRouter",
            "model": "test/model",
            "parameters": {"temperature": 0.1}
        }
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def test_existing_profile_load_does_not_modify_file(tmp_path):
    storage_path = tmp_path / "model_profiles.json"
    write_legacy_profile(storage_path)
    original_contents = storage_path.read_text(encoding="utf-8")

    manager = ModelProfileManager(str(storage_path))

    profile = manager.get_profile_for_model("OpenRouter", "test/model")
    assert profile is not None
    assert profile.slot == 1
    assert profile.enabled is True
    assert profile.weight == 1.0
    assert profile.parameters == {"temperature": 0.1}
    assert storage_path.read_text(encoding="utf-8") == original_contents


def test_save_slots_preserves_profile_one_and_adds_profiles(tmp_path):
    storage_path = tmp_path / "model_profiles.json"
    write_legacy_profile(storage_path)
    manager = ModelProfileManager(str(storage_path))

    assert manager.save_profile_slots(
        "OpenRouter",
        "test/model",
        [{"temperature": 0.1}, {"temperature": 0.9}],
        [True, True],
        [1.0, 3.0]
    )

    slots = manager.get_profile_slots("OpenRouter", "test/model")
    assert slots[0] is not None
    assert slots[0].parameters == {"temperature": 0.1}
    assert slots[1] is not None
    assert slots[1].parameters == {"temperature": 0.9}
    assert slots[1].weight == 3.0
    assert slots[2:] == [None] * 8


def test_random_selection_uses_only_enabled_profiles(tmp_path):
    manager = ModelProfileManager(str(tmp_path / "model_profiles.json"))
    manager.save_profile_slots(
        "OpenRouter",
        "test/model",
        [{"temperature": 0.1}, {"temperature": 0.5}, {"temperature": 0.9}],
        [False, True, False]
    )

    selected = manager.select_profile("OpenRouter", "test/model", random_enabled=True)

    assert selected is not None
    assert selected.slot == 2


def test_no_enabled_profiles_falls_back_to_profile_one(tmp_path):
    manager = ModelProfileManager(str(tmp_path / "model_profiles.json"))
    manager.save_profile_slots(
        "OpenRouter",
        "test/model",
        [{"temperature": 0.1}, {"temperature": 0.9}],
        [False, False]
    )

    selected = manager.select_profile("OpenRouter", "test/model", random_enabled=True)

    assert selected is not None
    assert selected.slot == 1


def test_weighted_selection_uses_profile_weights(tmp_path):
    manager = ModelProfileManager(str(tmp_path / "model_profiles.json"))
    manager.save_profile_slots(
        "OpenRouter",
        "test/model",
        [{"temperature": 0.1}, {"temperature": 0.9}, {"temperature": 1.2}],
        [True, True, True],
        [0.0, 2.0, 8.0]
    )

    with patch("random.choices", side_effect=lambda population, weights, k: [population[-1]]) as choices:
        selected = manager.select_profile("OpenRouter", "test/model", random_enabled=True)

    assert selected is not None
    assert selected.slot == 3
    assert choices.call_args.kwargs["weights"] == [2.0, 8.0]


def test_all_zero_weights_fall_back_to_profile_one(tmp_path):
    manager = ModelProfileManager(str(tmp_path / "model_profiles.json"))
    manager.save_profile_slots(
        "OpenRouter",
        "test/model",
        [{"temperature": 0.1}, {"temperature": 0.9}],
        [True, True],
        [0.0, 0.0]
    )

    selected = manager.select_profile("OpenRouter", "test/model", random_enabled=True)

    assert selected is not None
    assert selected.slot == 1


def test_random_model_selection_respects_conversation_profile_toggle(tmp_path):
    manager = ModelProfileManager(str(tmp_path / "model_profiles.json"))
    manager.save_profile_slots(
        "OpenRouter",
        "test/model",
        [{"temperature": 0.1}, {"temperature": 0.9}],
        [True, True]
    )
    selector = RandomLLMSelector(manager)
    config = SimpleNamespace(
        random_llm_one_on_one_enabled=True,
        llm_pool_one_on_one=[{"service": "OpenRouter", "model": "test/model"}],
        apply_profile_one_on_one=False
    )

    without_profile = selector.select_random_llm_for_conversation(
        "one_on_one",
        config,
        "OpenRouter",
        "fallback/model",
        {"temperature": 0.4},
        4096
    )
    assert without_profile.parameters == {"temperature": 0.4}
    assert without_profile.from_profile is False

    config.apply_profile_one_on_one = True
    with patch("random.choice", side_effect=lambda values: values[-1]), patch(
        "random.choices",
        side_effect=lambda population, weights, k: [population[-1]]
    ):
        with_profile = selector.select_random_llm_for_conversation(
            "one_on_one",
            config,
            "OpenRouter",
            "fallback/model",
            {"temperature": 0.4},
            4096
        )

    assert with_profile.parameters == {"temperature": 0.9}
    assert with_profile.from_profile is True
