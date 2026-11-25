"""Conditional blocks in prompts: {if shout} ... {else if npc_event} ... {else} ... {end if}.

A merged prompt holds the text of several situations. When it is sent, only the branches whose condition is true are kept.
A condition is a name, optionally combined with not, and, or ('not' binds tightest, then 'and', then 'or'; no parentheses).

The text is handled line by line: a tag alone on its line removes that whole line, and so do the lines of a branch that is left out,
so the text that is sent has no gaps where tags or other branches were. Tags inside a line keep the text around them.

Model conditions ({if model has claude}, {if model is google/gemini-3.1-flash-lite}) work in every prompt sent to an AI model.
The prompt is built before it is known which model gets it (a rotation, a character's own model or the Fallback model can pick it
when the request is made), so building it applies every other condition and keeps the blocks that depend on the model, in a form
only Mantella writes (hold_model_blocks). apply_model_conditions applies them when the request is made, for the model that gets it"""
import logging
import re
from dataclasses import dataclass
from typing import Any

from src.prompt_errors import PromptConditionError

log = logging.getLogger("prompt_conditions")

# The inside of a tag: if X, else if X, else, end if
_TAG_INSIDE = r"[ \t]*(?:(?P<kind>if|else[ \t]+if)[ \t]+(?P<expression>[^{open}{close}]*?)|(?P<else>else)|(?P<end>end[ \t]+if))[ \t]*"
# A tag: {if X}, {else if X}, {else}, {end if}. Anchored on these words, so a real variable like {name} never matches
TAG_RE = re.compile(r"\{" + _TAG_INSIDE.format(open="{", close="}") + r"\}")
# A model block's tag while the prompt waits for the model that gets it: the same tag between these brackets. No prompt text or
# filled-in variable uses them, and they are not curly brackets, so filling in the variables leaves them alone
HELD_OPEN = "⟦" # ⟦
HELD_CLOSE = "⟧" # ⟧
_HELD_TAG_RE = re.compile(re.escape(HELD_OPEN) + _TAG_INSIDE.format(open=HELD_OPEN, close=HELD_CLOSE) + re.escape(HELD_CLOSE))
# Almost a tag: {endif}, {elif shout}, {If shout}, {if}... Named as a tag in the error, not as an unknown variable
_NEAR_MISS_RE = re.compile(r"\{[ \t]*(?P<word>end[ \t]*if|end|fi|elif|else[ \t]*if|else|if)(?![A-Za-z0-9_])(?P<rest>[^{}]*)\}", re.IGNORECASE)
# Shared section names that would be read as (almost) a tag, so a section can't take them
RESERVED_SECTION_NAMES = {"if", "else", "elif", "elseif", "end", "endif", "fi"}
# A model condition: 'model has claude' (the model's name contains it) or 'model is <its whole name>'
MODEL_WORD = "model"
MODEL_TESTS = ("has", "is")
_MODEL_EXAMPLE = "{if model has claude}"


def near_miss_problem(text: str) -> str | None:
    """The first tag that is almost right, and how to write it"""
    for match in _NEAR_MISS_RE.finditer(text):
        written = match.group(0)
        if TAG_RE.fullmatch(written):
            continue
        word = re.sub(r"[ \t]+", " ", match.group("word").lower())
        rest = match.group("rest").strip()
        if word in ("end if", "endif", "end", "fi"):
            return f"{written}: write {{end if}}."
        if word in ("else if", "elseif", "elif"):
            return f"{written}: write {{else if {rest or 'shout'}}}."
        if word == "else":
            if not rest:
                return f"{written}: write {{else}}."
            return f"{written}: {{else}} takes no condition. For another condition, write {{else if {rest}}}."
        if not rest:
            return f"{written} has no condition. Write one after 'if', e.g. {{if shout}}."
        return f"{written}: write {{if {rest}}}."
    return None


@dataclass
class _Tag:
    kind: str # "if", "else if", "else", "end if"
    expression: str
    original: str # As written, for the error messages


@dataclass
class _Block:
    parent_active: bool
    branch_active: bool
    taken: bool # A branch of this block is surely chosen already
    has_else: bool
    opening: str
    held: bool = False # A branch depends on the model, so the block is written out again for when the model is known


@dataclass(frozen=True)
class _Condition:
    """One condition of a tag: a situation's name, or a model condition, optionally after 'not'"""
    negated: bool
    name: str # A situation, or MODEL_WORD
    test: str = "" # A model condition's 'has' or 'is'
    value: str = "" # A model condition's text, in lower case

    def holds(self, active: set[str], model: str | None) -> bool | None:
        """Whether it is true. None for a model condition while the model is not known yet"""
        if self.name != MODEL_WORD:
            return (self.name in active) != self.negated
        if model is None:
            return None
        model = model.lower()
        matches = self.value in model if self.test == "has" else self.value == model
        return matches != self.negated

    def written(self) -> str:
        return ("not " if self.negated else "") + (f"{MODEL_WORD} {self.test} {self.value}" if self.name == MODEL_WORD else self.name)


def has_condition_tags(text: str) -> bool:
    return TAG_RE.search(text or "") is not None


