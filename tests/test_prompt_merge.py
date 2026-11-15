"""The merged built-in prompts reproduce, branch by branch, the 18 prompts they replaced. The old texts are frozen in
tests/prompt_merge_fixtures, as they were before the merge. So is the Memory Prompt from before it got a Director Mode part"""
from pathlib import Path

import pytest

from src.config.definitions.prompt_definitions import PromptDefinitions as P

FIXTURES = Path(__file__).parent / "prompt_merge_fixtures"

# (merged prompt, the old prompt it replaces, the conditions that are true in that old prompt's situation)
CASES = [
    ("skyrim_game_master_prompt", "skyrim_game_master_prompt", set()),
    ("skyrim_game_master_prompt", "skyrim_game_master_director_prompt", {"director"}),
    ("skyrim_game_master_prompt", "skyrim_shout_game_master_prompt", {"shout"}),
    ("skyrim_game_master_prompt", "skyrim_shout_room_game_master_prompt", {"say_to_room"}),
    ("skyrim_game_master_prompt", "skyrim_npc_event_game_master_prompt", {"npc_event"}),
    ("skyrim_game_master_npc_prompt", "skyrim_game_master_npc_prompt", set()),
    ("skyrim_game_master_npc_prompt", "skyrim_game_master_director_npc_prompt", {"director"}),
    ("skyrim_game_master_npc_prompt", "skyrim_shout_npc_prompt", {"shout"}),
    ("skyrim_game_master_npc_prompt", "skyrim_npc_event_npc_prompt", {"npc_event"}),
    ("skyrim_conversation_event_generator_prompt", "skyrim_conversation_event_generator_prompt", {"talk_event"}),
    ("skyrim_conversation_event_generator_prompt", "skyrim_nearby_event_generator_prompt", {"nearby_event"}),
    ("skyrim_conversation_event_generator_prompt", "skyrim_random_event_generator_prompt", {"random_event"}),
    ("skyrim_game_master_decision_instructions", "skyrim_game_master_decision_instructions", set()),
    ("skyrim_game_master_decision_instructions", "skyrim_game_master_decision_event_instructions", {"after_event"}),
    ("skyrim_game_master_decision_player_criteria", "skyrim_game_master_decision_player_criteria", set()),
    ("skyrim_game_master_decision_player_criteria", "skyrim_game_master_decision_event_player_criteria", {"after_event"}),
    ("skyrim_shout_decision_nearby_criteria", "skyrim_shout_decision_nearby_criteria", {"shout"}),
    ("skyrim_shout_decision_nearby_criteria", "skyrim_shout_decision_nearby_criteria", {"say_to_room"}),
    ("skyrim_shout_decision_nearby_criteria", "skyrim_npc_event_decision_nearby_criteria", {"npc_event"}),
    ("memory_prompt", "memory_prompt", set()),
]


def default(prompt_type: str) -> str:
    return getattr(P, f"get_{prompt_type}_config_value")().default_value


@pytest.mark.parametrize("merged, old, conditions", CASES, ids=[f"{old}-{'+'.join(sorted(c)) or 'none'}" for _m, old, c in CASES])
def test_each_branch_is_the_old_prompt(merged, old, conditions):
    old_text = (FIXTURES / f"{old}.txt").read_text(encoding="utf-8")
    assert P.apply_prompt_conditions(merged, default(merged), conditions) == old_text


def test_every_old_prompt_is_covered():
    assert {old for _merged, old, _conditions in CASES} == {path.stem for path in FIXTURES.glob("*.txt")}


@pytest.mark.parametrize("prompt_type", list(P.PROMPT_CONDITIONS))
def test_merged_defaults_pass_their_own_checks(prompt_type):
    value = getattr(P, f"get_{prompt_type}_config_value")()
    assert value.does_value_cause_error(value.default_value).is_success


def test_director_lines_are_never_alternatives_to_the_kinds_of_conversation():
    """Director in Shout comes later: then 'director' is true together with 'shout'. So the built-in prompts keep director in its own blocks"""
    for prompt_type in ("skyrim_game_master_prompt", "skyrim_game_master_npc_prompt"):
        text = default(prompt_type)
        assert "{else if director}" not in text, prompt_type
        for kind in ("shout", "npc_event", "say_to_room"):
            assert f"{{if director}}\n{{else if {kind}}}" not in text and f"{{if {kind} or director}}" not in text, prompt_type


@pytest.mark.parametrize("prompt_type", ["skyrim_game_master_director_prompt", "skyrim_shout_npc_prompt", "skyrim_nearby_event_generator_prompt",
                                         "skyrim_game_master_decision_event_instructions", "skyrim_npc_event_decision_nearby_criteria"])
def test_absorbed_prompts_are_gone(prompt_type):
    from src.prompt_profile_manager import PromptProfileManager
    assert not PromptProfileManager.is_known_type(prompt_type)
    assert not hasattr(P, f"get_{prompt_type}_config_value")
