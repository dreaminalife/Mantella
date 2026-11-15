import copy
from src.character_manager import Character
from src.conversation.context import context as Context


def set_filter(context: Context, raw_value: str):
    """Sets the filter the way the UI / config.ini does, so the parsing is tested too"""
    context.config.definitions.get_config_value_definition("ingame_events_filter").value = raw_value
    context.config.update_config_loader_with_changed_config_values()


def kept_events(context: Context, events: list[str]) -> list[str]:
    context.update_context(None, None, events, "", {})
    return context.get_context_ingame_events()


# Parsing

def test_ingame_events_filter_is_empty_by_default(default_context: Context):
    assert default_context.config.ingame_events_filter == []


def test_patterns_are_trimmed_lowercased_and_empty_entries_dropped(default_context: Context):
    set_filter(default_context, "  *Flame Cloak* ,, *OAKFLESH*,  ,")

    assert default_context.config.ingame_events_filter == ["*flame cloak*", "*oakflesh*"]


def test_filter_of_only_commas_and_spaces_keeps_every_event(default_context: Context):
    set_filter(default_context, " , ,, ")

    assert kept_events(default_context, ["Lydia casted the spell Flames"]) == ["Lydia casted the spell Flames"]


# Matching

def test_events_matching_a_filter_pattern_are_dropped(default_context: Context):
    set_filter(default_context, "*Flame Cloak*")
    events = [
        "Lydia casted the spell Flame Cloak",
        "A bandit hit Lydia with Flame Cloak",
        "Lydia casted the spell Healing",
    ]

    assert kept_events(default_context, events) == ["Lydia casted the spell Healing"]


def test_spell_name_without_wildcards_ignores_nothing(default_context: Context):
    set_filter(default_context, "Flame Cloak")

    assert kept_events(default_context, ["Lydia casted the spell Flame Cloak"]) == ["Lydia casted the spell Flame Cloak"]


def test_pattern_without_wildcards_matches_an_identical_event(default_context: Context):
    set_filter(default_context, "Lydia casted the spell Flame Cloak")

    assert kept_events(default_context, ["Lydia casted the spell Flame Cloak"]) == []


def test_star_position_decides_where_the_text_must_be(default_context: Context):
    event = "Lydia casted the spell Flame Cloak"
    for pattern, is_dropped in [
        ("*Cloak*", True),
        ("*Flame Cloak", True),
        ("Lydia*", True),
        ("Lydia*Cloak", True),
        ("Flame*", False),
        ("*Lydia", False),
        ("*Frost Cloak*", False),
    ]:
        set_filter(default_context, pattern)
        default_context.clear_context_ingame_events()

        assert (kept_events(default_context, [event]) == []) == is_dropped, pattern


def test_star_matches_empty_text(default_context: Context):
    set_filter(default_context, "*Lydia casted the spell Flames*")

    assert kept_events(default_context, ["Lydia casted the spell Flames"]) == []


def test_question_mark_matches_exactly_one_character(default_context: Context):
    set_filter(default_context, "*Fl?mes")
    events = [
        "Lydia casted the spell Flames",
        "Lydia casted the spell Flmes",
        "Lydia casted the spell Flaames",
    ]

    assert kept_events(default_context, events) == ["Lydia casted the spell Flmes", "Lydia casted the spell Flaames"]


def test_matching_ignores_case_of_pattern_and_event(default_context: Context):
    set_filter(default_context, "*FLAME cloak*")

    assert kept_events(default_context, ["lydia casted the spell flame CLOAK"]) == []


def test_surrounding_whitespace_and_newlines_in_events_are_ignored(default_context: Context):
    set_filter(default_context, "*Flames")

    assert kept_events(default_context, ["  Lydia casted the spell Flames\n"]) == []


def test_star_alone_drops_every_event(default_context: Context):
    set_filter(default_context, "*")

    assert kept_events(default_context, ["Lydia casted the spell Flames", "Lydia equipped Iron Sword"]) == []


