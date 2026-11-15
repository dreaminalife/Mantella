import pytest

from src.prompt_conditions import apply_conditions, find_condition_error, has_condition_tags, strip_condition_tags
from src.prompt_errors import PromptConditionError, PromptSectionError

SITUATIONS = {"director", "shout", "say_to_room", "npc_event"}


def apply(text: str, *active: str) -> str:
    return apply_conditions(text, set(active), SITUATIONS)


def test_text_without_tags_is_unchanged():
    text = "Hello {name}.\nYou are in {location}.\n"
    assert apply(text, "shout") == text
    assert not has_condition_tags(text)


def test_a_tag_alone_on_its_line_removes_the_whole_line():
    text = "a\n{if shout}\nb\n{end if}\nc\n"
    assert apply(text, "shout") == "a\nb\nc\n"
    assert apply(text) == "a\nc\n"


def test_indented_tag_lines_are_removed_too():
    assert apply("a\n   {if shout}   \nb\n  {end if}\nc", "shout") == "a\nb\nc"


def test_a_tag_on_the_last_line_takes_the_line_break_before_it():
    assert apply("a\n{if shout}\nb\n{end if}", "shout") == "a\nb"
    assert apply("a\n{if shout}\nb\n{end if}") == "a"


def test_a_block_on_the_first_lines_leaves_no_empty_line():
    assert apply("{if shout}\nb\n{end if}\nc", "shout") == "b\nc"
    assert apply("{if shout}\nb\n{end if}\nc") == "c"


def test_empty_lines_inside_a_kept_branch_stay():
    assert apply("a\n{if shout}\n\nb\n{end if}\nc", "shout") == "a\n\nb\nc"


def test_a_trailing_line_break_stays():
    assert apply("a\n{if shout}\nb\n{end if}\n") == "a\n"


def test_inline_tags_keep_the_text_around_them():
    text = "You are in {location}{if shout}, where people can hear you{end if}."
    assert apply(text, "shout") == "You are in {location}, where people can hear you."
    assert apply(text) == "You are in {location}."


def test_else_if_and_else_choose_one_branch():
    text = "{if shout}\nS\n{else if npc_event}\nE\n{else}\nG\n{end if}\n"
    assert apply(text, "shout") == "S\n"
    assert apply(text, "npc_event") == "E\n"
    assert apply(text) == "G\n"
    assert apply(text, "director") == "G\n"


def test_the_first_true_branch_wins():
    assert apply("{if shout}A{else if not npc_event}B{end if}", "shout") == "A"


def test_nested_blocks():
    text = "{if not shout}\n{if director}\nD\n{else}\nG\n{end if}\n{else}\nS\n{end if}\n"
    assert apply(text, "director") == "D\n"
    assert apply(text) == "G\n"
    assert apply(text, "shout", "director") == "S\n"


@pytest.mark.parametrize("condition, active, expected", [
    ("shout or npc_event", {"npc_event"}, True),
    ("shout or npc_event", set(), False),
    ("shout and director", {"shout"}, False),
    ("shout and director", {"shout", "director"}, True),
    ("not director", set(), True),
    ("not director", {"director"}, False),
    ("not not director", {"director"}, True),
    ("shout and not director or npc_event", {"npc_event", "director"}, True), # 'and' binds tighter than 'or'
    ("shout and not director or npc_event", {"shout", "director"}, False),
    ("SHOUT", {"shout"}, True), # Names are not case sensitive
])
def test_not_and_or(condition, active, expected):
    assert apply_conditions(f"{{if {condition}}}yes{{end if}}", active, SITUATIONS) == ("yes" if expected else "")


def test_variables_are_left_alone():
    assert apply("{if shout}{nearby}{end if} {name} {names_w_player}", "shout") == "{nearby} {name} {names_w_player}"


def test_strip_keeps_every_branch():
    assert strip_condition_tags("{if shout}A{else if npc_event}B{else}C{end if}") == "ABC"


