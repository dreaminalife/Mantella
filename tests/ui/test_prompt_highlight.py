"""The colouring of the prompt text boxes (src/ui/prompt_highlight.py). Its script is run with Node, so it is tested as the
browser runs it: the coloured copy always holds exactly the typed text, the tags, variables and sections are told apart,
mistakes are marked the same as the prompt checks find them, and the cursor lights up the right block"""
import json
import re
import shutil
import subprocess

import gradio as gr
import pytest

from src.config.config_loader import ConfigLoader
from src.config.definitions.game_definitions import GameEnum
from src.config.definitions.prompt_definitions import PromptDefinitions
from src.config.types.config_value_group import ConfigValueGroup
from src.prompt_conditions import find_condition_error
from src.prompt_profile_manager import PromptProfileManager
from src.ui.prompt_highlight import PROMPT_HIGHLIGHT_CLASS, PROMPT_HIGHLIGHT_JS, prompt_box_classes
from src.ui.settings_ui_constructor import SettingsUIConstructor

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed")

# Reads the calls from stdin, answers with the results. Each call: [function, text, caret, prompt type]
RUNNER = """
global.window = {};
%s
const api = window.__mantellaPromptHighlight;
let input = "";
process.stdin.on("data", chunk => input += chunk);
process.stdin.on("end", () => {
  const results = JSON.parse(input).map(([name, text, caret, type]) => name === "render" ? api.render(text, caret, type) : api.analyze(text, type));
  process.stdout.write(JSON.stringify(results));
});
"""


def run(*calls: tuple) -> list:
    completed = subprocess.run([NODE, "-e", RUNNER % PROMPT_HIGHLIGHT_JS], input=json.dumps(calls), capture_output=True,
                               text=True, encoding="utf-8", check=True)
    return json.loads(completed.stdout)


def render(text: str, caret: int | None = None, prompt_type: str | None = None) -> str:
    """prompt_type: the prompt the text is; None for a shared section's text, which may use every condition"""
    return run(("render", text, caret, prompt_type))[0]


def analyze(text: str, prompt_type: str | None = None) -> list[dict]:
    return run(("analyze", text, None, prompt_type))[0]


def shown_text(html: str) -> str:
    """The text the coloured copy shows: one line for each line block"""
    lines = html.split("</div>")[:-1]
    text = [re.sub(r"<[^>]+>", "", line).replace("​", "") for line in lines]
    return "\n".join(text).replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")


def every_default_prompt() -> list[str]:
    return [getattr(PromptDefinitions, f"get_{prompt_type}_config_value")().default_value
            for prompt_type, _name, _allowed in PromptDefinitions.get_prompt_profile_types()]


@needs_node
def test_the_coloured_copy_holds_exactly_the_typed_text():
    """Anything else would put the colours under the wrong letters"""
    texts = every_default_prompt() + [
        "", "\n", "a\n\n\nb\n", "  indented\n\ttab", "<b>not html</b> & \"quotes\" &amp;",
        'JSON {"a": 1} and {{escaped}} and { spaced }', "{if shout}{end if}{else}{endif}{name}{my_section}",
        "{if shout}\nA\n{else}\nB\n{end if}", "line with unicode: Ysolda — ✓",
    ]
    results = run(*[("render", text, None) for text in texts])
    for text, html in zip(texts, results):
        assert shown_text(html) == text


@needs_node
def test_tags_variables_and_sections_are_told_apart():
    tokens = analyze('{if shout}\n{name} {content_rules} {"json": 1}\n{else if not npc_event}\nB\n{else}\nC\n{end if}')
    assert [(t["type"], t.get("kind")) for t in tokens] == [
        ("tag", "if"), ("variable", None), ("section", None), ("tag", "elseif"), ("tag", "else"), ("tag", "end")]
    assert not any(t.get("error") for t in tokens)
    html = render("{if shout and not npc_event}\n{name}\n{end if}")
    assert '<span class="ph-keyword">if</span> <span class="ph-condition">shout</span> <span class="ph-operator">and</span>' in html
    assert '<span class="ph-variable">{name}</span>' in html


