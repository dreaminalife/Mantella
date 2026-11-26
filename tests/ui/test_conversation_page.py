"""The Conversation page of Mantella's UI: the live conversation editor, with the Conversation settings below it"""
import asyncio

import gradio as gr
from gradio.state_holder import SessionState
import pytest

from src.config.config_loader import ConfigLoader
from src.config.definitions.action_definitions import ACTION_KINDS, GLOBAL_SETTINGS_KEY, actions_key
from src.config.definitions.conversation_definitions import CONVERSATION_PAGE
from src.ui.start_ui import StartUI


def tab_of(component) -> str | None:
    """The label of the tab a component is shown in"""
    parent = getattr(component, "parent", None)
    while parent is not None:
        if isinstance(parent, gr.Tab):
            return parent.label
        parent = getattr(parent, "parent", None)
    return None


def test_the_conversation_tab_shows_the_live_editor_and_the_settings_below_it(default_config: ConfigLoader):
    default_config.auto_launch_ui = False
    ui = StartUI(default_config)
    blocks = ui.create_main_block()
    tabs = [component.label for component in blocks.blocks.values() if isinstance(component, gr.Tab)]
    assert "Conversation" in tabs and "Live Conversation" not in tabs
    assert not {"All Conversations", "Group Conversation", "Shout", "Director Mode", "NPC Initiative", "Conversation Event", "Nearby Event", "Random Event", "Watched NPC Event"} & set(tabs)

    shown = ui._StartUI__constructor._SettingsUIConstructor__config_value_to_ui_element
    tab_by_identifier = {config_value.identifier: tab_of(component) for config_value, component in shown.items()}
    page_settings = [value.identifier for group in default_config.definitions.base_groups if group.page == CONVERSATION_PAGE for value in group.value]
    assert len(page_settings) > 20
    for identifier in page_settings:
        assert tab_by_identifier.get(identifier) == "Conversation", identifier
    table = next(component for component in blocks.blocks.values() if isinstance(component, gr.Dataframe) and component.label == "Conversation")
    assert tab_of(table) == "Conversation" # The live conversation editor
    assert not any(isinstance(component, gr.Textbox) and component.label == "Decensor bypass (read-only)" for component in blocks.blocks.values())


def accordions_of(component) -> list[str]:
    """The labels of the folded sections a component is shown in, innermost first"""
    labels = []
    parent = getattr(component, "parent", None)
    while parent is not None:
        if isinstance(parent, gr.Accordion):
            labels.append(parent.label)
        parent = getattr(parent, "parent", None)
    return labels


def test_each_conversation_section_is_foldable(default_config: ConfigLoader):
    default_config.auto_launch_ui = False
    ui = StartUI(default_config)
    ui.create_main_block()
    shown = ui._StartUI__constructor._SettingsUIConstructor__config_value_to_ui_element
    accordions_by_identifier = {config_value.identifier: accordions_of(component) for config_value, component in shown.items()}
    sections = [group for group in default_config.definitions.base_groups if group.page == CONVERSATION_PAGE]
    assert [group.name for group in sections] == ["All Conversations", "Group Conversation", "Shout", "Director Mode", "NPC Initiative", "Conversation Event", "Nearby Event", "Random Event", "Watched NPC Event", "Actions"]
    for group in sections:
        for value in group.value:
            # Directly in its section's fold, not in an Advanced one. Actions is the last section, under its heading, not folded,
            # with each kind's list of actions folded under its own name
            expected = [group.name] if group.foldable else [value.shown_in] if value.shown_in else []
            assert accordions_by_identifier[value.identifier] == expected, value.identifier


# The talk settings: tick boxes, radio buttons, settings that only show while a switch is on, and what is greyed out

def built_page(config: ConfigLoader):
    config.auto_launch_ui = False
    ui = StartUI(config)
    blocks = ui.create_main_block()
    shown = ui._StartUI__constructor._SettingsUIConstructor__config_value_to_ui_element
    by_identifier = {value.identifier: (value, component) for value, component in shown.items()}
    return blocks, by_identifier