@pytest.mark.parametrize("text, message", [
    ("{if shout}A", "{if shout} has no matching {end if}."),
    ("A{end if}", "{end if} has no {if ...} before it."),
    ("{else}A", "{else} has no {if ...} before it."),
    ("{else if shout}A{end if}", "{else if shout} has no {if ...} before it."),
    ("{if shout}A{else}B{else}C{end if}", "{if shout} has two {else}. Keep one."),
    ("{if shout}A{else}B{else if npc_event}C{end if}", "{else if npc_event} comes after the {else} of {if shout}. Put it before the {else}."),
    ("{if talkevent}A{end if}", "{if talkevent}: 'talkevent' is not a condition this prompt knows. Use one of: director, npc_event, say_to_room, shout."),
    ("{if shout and}A{end if}", "{if shout and}: a condition is missing after 'and'."),
    ("{if or shout}A{end if}", "{if or shout}: 'or' needs a condition before it."),
    ("{if shout npc_event}A{end if}", "{if shout npc_event}: put 'and' or 'or' between 'shout' and 'npc_event'."),
    ("{if (shout)}A{end if}", "{if (shout)}: brackets are not supported. Write the conditions one after another, e.g. {if shout or npc_event}."),
])
def test_broken_blocks_are_explained(text, message):
    assert find_condition_error(text, SITUATIONS) == message
    with pytest.raises(PromptConditionError) as error:
        apply(text)
    assert str(error.value) == f"Refusing LLM call. The prompt's conditional blocks are broken: {message}"


def test_conditions_in_branches_that_are_left_out_are_checked_too():
    assert find_condition_error("{if shout}A{else if typo}B{end if}", SITUATIONS) is not None


def test_a_condition_that_is_never_true_here_gets_its_explanation():
    explanations = {"say_to_room": "say_to_room is never true in this prompt."}
    assert find_condition_error("{if say_to_room}A{end if}", {"shout"}, explanations) == "{if say_to_room}: say_to_room is never true in this prompt."


def test_the_condition_error_is_a_prompt_error():
    # Everywhere that refuses the LLM call for a broken prompt catches PromptSectionError
    assert issubclass(PromptConditionError, PromptSectionError)


def test_a_valid_prompt_has_no_error():
    assert find_condition_error("{if shout}\nA\n{else}\nB\n{end if}", SITUATIONS) is None


@pytest.mark.parametrize("text, message", [
    ("{if shout}\nA\n{endif}", "{endif}: write {end if}."),
    ("{if shout}\nA\n{End If}", "{End If}: write {end if}."),
    ("{if shout}\nA\n{end}", "{end}: write {end if}."),
    ("{if shout}\nA\n{elif npc_event}\nB\n{end if}", "{elif npc_event}: write {else if npc_event}."),
    ("{if shout}\nA\n{elseif npc_event}\nB\n{end if}", "{elseif npc_event}: write {else if npc_event}."),
    ("{if shout}\nA\n{else npc_event}\nB\n{end if}", "{else npc_event}: {else} takes no condition. For another condition, write {else if npc_event}."),
    ("{If shout}\nA\n{end if}", "{If shout}: write {if shout}."),
    ("{if}\nA\n{end if}", "{if} has no condition. Write one after 'if', e.g. {if shout}."),
    ("{endif}", "{endif}: write {end if}."), # Also with no real tag in the prompt
])
def test_a_tag_that_is_almost_right_is_named_as_a_tag(text, message):
    assert find_condition_error(text, SITUATIONS) == message
    with pytest.raises(PromptConditionError):
        apply(text, "shout")


def test_variables_that_start_like_a_tag_are_not_mistaken_for_one():
    assert apply("{if_shout} {end_time} {elsewhere} {ifs}", "shout") == "{if_shout} {end_time} {elsewhere} {ifs}"
