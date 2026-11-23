from src.config.retired_models import CURRENT_FREE_MODEL, replace_retired_models


def test_old_default_becomes_the_current_free_model():
    assert replace_retired_models("google/gemma-2-9b-it:free") == (CURRENT_FREE_MODEL, ["google/gemma-2-9b-it:free"])


def test_old_vision_default_becomes_the_current_free_model():
    value, retired = replace_retired_models("meta-llama/llama-3.2-11b-vision-instruct:free")
    assert value == CURRENT_FREE_MODEL
    assert retired == ["meta-llama/llama-3.2-11b-vision-instruct:free"]


def test_a_model_the_player_picked_is_kept():
    assert replace_retired_models("anthropic/claude-opus-5.5") == ("anthropic/claude-opus-5.5", [])


def test_only_the_retired_model_in_a_list_is_swapped():
    value, retired = replace_retired_models("deepseek/deepseek-chat,google/gemma-2-9b-it:free")
    assert value == f"deepseek/deepseek-chat,{CURRENT_FREE_MODEL}"
    assert retired == ["google/gemma-2-9b-it:free"]


def test_the_paid_version_of_the_old_model_is_kept():
    assert replace_retired_models("google/gemma-2-9b-it") == ("google/gemma-2-9b-it", [])