def handlers_of(blocks, component, event: str) -> list:
    """The functions run when the component's event fires"""
    return [block_fn for block_fn in blocks.fns if any(target[0] == component._id and target[1] == event for target in block_fn.targets)]


def name_of(handler) -> str:
    return getattr(handler.fn, "__name__", "") # A handler that only runs in the browser has no function


def is_inside(component, block) -> bool:
    parent = getattr(component, "parent", None)
    while parent is not None:
        if parent is block:
            return True
        parent = getattr(parent, "parent", None)
    return False


def test_the_talk_settings_are_tick_boxes_dropdowns_and_radio_buttons(default_config: ConfigLoader):
    _blocks, shown = built_page(default_config)
    for kind in ["", "shout_", "npc_event_"]:
        assert isinstance(shown[f"{kind}who_must_speak"][1], gr.CheckboxGroup)
        assert isinstance(shown[f"{kind}who_still_speaks_at_reply_limit"][1], gr.Radio)
        assert isinstance(shown[f"{kind}when_an_npc_talks_to_someone"][1], gr.Dropdown)
    assert isinstance(shown["when_you_talk_to_someone"][1], gr.Dropdown)
    radio = shown["director_who_still_speaks_at_reply_limit"][1]
    assert ("Nobody, you can direct again now", "Nobody, it's your turn now") in radio.choices # Shown in Director Mode's words, saved as everywhere


def test_a_kinds_own_settings_show_only_while_its_switch_is_on(default_config: ConfigLoader):
    blocks, shown = built_page(default_config)
    columns = []
    for kind, is_on in [("shout", False), ("conversation_event", False), ("npc_event", True)]:
        switch = shown[f"{kind}_use_own_talk_settings"][1]
        handler = next(handler for handler in handlers_of(blocks, switch, "change") if any(isinstance(output, gr.Column) for output in handler.outputs))
        column = handler.outputs[0]
        columns.append(column)
        assert column.visible == is_on, kind
        for setting in ("who_must_speak", "when_an_npc_talks_to_someone", "who_still_speaks_at_reply_limit", "replies_in_between", "extra_rounds"):
            assert is_inside(shown[f"{kind}_{setting}"][1], column), f"{kind}_{setting}" # The whole set is in it
        assert not is_inside(switch, column)
        for on in (True, False):
            # Through Gradio's own processing, which only accepts an update for a column
            result = asyncio.run(blocks.process_api(fn_index=blocks.fns.index(handler), inputs=[on], state=SessionState(blocks), request=None))
            assert [update["visible"] for update in result["data"]] == [on] * len(handler.outputs)
    assert not any(is_inside(shown["who_must_speak"][1], column) for column in columns) # The shared set always shows
    assert not any(is_inside(shown["shout_max_people"][1], column) for column in columns) # Settings after the set always show too


def test_clicking_a_tick_box_unticks_one_it_cant_go_with_and_saves(default_config: ConfigLoader):
    blocks, shown = built_page(default_config)
    value, ticks = shown["who_must_speak"]
    on_click = handlers_of(blocks, ticks, "input")[0]
    assert not handlers_of(blocks, ticks, "change") or all(name_of(handler) in ("talk_greying", "describe", "") for handler in handlers_of(blocks, ticks, "change"))
    update, error = on_click.fn(["Anyone you talk to", "Nobody in particular"])
    assert update["value"] == ["Nobody in particular"] and value.value == ["Nobody in particular"] and not error["visible"]
    update, _error = on_click.fn([]) # The last box stays ticked
    assert update["value"] == ["Nobody in particular"]