@pytest.mark.parametrize("text", [
    "{endif}", "{End If}", "{elif shout}", "{if}", "{if unknown_thing}\nA\n{end if}", "{else}", "{end if}",
    "{if shout}\nA", "{if shout}\nA\n{else}\nB\n{else}\nC\n{end if}", "{if shout}\nA\n{else}\nB\n{else if npc_event}\nC\n{end if}",
    "{if shout npc_event}\nA\n{end if}", "{if shout and}\nA\n{end if}", "{if (shout)}\nA\n{end if}",
])
@needs_node
def test_mistakes_are_marked_where_the_prompt_check_finds_them(text):
    assert find_condition_error(text, {c for cs in PromptDefinitions.PROMPT_CONDITIONS.values() for c in cs}) is not None
    assert "ph-error" in render(text) or "ph-unknown" in render(text)


@needs_node
def test_correct_prompts_have_no_mistake_marked():
    texts = every_default_prompt() + ["{if not director and shout or npc_event}\nA\n{else if say_to_room}\nB\n{end if}"]
    for text, html in zip(texts, run(*[("render", text, None) for text in texts])):
        assert "ph-error" not in html and "ph-unknown" not in html, text[:80]


@needs_node
def test_the_cursor_on_a_tag_lights_up_its_whole_block_only():
    text = "{if shout}\nA\n{if npc_event}\nB\n{end if}\n{else}\nC\n{end if}"
    on_else = text.index("{else}") + 2
    html = render(text, on_else)
    lit = re.findall(r'<span class="ph-tag ph-match">(.*?)</div>', html)
    assert len(lit) == 3 # {if shout}, {else} and its {end if}; not the inner block
    assert "npc_event" not in "".join(lit)
    assert "ph-match" not in render(text, text.index("A")) # Not on a tag
    inner = render(text, text.index("{if npc_event}") + len("{if npc_event}")) # Right after the tag counts too
    assert inner.count("ph-match") == 2


@needs_node
def test_lines_inside_blocks_get_a_band_for_each_level():
    html = render("a\n{if shout}\nb\n{if npc_event}\nc\n{end if}\n{end if}\nd")
    bands = re.findall(r'<div class="ph-line( ph-depth-\d)?">', html)
    assert bands == ["", " ph-depth-1", " ph-depth-1", " ph-depth-2", " ph-depth-2", " ph-depth-2", " ph-depth-1", ""]


def test_the_script_knows_every_condition_and_built_in_variable():
    for condition in ("director", "shout", "say_to_room", "npc_event", "talk_event", "nearby_event", "random_event", "after_event"):
        assert f'"{condition}"' in PROMPT_HIGHLIGHT_JS
    assert '"player_name"' in PROMPT_HIGHLIGHT_JS and '"nearby_bios"' in PROMPT_HIGHLIGHT_JS
    assert "__MANTELLA" not in PROMPT_HIGHLIGHT_JS # Every placeholder is filled
    assert "</script>" not in PROMPT_HIGHLIGHT_JS # It is also put in the page's <head>


def _classes(block) -> list[str]:
    classes = getattr(block, "elem_classes", None) or []
    return [classes] if isinstance(classes, str) else list(classes)


def test_the_profile_editor_colours_the_prompt_and_section_text_but_not_the_preview(tmp_path):
    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    with gr.Blocks() as demo:
        SettingsUIConstructor(config).render_prompt_profile_editor(ConfigValueGroup("prompt_profiles", "Prompt Profiles", "", []))
    coloured = {b.label: _classes(b) for b in demo.blocks.values() if PROMPT_HIGHLIGHT_CLASS in _classes(b)}
    assert set(coloured) == {"Prompt text", "Section text"}
    first_type = PromptProfileManager.identifier_for_display(PromptProfileManager.display_names()[0])
    assert f"ph-type-{first_type}" in coloured["Prompt text"]
    assert "ph-type-section" in coloured["Section text"] # A section can go into any prompt