def test_any_of_several_patterns_drops_an_event_and_order_is_kept(default_context: Context):
    set_filter(default_context, "*Flame Cloak*, *Oakflesh*")
    events = [
        "Lydia equipped Iron Sword",
        "Lydia casted the spell Oakflesh",
        "Lydia casted the spell Healing",
        "Lydia casted the spell Flame Cloak",
        "Lydia unequipped Iron Sword",
    ]

    assert kept_events(default_context, events) == [
        "Lydia equipped Iron Sword",
        "Lydia casted the spell Healing",
        "Lydia unequipped Iron Sword",
    ]


def test_square_brackets_are_a_character_set_so_question_marks_are_needed(default_context: Context):
    event = "Lydia casted the spell [Mod] Fireball"

    set_filter(default_context, "*[Mod] Fireball")
    assert kept_events(default_context, [event]) == [event]

    default_context.clear_context_ingame_events()
    set_filter(default_context, "*?Mod? Fireball")
    assert kept_events(default_context, [event]) == []


# Interaction with the rest of the context

def test_no_events_or_none_does_not_fail(default_context: Context):
    set_filter(default_context, "*Flames*")

    assert kept_events(default_context, []) == []
    assert kept_events(default_context, None) == []


def test_filtered_location_event_does_not_change_the_location(default_context: Context):
    set_filter(default_context, "*Whiterun*")

    kept_events(default_context, ["The location is now Whiterun"])

    assert default_context.location == "Skyrim"


def test_events_written_by_the_server_are_filtered_too(default_context: Context):
    set_filter(default_context, "*location*")

    default_context.update_context("Whiterun", None, ["The location is now Riften"], "", {})

    assert default_context.get_context_ingame_events() == []
    assert default_context.location == "Whiterun" # Only the event is hidden, the location is still tracked


def test_combat_events_are_filtered_but_combat_state_still_updates(default_context: Context, example_skyrim_player_character: Character, example_skyrim_npc_character: Character):
    default_context.add_or_update_characters([example_skyrim_player_character, example_skyrim_npc_character])
    default_context.clear_context_ingame_events()
    set_filter(default_context, "*no longer in combat*, *now in combat*")

    player, guard = copy.deepcopy(example_skyrim_player_character), copy.deepcopy(example_skyrim_npc_character)
    player.is_in_combat = guard.is_in_combat = True
    default_context.add_or_update_characters([player, guard])
    assert default_context.get_context_ingame_events() == []
    assert default_context.npcs_in_conversation.get_character_by_name("Guard").is_in_combat

    player, guard = copy.deepcopy(player), copy.deepcopy(guard)
    player.is_in_combat = guard.is_in_combat = False
    default_context.add_or_update_characters([player, guard])
    assert default_context.get_context_ingame_events() == []
    assert not default_context.npcs_in_conversation.get_character_by_name("Guard").is_in_combat


def test_combat_events_are_kept_without_a_matching_filter(default_context: Context, example_skyrim_player_character: Character, example_skyrim_npc_character: Character):
    default_context.add_or_update_characters([example_skyrim_player_character, example_skyrim_npc_character])
    default_context.clear_context_ingame_events()
    set_filter(default_context, "*Flames*")

    guard = copy.deepcopy(example_skyrim_npc_character)
    guard.is_in_combat = True
    default_context.add_or_update_characters([example_skyrim_player_character, guard])

    assert default_context.get_context_ingame_events() == ["Guard is now in combat!"]


def test_changing_the_filter_applies_to_the_next_events(default_context: Context):
    set_filter(default_context, "*Flames*")
    assert kept_events(default_context, ["Lydia casted the spell Flames"]) == []

    set_filter(default_context, "")
    assert kept_events(default_context, ["Lydia casted the spell Flames"]) == ["Lydia casted the spell Flames"]