@pytest.mark.parametrize("ticked, at_limit, rounds, expected", [
    (["Anyone you talk to"], "Only people who haven't answered you yet", 1, [True, True, True]),
    (["Nobody in particular"], "Only people who haven't answered you yet", 1, [False, False, False]), # Nobody is left who has to speak
    (["Anyone you talk to"], "Nobody, it's your turn now", 1, [True, False, False]),
    (["Anyone you talk to"], "Everyone who still has to speak", 0, [True, False, True]),
])
def test_what_cant_change_anything_is_greyed_out_after_each_change_and_on_load(default_config: ConfigLoader, ticked, at_limit, rounds, expected):
    blocks, shown = built_page(default_config)
    components = [shown[f"shout_{setting}"][1] for setting in ("who_must_speak", "who_still_speaks_at_reply_limit", "extra_rounds")]
    outputs = [shown[f"shout_{setting}"][1] for setting in ("who_still_speaks_at_reply_limit", "replies_in_between", "extra_rounds")]
    for component in components:
        handler = next(handler for handler in handlers_of(blocks, component, "change") if name_of(handler) == "talk_greying")
        assert handler.inputs == components and handler.outputs == outputs
    on_load = [handler for handler in handlers_of(blocks, blocks, "load") if name_of(handler) == "talk_greying" and handler.outputs == outputs]
    assert len(on_load) == 1
    assert [update["interactive"] for update in on_load[0].fn(ticked, at_limit, rounds)] == expected


def test_global_actions_tick_every_list_and_each_list_updates_it(default_config: ConfigLoader):
    blocks, shown = built_page(default_config)
    every, every_ui = shown[GLOBAL_SETTINGS_KEY]
    kind_uis = [shown[actions_key(kind)][1] for kind in ACTION_KINDS]
    assert accordions_of(every_ui) == ["Actions: Global Settings"]

    on_every = handlers_of(blocks, every_ui, "input")
    assert len(on_every) == 1 and on_every[0].outputs == [every_ui, *kind_uis]
    updates = on_every[0].fn([*every.value, "Wait"])
    assert all("Wait" in update["value"] for update in updates)

    one_on_one, one_on_one_ui = shown[actions_key(ACTION_KINDS[0])]
    on_kind = handlers_of(blocks, one_on_one_ui, "input")
    assert len(on_kind) == 1 and on_kind[0].outputs == [one_on_one_ui, every_ui]
    kind_update, every_update = on_kind[0].fn([n for n in one_on_one.value if n != "Wait"])
    assert "Wait" not in kind_update["value"] and "Wait" not in every_update["value"]
    assert "Wait" in shown[actions_key(ACTION_KINDS[1])][0].value # the other lists keep it

    # Default: Global Settings puts every list back to its own defaults; a kind's Default also updates Global Settings
    for trigger, outputs in ((every_ui, [every_ui, *kind_uis]), (one_on_one_ui, [one_on_one_ui, every_ui])):
        default = next(handler for handler in blocks.fns if handler.outputs == outputs and any(target[1] == "click" for target in handler.targets))
        default.fn()
    assert all(shown[actions_key(kind)][0].value == shown[actions_key(kind)][0].default_value for kind in ACTION_KINDS)


def test_the_line_under_who_still_speaks_says_who_that_is_with_the_boxes_ticked_now(default_config: ConfigLoader):
    blocks, shown = built_page(default_config)
    ticks, radios = shown["shout_who_must_speak"][1], shown["shout_who_still_speaks_at_reply_limit"][1]
    for trigger, event in ((ticks, "change"), (radios, "change"), (blocks, "load")):
        handler = next(handler for handler in handlers_of(blocks, trigger, event) if name_of(handler) == "describe" and handler.inputs == [ticks, radios])
        result = asyncio.run(blocks.process_api(fn_index=blocks.fns.index(handler), inputs=[["Anyone you talk to", "Anyone an NPC talks to"],
                                                "Everyone who still has to speak"], state=SessionState(blocks), request=None))
        line = result["data"][0]
        line = line["value"] if isinstance(line, dict) else line
        assert line.startswith("Nobody is dropped") # What the choice does
        assert line.endswith("Right now, with the boxes ticked above: everyone you spoke to who hasn't answered you yet, "
                             "and everyone an NPC spoke to who hasn't answered that NPC yet.")
    # Only from Mantella: a lookup in the browser would overwrite the "Right now" part
    assert not [handler for handler in handlers_of(blocks, radios, "change") if handler.fn is None]
