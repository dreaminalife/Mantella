from typing import TYPE_CHECKING, Iterable

from src.config.definitions.prompt_definitions import PromptDefinitions

if TYPE_CHECKING:
    from src.prompt_profile_manager import PromptProfileManager


class LabeledPrompt(str):
    """A filled-in prompt that remembers which prompt it was built from, so the log can say which prompt each LLM request sent.
    It is the prompt text itself everywhere else. Text added to its end keeps the label (e.g. the Group Answer Note added to an NPC's prompt)"""
    label: str

    def __new__(cls, text: str = "", label: str = ""):
        result = super().__new__(cls, text)
        result.label = label
        return result

    def __add__(self, other: str) -> "LabeledPrompt":
        return LabeledPrompt(str.__add__(self, other), self.label)


_display_names: dict[str, str] | None = None

def prompt_display_name(prompt_type: str) -> str:
    """The prompt's name as the Prompts tab shows it"""
    global _display_names
    if _display_names is None:
        _display_names = {identifier: name for identifier, name, _allowed in PromptDefinitions.get_prompt_profile_types()}
    return _display_names.get(prompt_type, prompt_type)


def describe_prompt(manager: "PromptProfileManager | None", prompt_type: str, conditions: Iterable[str] | None = None, speaker: str = "") -> str:
    """For the log: the prompt's name, its active prompt profile, who it is for and the {if ...} parts of it that were kept.
    E.g. 'Skyrim Game Master NPC Prompt, profile "Gritty", for Lydia, with {if shout}, {if director}'

    Args:
        conditions (Iterable[str] | None): the conditions that were true when the prompt was built. Only those the prompt knows are named
        speaker (str): the NPC (or NPCs) the prompt is for, when that is not clear from the prompt itself
    """
    parts = [prompt_display_name(prompt_type)]
    profile = manager.get_active_name(prompt_type) if manager else None
    if isinstance(profile, str) and profile:
        parts.append(f'profile "{profile}"')
    if speaker:
        parts.append(f"for {speaker}")
    true_conditions = set(conditions or ())
    kept = [condition for condition in PromptDefinitions.PROMPT_CONDITIONS.get(prompt_type, ()) if condition in true_conditions]
    if kept:
        parts.append("with " + ", ".join("{if " + condition + "}" for condition in kept))
    return ", ".join(parts)


def label_prompt(text: str, manager: "PromptProfileManager | None", prompt_type: str, conditions: Iterable[str] | None = None,
                 speaker: str = "") -> LabeledPrompt:
    """The prompt text, labeled with describe_prompt"""
    return LabeledPrompt(text, describe_prompt(manager, prompt_type, conditions, speaker))


def label_text(text: str, label: str) -> LabeledPrompt:
    """The text, labeled with a name of its own, for a request that is not built from a prompt in the Prompts tab"""
    return LabeledPrompt(text, label)


def label_of(text: object) -> str:
    """The label of a prompt text. Empty if it has none"""
    return getattr(text, "label", "") or ""