def strip_condition_tags(text: str) -> str:
    """The text with the tags removed and every branch kept. For checking the variables of all branches"""
    return TAG_RE.sub("", text or "")


def _tag_from_match(match: re.Match[str]) -> _Tag:
    if match.group("end"):
        return _Tag("end if", "", match.group(0))
    if match.group("else"):
        return _Tag("else", "", match.group(0))
    kind = "if" if match.group("kind") == "if" else "else if"
    return _Tag(kind, match.group("expression").strip(), match.group(0))


def _read(expression: str, allowed: set[str], explanations: dict[str, str], original: str) -> list[list[_Condition]]:
    """The conditions of a tag: an 'or' of 'and's. Raises PromptConditionError for a condition that can't be read"""
    if "(" in expression or ")" in expression:
        raise PromptConditionError(f"{original}: brackets are not supported. Write the conditions one after another, e.g. {{if shout or npc_event}}.")
    words = expression.split()
    if not words:
        raise PromptConditionError(f"{original} has no condition. Write one after 'if', e.g. {{if shout}}.")
    alternatives: list[list[_Condition]] = [[]]
    negated = False
    expect_name = True
    index = 0
    while index < len(words):
        word = words[index]
        lowered = word.lower()
        if expect_name:
            if lowered == "not":
                negated = not negated
            elif lowered in ("and", "or"):
                raise PromptConditionError(f"{original}: '{word}' needs a condition before it.")
            elif lowered == MODEL_WORD:
                test = words[index + 1].lower() if index + 1 < len(words) else ""
                if test not in MODEL_TESTS:
                    raise PromptConditionError(f"{original}: write 'model has' and a part of the model's name, or 'model is' and its whole name, "
                                               f"e.g. {_MODEL_EXAMPLE}.")
                value = words[index + 2].lower() if index + 2 < len(words) else ""
                if not value or value in ("and", "or", "not"):
                    raise PromptConditionError(f"{original}: write the model's name after 'model {test}', e.g. {{if model {test} claude}}.")
                alternatives[-1].append(_Condition(negated, MODEL_WORD, test, value))
                negated = False
                expect_name = False
                index += 2
            elif lowered not in allowed:
                if lowered in explanations:
                    raise PromptConditionError(f"{original}: {explanations[lowered]}")
                if not allowed:
                    raise PromptConditionError(f"{original}: '{word}' is not a condition this prompt knows. This prompt only takes model conditions, "
                                               f"e.g. {_MODEL_EXAMPLE}.")
                raise PromptConditionError(f"{original}: '{word}' is not a condition this prompt knows. Use one of: {', '.join(sorted(allowed))}.")
            else:
                alternatives[-1].append(_Condition(negated, lowered))
                negated = False
                expect_name = False
        elif lowered == "and":
            expect_name = True
        elif lowered == "or":
            alternatives.append([])
            expect_name = True
        else:
            raise PromptConditionError(f"{original}: put 'and' or 'or' between '{words[index - 1]}' and '{word}'.")
        index += 1
    if expect_name:
        raise PromptConditionError(f"{original}: a condition is missing after '{words[-1]}'.")
    return alternatives


def _evaluate(alternatives: list[list[_Condition]], active: set[str], model: str | None) -> bool | str:
    """True or False, or while the model is not known yet and the answer depends on it: the model conditions it depends on"""
    waiting: list[list[_Condition]] = []
    for group in alternatives:
        unknown: list[_Condition] = []
        is_false = False
        for condition in group:
            holds = condition.holds(active, model)
            if holds is None:
                unknown.append(condition)
            elif not holds:
                is_false = True
                break
        if is_false:
            continue
        if not unknown:
            return True
        waiting.append(unknown)
    if not waiting:
        return False
    return " or ".join(" and ".join(condition.written() for condition in group) for group in waiting)


class _Evaluator:
    def __init__(self, active: set[str], allowed: set[str], explanations: dict[str, str], model: str | None, tag_form: tuple[str, str]):
        self.__active = active
        self.__allowed = allowed
        self.__explanations = explanations
        self.__model = model
        self.__open, self.__close = tag_form
        self.blocks: list[_Block] = []

    @property
    def is_active(self) -> bool:
        return not self.blocks or self.blocks[-1].branch_active

    def __holds(self, tag: _Tag) -> bool | str:
        return _evaluate(_read(tag.expression, self.__allowed, self.__explanations, tag.original), self.__active, self.__model)

    def __tag(self, inside: str) -> str:
        return f"{self.__open}{inside}{self.__close}"

    def apply(self, tag: _Tag) -> str:
        """Opens, switches or closes a block. Every condition is checked, also in branches that are left out.
        Returns the tag to write in its place: empty, except in a block that depends on the model while it is not known yet"""
        if tag.kind == "if":
            holds = self.__holds(tag)
            parent_active = self.is_active
            block = _Block(parent_active, parent_active and holds is not False, holds is True, False, tag.original)
            self.blocks.append(block)
            if parent_active and isinstance(holds, str):
                block.held = True
                return self.__tag(f"if {holds}")
            return ""
        if not self.blocks:
            raise PromptConditionError(f"{tag.original} has no {{if ...}} before it.")
        block = self.blocks[-1]
        if tag.kind == "else if":
            if block.has_else:
                raise PromptConditionError(f"{tag.original} comes after the {{else}} of {block.opening}. Put it before the {{else}}.")
            holds = self.__holds(tag)
            if not block.parent_active or block.taken or holds is False:
                block.branch_active = False
                return ""
            block.branch_active = True
            if holds is True:
                block.taken = True
                return self.__tag("else") if block.held else ""
            written = self.__tag(f"else if {holds}" if block.held else f"if {holds}")
            block.held = True
            return written
        if tag.kind == "else":
            if block.has_else:
                raise PromptConditionError(f"{block.opening} has two {{else}}. Keep one.")
            block.has_else = True
            block.branch_active = block.parent_active and not block.taken
            block.taken = True
            return self.__tag("else") if block.held and block.branch_active else ""
        self.blocks.pop()
        return self.__tag("end if") if block.held else ""


