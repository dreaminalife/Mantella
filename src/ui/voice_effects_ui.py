"""Voice Effects on the Text-to-Speech page: tick the effects to use, change how each one sounds, and hear a line with and without one"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from typing import Any, Callable

import gradio as gr

from src.config.config_loader import ConfigLoader
from src.tts import voice_effects
from src.tts.registry import voice_services
from src.tts.test_line import TEST_LINE, as_in_game, speak_with_service, voice_type_choices
from src.tts.voice_effects import RACE_GROUPS, SLIDER_KEYS, SLIDERS, race_ids, race_names
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VOICES)

EFFECT_LINE = "You dare enter this place? Your bones will join the others."
KEEP_LINE_SECONDS = 300 # both buttons play the same spoken line for this long, so an online service is paid once
EFFECT_SERVICES = "Pocket TTS, your voice setups, ElevenLabs, Inworld, HeyGen, Qwen, Boson AI and Iodine"
UNSAVED = "**Not saved yet.** Press **Save changes** to keep what you changed."
GHOSTS = "Ghosts" # listed with the races, though the game marks ghosts apart: they share their race with the living


def _default_voice_type(voice_types: list[str]) -> str:
    return "MaleNord" if "MaleNord" in voice_types else (voice_types[0] if voice_types else "MaleNord")


def fitting_voice_type(effect: voice_effects.VoiceEffect | None, voice_types: list[str]) -> str:
    """A voice type to try the effect with: one named after the effect or its races when you have it (e.g. MaleDraugr), else MaleNord"""
    default = _default_voice_type(voice_types)
    if effect is None:
        return default
    male_first = [v for v in voice_types if not v.lower().startswith("female")] + [v for v in voice_types if v.lower().startswith("female")]
    for name in [effect.title, *race_names(effect.races)]:
        word = re.sub(r"[^a-z]", "", name.lower())
        if len(word) < 4: # not "orc" inside "MaleOrcish..." by chance
            continue
        found = next((v for v in male_first if word in v.lower()), None)
        if found:
            return found
    return default


def with_label(effect: voice_effects.VoiceEffect | None) -> str:
    """The button that plays the line with the picked effect, named after it"""
    return f"▶ With {effect.title} Effect" if effect else "▶ With the effect"


def without_label(effect: voice_effects.VoiceEffect | None) -> str:
    """The button that plays the line without the picked effect, named after it"""
    return f"▶ Without {effect.title} Effect" if effect else "▶ Without the effect"


def tick_message(effects: voice_effects.VoiceEffects) -> str:
    """What the ticks mean now: who gets each effect in use"""
    used = [e for e in effects.all() if e.on]
    if not used:
        return "No effects in use. NPCs sound as the voice service speaks them."
    return "In use: " + "; ".join(f"{e.title} ({e.who()})" for e in used) + ". NPCs get it from their next line."


def how_it_works(effects: voice_effects.VoiceEffects) -> str:
    problems = [f"- Can't be used: {p}" for p in effects.problems()]
    return (
        "**Who gets an effect**\n\n"
        "- Tick an effect under **Effects in use**. "
        "From their next line, the NPCs under **Who gets it** get it: ghosts, or NPCs of those races.\n"
        "- To give one NPC an effect, or none, pick it under **Voice effect** in the **Bio Editor** tab and press **Save**. "
        "The effect must be ticked here too.\n"
        f"- Works with {EFFECT_SERVICES}. Piper, XTTS and xVASynth don't use effects.\n"
        "- Mouths move with the words, not the echo.\n\n"
        "**Change how an effect sounds**\n\n"
        "- **Strength** turns the whole effect up or down. **Fine-tune** has each part of the sound.\n"
        "- Press **▶ With** and the effect's name to try your changes, then **Save changes**. **Undo my changes** brings back Mantella's version.\n"
        "- **Make your own effect** saves what you hear as a new effect. Give it races or ghosts, or pick it for single NPCs in the Bio Editor.\n\n"
        "**Where the effects are kept**\n\n"
        f"- Mantella's: `{effects.built_in_folder}`, replaced by updates.\n"
        f"- Your changes and your own effects: `{effects.own_folder}`, never touched by updates. "
        "Press **Reload effects** after changing a file by hand."
        + ("\n\n" + "\n".join(problems) if problems else "")
    )


def render_voice_effects_editor(config: ConfigLoader, chat_manager_getter: Callable[[], Any]) -> None:
    effects = voice_effects.shared_voice_effects(config.save_folder)
    os.makedirs(effects.own_folder, exist_ok=True)
    voice_types = voice_type_choices(config.save_folder)

    def effect_choices() -> list[tuple[str, str]]:
        return [(e.title, e.name) for e in effects.all()]

    def ticked() -> list[str]:
        return [e.name for e in effects.all() if e.on]

    first = effect_choices()
    first_name = first[0][1] if first else None
    first_effect = effects.find(first_name or "")
    first_voice_type = fitting_voice_type(first_effect, voice_types)

    with gr.Accordion(label="How it works", open=False, elem_classes="help-fold"):
        how_ui = gr.Markdown(how_it_works(effects))
        with gr.Row():
            reload_btn = gr.Button("Reload effects", size="sm", scale=0, min_width=200)
    in_use_ui = gr.CheckboxGroup(choices=effect_choices(), value=ticked(), label="Effects in use",
                                 info="Tick an effect to use it in game, from the NPCs' next line. Mantella goes by each NPC's "
                                      "race in the game, e.g. Draugr is for NPCs whose race is Draugr. Ghost is for NPCs the game "
                                      "marks as ghosts, whatever their race. Change who gets it below, under Who gets it.")
    in_use_message_ui = gr.Markdown("", elem_classes="voice-effects-note")

    gr.Markdown("### Change an effect")
    effect_ui = gr.Dropdown(choices=first, value=first_name, label="Effect", allow_custom_value=False)
    about_ui = gr.Markdown(first_effect.about if first_effect else "", elem_classes="voice-effects-note")
    gr.Markdown("#### Who gets it")
    races_ui = gr.Dropdown(choices=_race_choices(first_effect), value=_who(first_effect),
                           multiselect=True, allow_custom_value=True, label="Ghosts and races",
                           info="For a race from a mod, type its name from the game, e.g. FoxRace")
    gr.Markdown("#### How it sounds")
    strength_ui = gr.Slider(0, 100, step=1, value=first_effect.strength if first_effect else 100, label="Strength",
                            info="How much of the effect you hear")
    with gr.Accordion(label="Fine-tune", open=False, elem_classes="voice-clips-subsection"):
        slider_uis = [gr.Slider(s.low, s.high, step=s.step, value=first_effect.sliders[s.key] if first_effect else 0, label=s.label, info=s.info)
                      for s in SLIDERS]
    gr.Markdown("#### Try it")
    voice_type_ui = gr.Dropdown(choices=voice_types, value=first_voice_type, label="Voice type", allow_custom_value=True)
    with gr.Row(elem_classes="voice-effects-try"):
        line_ui = gr.Text(value=EFFECT_LINE, label="Test sentence", max_lines=2, scale=1)
        with_btn = gr.Button(with_label(first_effect), size="sm", scale=0, min_width=200)
        without_btn = gr.Button(without_label(first_effect), size="sm", scale=0, min_width=200)
    hear_message_ui = gr.Markdown("", elem_classes="voice-effects-note")
    audio_ui = gr.Audio(label="Voice effect", show_label=False, interactive=False, type="filepath", visible=False)
    unsaved_ui = gr.Markdown("", elem_classes="voice-effects-note")
    with gr.Row(elem_classes="voice-effects-buttons"):
        save_btn = gr.Button("Save changes", variant="primary", size="sm")
        undo_btn = gr.Button("Undo my changes", size="sm", visible=bool(first_effect and first_effect.changed))
        delete_btn = gr.Button("Delete this effect", size="sm", visible=bool(first_effect and first_effect.is_own))
    message_ui = gr.Markdown("", elem_classes="voice-effects-note")
    with gr.Accordion(label="Make your own effect", open=False, elem_classes="voice-clips-subsection"):
        gr.Markdown(
            "Copies the effect you're changing, as you hear it now, under a new name. The effect you copied stays as it is.\n\n"
            "1. Set **Strength** and **Fine-tune** until **▶ With … Effect** sounds right.\n"
            "2. Type a **Name** and press **Save as a new effect**. Your effect opens above, used by nobody yet.\n"
            "3. Choose who gets it:\n"
            "    - **Ghosts or races:** pick them under **Ghosts and races** and press **Save changes**. "
            "Then tick your effect under **Effects in use**. If one of Mantella's effects is ticked for the same race, yours is used.\n"
            "    - **One NPC:** in the **Bio Editor** tab, open the NPC, pick your effect under **Voice effect** and press **Save**. "
            "Then tick your effect under **Effects in use**, or it stays off.")
        with gr.Row(equal_height=False, elem_classes="voice-clips-row"):
            copy_name_ui = gr.Text(label="Name", placeholder="e.g. Echoing Hall", max_lines=1, scale=3)
            save_new_btn = gr.Button("Save as a new effect", size="sm", scale=1, min_width=160)
        new_message_ui = gr.Markdown("", elem_classes="voice-effects-note")
    pending_delete = gr.State("")
    auto_voice_type = gr.State(first_voice_type) # the voice type the page picked, so picking another effect only replaces that one
    spoken: dict[str, Any] = {} # the last line the TTS Service spoke, so both buttons compare the same line

    # The parts of the page that show one effect, in this order
    shown = [effect_ui, about_ui, races_ui, strength_ui, *slider_uis, undo_btn, delete_btn, pending_delete,
             unsaved_ui]
    delete_at, pending_at = shown.index(delete_btn), shown.index(pending_delete)

    def show(name: str | None) -> list[Any]:
        """The page showing this effect (or the first one), with the effect list read again"""
        choices = effect_choices()
        names = [value for _, value in choices]
        name = name if name in names else (names[0] if names else None)
        effect = effects.find(name or "")
        if effect is None:
            return [gr.update(choices=choices, value=None), "", gr.update(choices=_race_choices(None), value=[]), 100,
                    *[0 for _ in SLIDERS], gr.update(visible=False), gr.update(visible=False, value="Delete this effect"), "", ""]
        return [gr.update(choices=choices, value=effect.name), effect.about,
                gr.update(choices=_race_choices(effect), value=_who(effect)), effect.strength,
                *[effect.sliders[k] for k in SLIDER_KEYS], gr.update(visible=effect.changed),
                gr.update(visible=effect.is_own, value="Delete this effect"), "", ""]

    def in_use_update() -> Any:
        return gr.update(choices=effect_choices(), value=ticked())

    def on_pick(name: str | None, voice_type: str, auto: str):
        page = show(name)
        if voice_type and voice_type != auto: # the player picked a voice type: keep it
            return [*page, gr.update(), auto]
        fitting = fitting_voice_type(effects.find(name or ""), voice_types)
        return [*page, fitting, fitting]

    def on_refresh(current: str | None):
        return [*show(current), in_use_update(), how_it_works(effects)]

    def on_tick(names: list[str]):
        """Effects in use"""
        try:
            effects.set_on(names or [])
        except Exception as e:
            log.warning(f"Could not tick the voice effects: {e}")
            return in_use_update(), f"Could not save: {e}"
        return in_use_update(), tick_message(effects)

    def sliders_of(values: tuple) -> dict[str, float]:
        return dict(zip(SLIDER_KEYS, values))

    def on_edit(name: str | None, who: list[str], strength: float, *values: float) -> str:
        """Not saved yet, when the page differs from the saved effect"""
        effect = effects.find(name or "")
        if effect is None:
            return ""
        try:
            same = (set(who or []) == set(_who(effect))
                    and abs(float(strength) - effect.strength) < 1e-6
                    and all(abs(float(v) - effect.sliders[k]) < 1e-6 for k, v in sliders_of(values).items()))
        except (TypeError, ValueError):
            same = False
        return "" if same else UNSAVED

    def on_save(name: str | None, who: list[str], strength: float, *values: float):
        if not name:
            return [*show(name), in_use_update(), "Pick an effect first."]
        try:
            ghosts, races = _split_who(who)
            effect = effects.save_changes(name, race_ids(races), ghosts, strength, sliders_of(values))
        except Exception as e: # keep what the player changed on the page, so they can fix it and press Save again
            return [*[gr.update() for _ in shown], gr.update(), f"Could not save: {e}"]
        message = f"Saved {effect.title}."
        message += " NPCs get it from their next line." if effect.on else " Tick it under **Effects in use** to use it in game."
        return [*show(effect.name), in_use_update(), message]

    def on_undo(name: str | None):
        try:
            effect = effects.undo_changes(name or "")
        except Exception as e:
            return [*show(name), in_use_update(), str(e)]
        return [*show(effect.name), in_use_update(), f"{effect.title} is back as Mantella made it."]

    def on_delete(name: str | None, pending: str):
        effect = effects.find(name or "")
        if effect is None:
            return [*show(name), in_use_update(), "Pick an effect first."]
        if pending != effect.name: # the first press only asks
            page = show(effect.name)
            page[delete_at] = gr.update(visible=True, value=f"Press again to delete {effect.title}")
            page[pending_at] = effect.name
            return [*page, in_use_update(), ""]
        try:
            effects.delete(effect.name)
        except Exception as e:
            return [*show(name), in_use_update(), str(e)]
        return [*show(None), in_use_update(), f"Deleted {effect.title}. NPCs picked for it in the Bio Editor now get the usual effect for them."]

    def on_save_new(title: str, name: str | None, strength: float, *values: float):
        try:
            effect = effects.save_as_new(title, name or "", strength, sliders_of(values))
        except Exception as e:
            return [*show(name), in_use_update(), gr.update(), str(e)]
        return [*show(effect.name), in_use_update(), "",
                f"Saved {effect.title}, your own effect, and opened it above. Give it races or ghosts under **Who gets it** and press "
                "**Save changes**, or pick it for an NPC in the Bio Editor. Then tick it under **Effects in use**."]

    def plain_line(service: str, voice_type: str, sentence: str) -> str:
        key = (service, voice_type, sentence)
        raw = os.path.join(tempfile.gettempdir(), "voicelines", "effect_test_plain.wav")
        if spoken.get("key") != key or time.time() - spoken.get("at", 0) > KEEP_LINE_SECONDS or not os.path.exists(raw):
            path = speak_with_service(config, chat_manager_getter, service, voice_type, sentence, as_heard=False)
            os.makedirs(os.path.dirname(raw), exist_ok=True)
            shutil.copyfile(path, raw)
            spoken.update(key=key, at=time.time())
        return raw

    def hear(effect_name: str | None, voice_type: str, sentence: str, with_effect: bool, strength: float | None, values: tuple):
        service = config.chosen_voice_service_name() # as picked on the page now, before the game has sent a request
        info = voice_services.find(service)
        if info is None or info.is_legacy:
            return gr.update(value=None, visible=False), (f"Your TTS Service is {service}, which doesn't use voice effects. "
                                                          f"To hear one, pick one of these as the TTS Service: {EFFECT_SERVICES}.")
        effect = effects.find(effect_name or "") if with_effect else None
        if with_effect and effect is None:
            return gr.update(value=None, visible=False), "Pick an effect first."
        if effect is not None and strength is not None and len(values) == len(SLIDER_KEYS):
            effect = effect.with_values(strength, sliders_of(values)) # as set on the page, saved or not
        try:
            voice_type = (voice_type or "").strip() or "MaleNord"
            raw = plain_line(service, voice_type, (sentence or "").strip() or TEST_LINE)
            out = os.path.join(tempfile.gettempdir(), "voicelines", f"effect_test_{'with' if with_effect else 'without'}.wav")
            shutil.copyfile(raw, out)
            volume, pitch = as_in_game(config, service, voice_type)
            voice_effects.finish_line(out, effect, volume, pitch)
        except Exception as e:
            log.warning(f"Voice effect test failed: {e}")
            return gr.update(value=None, visible=False), f"Could not play the line: {e}"
        what = f"{effect.title} · Strength {effect.strength:g}" if effect else "No effect"
        return gr.update(value=out, visible=True), f"{service} · {what}"

    def on_with(effect_name: str | None, voice_type: str, sentence: str, strength: float | None = None, *values: float):
        return hear(effect_name, voice_type, sentence, True, strength, values)

    def on_without(effect_name: str | None, voice_type: str, sentence: str, strength: float | None = None, *values: float):
        return hear(effect_name, voice_type, sentence, False, strength, values)

    edits = [effect_ui, races_ui, strength_ui, *slider_uis]
    hear_inputs = [effect_ui, voice_type_ui, line_ui, strength_ui, *slider_uis]
    effect_ui.input(on_pick, inputs=[effect_ui, voice_type_ui, auto_voice_type], outputs=[*shown, voice_type_ui, auto_voice_type],
                    show_progress="hidden")
    effect_ui.change(lambda name: (with_label(effects.find(name or "")), without_label(effects.find(name or ""))), inputs=[effect_ui],
                     outputs=[with_btn, without_btn],
                     show_progress="hidden", show_api=False)
    reload_btn.click(on_refresh, inputs=[effect_ui], outputs=[*shown, in_use_ui, how_ui])
    # Each time the page is opened: the ticks and sliders as saved now, not as they were when Mantella started.
    # Otherwise the next tick would save the old ticks over the new ones, and Save changes the old sliders
    page = gr.context.Context.root_block
    if page is not None:
        page.load(on_refresh, inputs=[effect_ui], outputs=[*shown, in_use_ui, how_ui], show_progress="hidden", show_api=False)
    in_use_ui.input(on_tick, inputs=[in_use_ui], outputs=[in_use_ui, in_use_message_ui], show_progress="hidden")
    for edit_ui in edits[1:]:
        edit_ui.input(on_edit, inputs=edits, outputs=[unsaved_ui], show_progress="hidden", show_api=False)
    save_btn.click(on_save, inputs=edits, outputs=[*shown, in_use_ui, message_ui])
    undo_btn.click(on_undo, inputs=[effect_ui], outputs=[*shown, in_use_ui, message_ui])
    delete_btn.click(on_delete, inputs=[effect_ui, pending_delete], outputs=[*shown, in_use_ui, message_ui])
    save_new_btn.click(on_save_new, inputs=[copy_name_ui, effect_ui, strength_ui, *slider_uis],
                       outputs=[*shown, in_use_ui, copy_name_ui, new_message_ui])
    # Gradio 4.28 can't describe an Audio player in its API page, which breaks the page
    with_btn.click(on_with, inputs=hear_inputs, outputs=[audio_ui, hear_message_ui], show_api=False)
    without_btn.click(on_without, inputs=hear_inputs, outputs=[audio_ui, hear_message_ui], show_api=False)


def _race_choices(effect: voice_effects.VoiceEffect | None) -> list[str]:
    """Ghosts and the races offered, plus any race from a mod the effect already has"""
    names = [GHOSTS, *RACE_GROUPS]
    return names + [n for n in (race_names(effect.races) if effect else []) if n not in names]


def _who(effect: voice_effects.VoiceEffect | None) -> list[str]:
    """Who gets the effect, as the list shows it: Ghosts first, then the races"""
    if effect is None:
        return []
    return ([GHOSTS] if effect.ghosts else []) + race_names(effect.races)


def _split_who(who: list[str] | None) -> tuple[bool, list[str]]:
    """The list's picks as ghosts or not, and the races"""
    picks = [str(w).strip() for w in (who or []) if str(w).strip()]
    ghosts = any(w.lower() == GHOSTS.lower() for w in picks)
    return ghosts, [w for w in picks if w.lower() != GHOSTS.lower()]