def test_the_profile_editor_prompt_text_follows_the_chosen_prompt_type(tmp_path):
    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    with gr.Blocks() as demo:
        SettingsUIConstructor(config).render_prompt_profile_editor(ConfigValueGroup("prompt_profiles", "Prompt Profiles", "", []))
    on_type = next(d.fn for d in demo.fns if getattr(d.fn, "__name__", "") == "on_type_change")
    text_update = on_type("Skyrim Game Master Decision Instructions")[4]
    assert text_update["value"] == ""
    assert text_update["elem_classes"] == ["multiline-textbox", *prompt_box_classes("skyrim_game_master_decision_instructions")]


def test_the_prompts_tab_colours_every_prompt_and_nothing_else(tmp_path):
    config = ConfigLoader(str(tmp_path), game_override=GameEnum.SKYRIM, prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    constructor = SettingsUIConstructor(config)
    with gr.Blocks():
        for identifier in ("skyrim_game_master_decision_instructions", "skyrim_prompt", "player_character_description"):
            constructor.visit_ConfigValueString(config.definitions.get_config_value_definition(identifier))
    boxes = {value.identifier: box for value, box in constructor.config_value_to_ui_element.items()}
    one_line_prompt = boxes["skyrim_game_master_decision_instructions"]
    assert PROMPT_HIGHLIGHT_CLASS in _classes(one_line_prompt) and one_line_prompt.max_lines > 1 # A text area even for one line
    assert "ph-type-skyrim_game_master_decision_instructions" in _classes(one_line_prompt)
    assert PROMPT_HIGHLIGHT_CLASS in _classes(boxes["skyrim_prompt"]) and "ph-type-skyrim_prompt" in _classes(boxes["skyrim_prompt"])
    assert PromptProfileManager.is_known_type("skyrim_prompt") and not PromptProfileManager.is_known_type("player_character_description")
    assert PROMPT_HIGHLIGHT_CLASS not in _classes(boxes["player_character_description"])


CROSS_CHECK_TEXTS = [
    "{if after_event}\nA\n{end if}", "{if shout}\nA\n{else}\nB\n{end if}", "{if say_to_room}\nA\n{end if}",
    "{if talk_event or random_event}\nA\n{end if}", "{if director and not shout}\nA\n{end if}", "Plain {name} text", "{else}",
    "{if model has claude}\nA\n{else}\nB\n{end if}", "{if not model is google/gemini-3.1-flash-lite}\nA\n{end if}",
    "{if shout and model has claude}\nA\n{end if}", "{if model claude}\nA\n{end if}", "{if model has}\nA\n{end if}",
]


@needs_node
@pytest.mark.parametrize("prompt_type", list(PromptDefinitions.PROMPT_CONDITIONS) + ["skyrim_prompt", "memory_prompt", "resummarize_prompt",
                                                                                 "stt_names_prompt"])
def test_each_prompt_type_underlines_what_its_own_check_rejects(prompt_type):
    """A condition of another prompt, a broken model condition, or any tag in a prompt that never goes to an AI model, is underlined like
    the check rejects it"""
    results = run(*[("render", text, None, prompt_type) for text in CROSS_CHECK_TEXTS])
    for text, html in zip(CROSS_CHECK_TEXTS, results):
        rejected = PromptDefinitions.find_condition_problem(prompt_type, text) is not None
        marked = "ph-error" in html or "ph-unknown" in html
        assert marked == rejected, (prompt_type, text)


@needs_node
def test_a_shared_section_may_use_every_condition():
    for text in CROSS_CHECK_TEXTS[:5]:
        html = render(text)
        assert "ph-error" not in html and "ph-unknown" not in html, text


@needs_node
def test_a_model_condition_is_coloured_and_its_name_kept_as_typed():
    text = "{if model has google/gemini-3.1-flash-lite and not shout}\nA\n{end if}"
    html = render(text, None, "skyrim_game_master_prompt")
    assert shown_text(html) == text
    assert '<span class="ph-condition">google/gemini-3.1-flash-lite</span>' in html
    assert '<span class="ph-operator">has</span>' in html
    assert "ph-error" not in html and "ph-unknown" not in html