def _apply(text: str, active: set[str], allowed: set[str], explanations: dict[str, str], model: str | None, tag_re: re.Pattern[str],
           tag_form: tuple[str, str]) -> str:
    evaluator = _Evaluator(active, allowed, explanations, model, tag_form)
    kept_lines: list[str] = []
    for line in text.split("\n"):
        whole_line_tag = tag_re.fullmatch(line.strip())
        if whole_line_tag:
            written = evaluator.apply(_tag_from_match(whole_line_tag)) # The line itself is removed
            if written:
                kept_lines.append(written)
            continue
        was_active = evaluator.is_active
        parts: list[str] = []
        position = 0
        for match in tag_re.finditer(line):
            if evaluator.is_active:
                parts.append(line[position:match.start()])
            parts.append(evaluator.apply(_tag_from_match(match)))
            was_active = was_active or evaluator.is_active
            position = match.end()
        if evaluator.is_active:
            parts.append(line[position:])
        if was_active: # A line that is entirely in a branch left out is removed, line break included
            kept_lines.append("".join(parts))
    if evaluator.blocks:
        raise PromptConditionError(f"{evaluator.blocks[-1].opening} has no matching {{end if}}.")
    return "\n".join(kept_lines)


def apply_conditions(text: str, active: set[str], allowed: set[str], explanations: dict[str, str] | None = None, model: str | None = None) -> str:
    """Keeps the branches whose condition is true and removes the tags

    Args:
        active (set[str]): the conditions that are true now
        allowed (set[str]): the conditions this prompt knows. Model conditions are always allowed
        explanations (dict[str, str] | None): by condition name, why a condition this prompt doesn't know can never be true here
        model (str | None): the name of the AI model that gets the prompt. None while it is not known yet: the blocks that depend on it
            are kept, as {if model has ...} tags with only the model conditions left

    Raises:
        PromptConditionError: the blocks are broken, or use a condition this prompt doesn't know
    """
    text = text or ""
    near_miss = near_miss_problem(text)
    if near_miss:
        raise PromptConditionError(near_miss)
    if not has_condition_tags(text):
        return text
    return _apply(text, active, allowed, explanations or {}, model, TAG_RE, ("{", "}"))


def hold_model_blocks(text: str) -> str:
    """The model blocks that apply_conditions kept, in the form apply_model_conditions reads. Every tag left after apply_conditions
    without a model is one of them"""
    return TAG_RE.sub(lambda match: HELD_OPEN + match.group(0)[1:-1] + HELD_CLOSE, text or "")


def apply_model_conditions(text: str, model: str) -> str:
    """Applies the model blocks a built prompt kept (hold_model_blocks) for the AI model that gets it. Text without them is returned unchanged"""
    if not text or HELD_OPEN not in text:
        return text
    try:
        return _apply(text, set(), set(), {}, model or "", _HELD_TAG_RE, (HELD_OPEN, HELD_CLOSE))
    except PromptConditionError as e: # Only if a filled-in variable brought in one of the brackets: every branch is kept rather than none
        log.warning(f"Could not apply the model conditions of a prompt for {model}: {e.problem} Every model's text is sent.")
        return _HELD_TAG_RE.sub("", text)


def apply_model_conditions_to_request(value: Any, model: str) -> Any:
    """apply_model_conditions on every text of a request (its messages, or a Decisions API body). Returns a copy where something changed,
    so the messages a conversation keeps are left as they are"""
    if isinstance(value, str):
        return apply_model_conditions(value, model)
    if isinstance(value, list):
        changed = [apply_model_conditions_to_request(item, model) for item in value]
        return value if all(new is old for new, old in zip(changed, value)) else changed
    if isinstance(value, dict):
        changed = {key: apply_model_conditions_to_request(item, model) for key, item in value.items()}
        return value if all(changed[key] is item for key, item in value.items()) else changed
    return value


def find_condition_error(text: str, allowed: set[str], explanations: dict[str, str] | None = None) -> str | None:
    """What is wrong with the prompt's conditional blocks, in plain words. None if nothing is"""
    try:
        apply_conditions(text, set(), allowed, explanations)
    except PromptConditionError as e:
        return e.problem
    return None
