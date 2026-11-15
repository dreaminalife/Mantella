"""The loading part of the original Mantella's tests of FunctionManager (tests/actions/test_function_manager.py there). The rest of them,
on the tools of Advanced Actions, come with Advanced Actions"""
import json
from src.actions.function_manager import FunctionManager
from src.config.definitions.action_definitions import ORIGINAL_ACTIONS
from src.conversation.action import Action

def test_load_all_actions():
    """Test loading all actions from data/actions/ folder"""
    # Clear any previously loaded actions
    FunctionManager._actions.clear()
    
    # Load actions
    FunctionManager.load_all_actions()
    
    # Verify actions were loaded
    assert len(FunctionManager._actions) > 0
    
    # Verify all loaded actions have the mantella_ prefix
    for identifier in FunctionManager._actions.keys():
        assert identifier.startswith('mantella_')


def test_load_all_actions_structure():
    """Test that loaded actions have required fields"""
    FunctionManager.load_all_actions()
    
    for _, action in FunctionManager._actions.items():
        if action['name'] not in ORIGINAL_ACTIONS:
            continue # Another mod's file (e.g. Actions For Mantella's), in the older format
        assert 'identifier' in action
        assert 'name' in action
        assert 'description' in action
        assert 'key' in action
        assert 'requires_response' in action
        assert 'is-interrupting' in action or 'is_interrupting' in action
        assert 'one-on-one' in action or 'one_on_one' in action
        assert 'multi-npc' in action or 'multi_npc' in action
        assert 'radiant' in action


def test_load_all_actions_respects_disabled_list(tmp_path):
    """Test that actions in the disabled list are not loaded"""
    FunctionManager._actions.clear()
    FunctionManager._disabled_action_names = []

    actions_dir = tmp_path / "actions"
    actions_dir.mkdir()
    
    action_a = {
        "identifier": "test_a",
        "name": "ActionA",
        "description": "Test",
        "key": "A",
        "prompt": "Test",
        "requires_response": False,
        "is-interrupting": False,
        "one-on-one": True,
        "multi-npc": False,
        "radiant": False
    }

    action_b = {
        "identifier": "test_b",
        "name": "ActionB",
        "description": "Test",
        "key": "B",
        "prompt": "Test",
        "requires_response": False,
        "is-interrupting": False,
        "one-on-one": True,
        "multi-npc": False,
        "radiant": False
    }

    action_c = {
        "identifier": "test_c",
        "name": "ActionC",
        "description": "Test",
        "key": "C",
        "prompt": "Test",
        "requires_response": False,
        "is-interrupting": False,
        "one-on-one": True,
        "multi-npc": False,
        "radiant": False
    }

    with open(actions_dir / "a.json", 'w') as f:
        json.dump(action_a, f)
    with open(actions_dir / "b.json", 'w') as f:
        json.dump(action_b, f)
    with open(actions_dir / "c.json", 'w') as f:
        json.dump(action_c, f)

    # Load with ActionB disabled
    disabled_set = {"actionb"}
    for file_path in actions_dir.glob("*.json"):
        FunctionManager._load_action_file(file_path, disabled_set)

    # ActionA and ActionC should be loaded, ActionB should not
    assert 'mantella_test_a' in FunctionManager._actions
    assert 'mantella_test_c' in FunctionManager._actions
    assert 'mantella_test_b' not in FunctionManager._actions
    assert len(FunctionManager._actions) == 2
    assert 'ActionB' in FunctionManager._disabled_action_names


def test_get_legacy_actions():
    """Test that get_legacy_actions returns Action objects"""
    FunctionManager.load_all_actions()
    
    legacy_actions = FunctionManager.get_legacy_actions()
    
    assert isinstance(legacy_actions, list)
    assert all(isinstance(action, Action) for action in legacy_actions)
    
    for action in legacy_actions:
        assert hasattr(action, 'identifier')
        assert hasattr(action, 'name')
        assert hasattr(action, 'keyword')
        assert hasattr(action, 'description')
        assert hasattr(action, 'prompt_text')
        assert hasattr(action, 'is_interrupting')
        assert hasattr(action, 'use_in_on_on_one')
        assert hasattr(action, 'use_in_multi_npc')
        assert hasattr(action, 'use_in_radiant')


