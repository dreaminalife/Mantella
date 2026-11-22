"""The voice parts of the Text-to-Speech page: trying a voice, the player's voice setups, and the lists of words to say differently"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

import gradio as gr

from src.config.config_loader import ConfigLoader
from src.config.definitions.tts_definitions import TTSDefinitions
from src.config.types.config_value_selection import ConfigValueSelection
from src.tts.address_check import check_address
from src.tts.clip_service import NoSoundSymbols
from src.tts.registry import normalize_service_name, voice_services
from src.tts.test_line import TEST_LINE, speak_with_clip, speak_with_service, speak_with_setup, voice_type_choices, word_line
from src.tts.voice_folders import pronunciation_folder, voice_clips_folder
from src.tts.voice_style import SPEED_DEFAULT, SPEED_HIGH, SPEED_LABEL, SPEED_LOW
from src.tts.voice_setups import CLIP_BASE64, CLIP_MODES, CLIP_NAME, STYLES, XTTS, empty_setup, normalized, problems, ready_made_setups, to_service_info, voice_setups_for
from src.tts.word_lists import IPA, SPELLING, shared_word_lists
from src.ui.voice_clips_ui import clip_voice
from src.voice_clips.clip_store import VOICE_TYPES, ClipStore, shared_clip_store, voice_key
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VOICES)

NEW_SETUP = "(new setup)"
ALL = "(all)"
SERVICE_SETTINGS = ("tts_service", "tts_backup_service")
# Shown with Your voice setups' introduction, under Settings for
QUICKEST_WAY = ("**Quickest way:** pick a ready-made setup, or type the program's address and press **Check address** to fill in the rest. "
                "Then press **Hear it**.")
# Shown under Hear it for a service that clones voices, which lists only the voice types it can clone
ONLY_READY_VOICE_TYPES = "Only voice types ready to clone are listed. A new one is added the first time an NPC with that voice talks to you in game."


def _newer_service_names() -> list[str]:
    """The services that use the word lists: every service but Piper, XTTS and xVASynth"""
    return [s.name for s in voice_services.all() if not s.is_legacy]


def _default_voice_type(choices: list[str]) -> str | None:
    return "FemaleEvenToned" if "FemaleEvenToned" in choices else (choices[0] if choices else None)


def _clones(service: str | None) -> bool:
    info = voice_services.find(service)
    return info is not None and info.uses_clips


def _voice_types_for(service: str | None, save_folder: str, clips: ClipStore) -> list[str]:
    """A service that clones voices: only the voice types with a clip, so it never speaks in another voice than the one picked.
    Piper, XTTS and xVASynth have a voice of their own for every voice type"""
    if not _clones(service):
        return voice_type_choices(save_folder)
    return sorted((e.info.name or e.key for e in clips.entries(VOICE_TYPES)), key=str.lower)


def render_voice_test(config: ConfigLoader, chat_manager_getter: Callable[[], Any], service_ui: Any) -> None:
    """Hear it, above Customization per voice type and per NPC: the service shown on the page (*service_ui* holds its name) speaks as a voice type"""
    clips = shared_clip_store(voice_clips_folder(config.save_folder))
    gr.Markdown("Hear this service speak as a voice type, without the game.")
    voice_types = _voice_types_for(service_ui.value, config.save_folder, clips)
    with gr.Row(equal_height=False, elem_classes="voice-clips-row"):
        voice_type_ui = gr.Dropdown(choices=voice_types, value=_default_voice_type(voice_types), label="Voice type", allow_custom_value=True, scale=2)
        line_ui = gr.Text(value=TEST_LINE, label="Test sentence", max_lines=2, scale=3)
        play_btn = gr.Button("Hear it", variant="primary", size="sm", scale=1, min_width=160)
    only_ready_ui = gr.Markdown(ONLY_READY_VOICE_TYPES, visible=_clones(service_ui.value))
    audio_ui = gr.Audio(label="Result", show_label=False, interactive=False, type="filepath", visible=False) # shown once there is a line to play
    status_ui = gr.Markdown("")
    clips_changed_ui = gr.Textbox(value="", visible=False) # changes when a voice type gets its clip, so the list redraws by itself

    def on_play(service: str, voice_type: str, line: str):
        try:
            if _clones(service):
                if not voice_type:
                    return gr.update(value=None, visible=False), "Pick a voice type first."
                pick = clip_voice(clips, VOICE_TYPES, voice_key(voice_type))
                if pick is None:
                    return gr.update(value=None, visible=False), (f"{voice_type} isn't ready to clone yet. It's added the first time "
                                                                  "an NPC with that voice talks to you in game.")
                # Says why when the service can't clone right now, instead of speaking in its built-in voice
                path = speak_with_clip(config, chat_manager_getter, service, pick, line or TEST_LINE)
            else:
                path = speak_with_service(config, chat_manager_getter, service, voice_type or "MaleNord", line or TEST_LINE)
            return gr.update(value=path, visible=True), ""
        except Exception as e:
            log.warning(f"Voice test failed: {e}")
            return gr.update(value=None, visible=False), f"Could not play the line: {e}"

    def on_service(service: str, voice_type: str | None):
        choices = _voice_types_for(service, config.save_folder, clips)
        value = voice_type if voice_type in choices else _default_voice_type(choices)
        return gr.update(choices=choices, value=value), gr.update(visible=_clones(service))

    def clips_changed() -> str:
        """When the voice types' clips last changed: read every 3 seconds, like the lists under Customization per voice type and per NPC"""
        try:
            return str(os.stat(os.path.join(clips.folder, VOICE_TYPES)).st_mtime_ns)
        except OSError:
            return "0"

    play_btn.click(on_play, inputs=[service_ui, voice_type_ui, line_ui], outputs=[audio_ui, status_ui], show_api=False) # Gradio 4.28 can't describe an Audio player in its API page, which breaks the page
    service_ui.change(on_service, inputs=[service_ui, voice_type_ui], outputs=[voice_type_ui, only_ready_ui], show_progress="hidden", show_api=False)
    clips_changed_ui.change(lambda service: gr.update(choices=_voice_types_for(service, config.save_folder, clips)), inputs=[service_ui],
                            outputs=[voice_type_ui], show_progress="hidden", show_api=False)
    page = gr.context.Context.root_block
    if page is not None: # also right when the page opens, as the list was made when Mantella started
        page.load(clips_changed, outputs=[clips_changed_ui], every=3, show_progress="hidden", show_api=False)


def render_voice_setups_editor(config: ConfigLoader, service_dropdowns: list[tuple[ConfigValueSelection, Any]], other_service_lists: list[Any] | None = None,
                               selected: str | None = None) -> Any:
    """The list of the player's voice setups: add, edit, remove and test them. Returns the Setup to edit list
    *service_dropdowns*: the TTS Service and Backup Voice Service settings with their dropdowns, which list the setups too
    *other_service_lists*: other dropdowns of voice services that aren't settings
    *selected*: the setup shown first, e.g. the TTS Service's when it is a setup. Else the first saved one"""
    other_service_lists = other_service_lists or []
    setups = voice_setups_for(config.save_folder)
    names = setups.names()
    first = next((name for name in names if normalize_service_name(name) == normalize_service_name(selected)), names[0] if names else None)
    ready_made = ready_made_setups()
    style_choices = [(label, value) for value, label in STYLES.items()]
    voice_types = voice_type_choices(config.save_folder)

    def clip_choices_for(style: str) -> list[tuple[str, str]]:
        """XTTS-style programs can't take the clip itself"""
        return [(label, value) for value, label in CLIP_MODES.items() if not (style == XTTS and value == CLIP_BASE64)]

    def edit_choices() -> list[str]:
        return [NEW_SETUP] + setups.names()

    initial = (setups.get(first) if first else None) or empty_setup()
    with gr.Row():
        edit_ui = gr.Dropdown(choices=edit_choices(), value=first or NEW_SETUP, label="Setup to edit", allow_custom_value=False, scale=3)
        ready_ui = gr.Dropdown(choices=[s["name"] for s in ready_made], value=None, label="Ready-made setups", allow_custom_value=False, scale=3)
        add_ready_btn = gr.Button("Use ready-made", size="sm", scale=1)
    ready_about_ui = gr.Markdown("")
    name_ui = gr.Text(value=initial["name"], label="Name", placeholder="Kokoro on my PC", max_lines=1,
                      info="Any name. You pick it in the TTS Service list, or type it in a character's tts_service column.")
    with gr.Row():
        style_ui = gr.Dropdown(choices=style_choices, value=initial["style"], label="Request style", allow_custom_value=False,
                               info="How Mantella talks to the program. Most programs and websites: OpenAI-style. "
                                    "XTTS servers and programs like them: XTTS-style.")
        url_ui = gr.Text(value=initial["url"], label="Address", placeholder="http://127.0.0.1:8080", max_lines=1,
                         info="Where the program answers. On this PC, e.g. http://127.0.0.1:8080 (llama.cpp) or http://127.0.0.1:8020 (XTTS). "
                              "A website: the address from its instructions, without /v1/audio/speech, e.g. https://api.openai.com")
    check_btn = gr.Button("Check address", size="sm")
    openai_only = initial["style"] != XTTS
    with gr.Row():
        key_ui = gr.Text(value=initial["key_file"], label="Key file", placeholder="OPENAI_VOICE_KEY.txt", max_lines=1, visible=openai_only,
                         info="Websites that need a key only: the name of a text file holding your key, next to GPT_SECRET_KEY.txt. "
                              "Empty for a program on this PC.")
        model_ui = gr.Text(value=initial["model"], label="Model", placeholder="tts-1", max_lines=1, visible=openai_only,
                           info="The model name the program expects, e.g. tts-1 (OpenAI) or tts (llama.cpp). Empty if it has only one.")
    with gr.Row():
        clip_field_ui = gr.Text(value=initial["clip_field"], label="Clip field name", placeholder="speaker_ref_b64", max_lines=1, visible=openai_only,
                                info="What the program's instructions call the voice clip: speaker_ref_b64 (llama.cpp) or voice_ref (audio.cpp). "
                                     "Empty if it can't clone voices: the male and female voices below then always speak.")
        clip_mode_ui = gr.Dropdown(choices=clip_choices_for(initial["style"]), value=initial["clip_mode"] or None, label="How to send the clip",
                                   allow_custom_value=False,
                                   info="The clip itself: works everywhere, websites too. Its place on this PC: for a program on this PC that reads files. "
                                        "The voice type's name: for a program that keeps its own clips, named after the voice types.")
    with gr.Row():
        male_ui = gr.Text(value=initial["male_voice"], label="Male voice without a clip", placeholder="onyx", max_lines=1,
                          info="A voice built into the program, used until the NPC's clip arrives, e.g. onyx (OpenAI) or malenord (XTTS).")
        female_ui = gr.Text(value=initial["female_voice"], label="Female voice without a clip", placeholder="nova", max_lines=1,
                            info="The same for women, e.g. nova (OpenAI) or femalenord (XTTS).")
    with gr.Row():
        languages_ui = gr.Text(value=", ".join(initial["languages"]), label="Languages", placeholder="en, de", max_lines=1,
                               info="The languages it speaks, as short codes. Empty if it speaks any, or you're not sure.")
        online_ui = gr.Checkbox(value=initial["runs_online"], label="Runs online",
                                info="Tick for a website. Prepare all voices skips it, so it doesn't run up a bill.")
        volume_ui = gr.Number(value=initial["volume"], label="Volume (%)", precision=0, minimum=10, maximum=300,
                              info="How loud NPCs speak with it. 100 is as loud as Skyrim's own lines.")
        speed_ui = gr.Slider(value=initial["speed"], label=SPEED_LABEL, minimum=SPEED_LOW, maximum=SPEED_HIGH, step=0.1,
                             info="How fast NPCs talk with it. 1 is the voice's own speed; the pitch stays the same.")
    extra_ui = gr.Textbox(value=json.dumps(initial["extra_fields"]) if initial["extra_fields"] else "", label="Extra request settings (JSON)",
                          placeholder='{"seed": 42}', lines=2, max_lines=4,
                          info="Anything else the program accepts, from its instructions. Empty if unsure.")
    error_ui = gr.Markdown("", visible=False, elem_classes="constraint-violation")
    with gr.Row():
        save_btn = gr.Button("Save", variant="primary", size="sm")
        delete_btn = gr.Button("Delete", variant="secondary", size="sm")
    with gr.Row():
        test_voice_ui = gr.Dropdown(choices=voice_types, value=_default_voice_type(voice_types), label="Voice type to test with", allow_custom_value=True)
        test_btn = gr.Button("Hear it", size="sm")
    test_audio_ui = gr.Audio(label="Test", show_label=False, interactive=False, type="filepath")
    status_ui = gr.Markdown("")

    boxes = [name_ui, style_ui, url_ui, key_ui, model_ui, clip_field_ui, clip_mode_ui, male_ui, female_ui, languages_ui, online_ui, extra_ui, volume_ui,
             speed_ui]
    service_outputs = [ui for _, ui in service_dropdowns] + other_service_lists

    def from_boxes(name, style, url, key_file, model, clip_field, clip_mode, male, female, languages, online, extra, volume, speed) -> dict[str, Any]:
        return {"name": name, "style": style, "url": url, "key_file": key_file, "model": model, "clip_field": clip_field, "clip_mode": clip_mode or "",
                "male_voice": male, "female_voice": female, "languages": languages or "", "runs_online": bool(online), "extra_fields": extra or "",
                "volume": 100 if volume is None else volume, "speed": SPEED_DEFAULT if speed is None else speed}

    def to_boxes(setup: dict[str, Any]) -> list[Any]:
        return [setup["name"], setup["style"], setup["url"], setup["key_file"], setup["model"], setup["clip_field"], setup["clip_mode"] or None,
                setup["male_voice"], setup["female_voice"], ", ".join(setup["languages"]), setup["runs_online"],
                json.dumps(setup["extra_fields"]) if setup["extra_fields"] else "", setup["volume"], setup["speed"]]

    def other_names(selected: str) -> list[str]:
        return [s.name for s in voice_services.all() if not (selected != NEW_SETUP and normalize_service_name(s.name) == normalize_service_name(selected))]

    def errors_panel(found: list[str]):
        return gr.update(value="\n".join(f"- {p}" for p in found), visible=bool(found))

    def service_updates(renamed_from: str | None = None, renamed_to: str | None = None) -> list[Any]:
        updates = []
        for selection, _ in service_dropdowns:
            TTSDefinitions.refresh_voice_service_options(selection)
            if renamed_from and renamed_to and normalize_service_name(selection.value) == normalize_service_name(renamed_from):
                selection.value = renamed_to
            updates.append(gr.update(choices=selection.options, value=selection.value))
        for _ in other_service_lists:
            updates.append(gr.update(choices=voice_services.names()))
        return updates

    def on_edit(selected: str):
        setup = setups.get(selected) if selected and selected != NEW_SETUP else empty_setup()
        return [*to_boxes(setup or empty_setup()), errors_panel([]), ""]

    def on_change(selected: str, *values):
        return errors_panel(problems(from_boxes(*values), other_names(selected)))

    def on_ready_pick(ready_name: str):
        for setup in ready_made:
            if setup["name"] == ready_name:
                return setup.get("about", "")
        return ""

    def on_use_ready(ready_name: str):
        for setup in ready_made:
            if setup["name"] == ready_name:
                return [gr.update(value=NEW_SETUP), *to_boxes(setup), f"Filled in the ready-made setup '{ready_name}'. Press Save to keep it."]
        return [gr.update(), *[gr.update() for _ in boxes], "Pick a ready-made setup first."]

    def on_style(style: str, clip_mode: str | None):
        """XTTS-style programs don't use a key, a model or a clip box, and can't take the clip itself"""
        openai = style != XTTS
        mode = CLIP_NAME if not openai and clip_mode == CLIP_BASE64 else clip_mode
        return (gr.update(visible=openai), gr.update(visible=openai), gr.update(visible=openai),
                gr.update(choices=clip_choices_for(style), value=mode))

    def on_check(url: str):
        result = check_address(url)
        filled = result.filled
        def value_or_keep(key: str):
            return gr.update(value=filled[key]) if key in filled else gr.update()
        return [value_or_keep("style"), value_or_keep("model"), value_or_keep("clip_field"), value_or_keep("clip_mode"), result.message]

    def on_save(selected: str, *values):
        setup = from_boxes(*values)
        old_name = selected if selected != NEW_SETUP else None
        ok, message = setups.save(setup, old_name)
        if not ok:
            return [message, errors_panel(problems(setup, other_names(selected))), gr.update(), *[gr.update() for _ in service_outputs]]
        saved = normalized(setup)["name"]
        config.notify_voice_services_changed() # the next line uses the setup as saved now
        info = voice_services.find(saved)
        if info is not None and not info.speaks(config.language):
            message += f" Note: it isn't set up for your Language setting ({config.language}), so its lines may sound wrong."
        return [message, errors_panel([]), gr.update(choices=edit_choices(), value=saved), *service_updates(old_name, saved)]

    def on_delete(selected: str):
        if not selected or selected == NEW_SETUP:
            return ["Pick a saved setup to delete.", gr.update(), *[gr.update() for _ in service_outputs]]
        in_use = [selection.name for selection, _ in service_dropdowns if normalize_service_name(selection.value) == normalize_service_name(selected)]
        ok, message = setups.delete(selected, in_use)
        if not ok:
            return [message, gr.update(), *[gr.update() for _ in service_outputs]]
        config.notify_voice_services_changed() # an NPC whose tts_service column named it goes back to the TTS Service
        return [message, gr.update(choices=edit_choices(), value=NEW_SETUP), *service_updates()]

    def on_test(selected: str, voice_type: str, *values):
        setup = from_boxes(*values)
        found = problems(setup, other_names(selected))
        if found:
            return None, " ".join(found)
        try:
            return speak_with_setup(config, to_service_info(normalized(setup)), voice_type or "MaleNord"), ""
        except Exception as e:
            log.warning(f"Voice setup test failed: {e}")
            return None, f"Could not play the line: {e}"

    edit_ui.input(on_edit, inputs=[edit_ui], outputs=[*boxes, error_ui, status_ui], show_progress="hidden")
    for box in boxes:
        box.change(on_change, inputs=[edit_ui, *boxes], outputs=[error_ui], show_progress="hidden")
    ready_ui.change(on_ready_pick, inputs=[ready_ui], outputs=[ready_about_ui], show_progress="hidden")
    add_ready_btn.click(on_use_ready, inputs=[ready_ui], outputs=[edit_ui, *boxes, status_ui])
    style_ui.change(on_style, inputs=[style_ui, clip_mode_ui], outputs=[key_ui, model_ui, clip_field_ui, clip_mode_ui], show_progress="hidden")
    check_btn.click(on_check, inputs=[url_ui], outputs=[style_ui, model_ui, clip_field_ui, clip_mode_ui, status_ui])
    save_btn.click(on_save, inputs=[edit_ui, *boxes], outputs=[status_ui, error_ui, edit_ui, *service_outputs])
    delete_btn.click(on_delete, inputs=[edit_ui], outputs=[status_ui, edit_ui, *service_outputs])
    test_btn.click(on_test, inputs=[edit_ui, test_voice_ui, *boxes], outputs=[test_audio_ui, status_ui], show_api=False) # Gradio 4.28 can't describe an Audio player in its API page, which breaks the page
    return edit_ui


def render_word_lists_editor(config: ConfigLoader, chat_manager_getter: Callable[[], Any]) -> None:
    words = shared_word_lists(pronunciation_folder(config.save_folder))
    headers = ["Word", "Say as", "Kind", "Service", "Language", "File"]

    def rows() -> list[list[str]]:
        return [[e.word, e.say_as, e.kind, e.service or ALL, e.language or ALL, e.file_name] for e in words.entries()]

    initial_rows = rows()
    services = _newer_service_names()
    voice_types = voice_type_choices(config.save_folder)
    with gr.Accordion("How it works", open=False, elem_classes="help-fold"):
        gr.Markdown("- **Only the speech changes.** The subtitles and what NPCs remember keep the normal spelling.\n"
                    "- **Works from the next line.**\n"
                    "- **my_words.csv** is your list: updates never touch it, and it wins over Mantella's. "
                    "**mantella_words.csv** is Mantella's list, replaced by updates. Any other list you put in the folder is used too.\n"
                    "- **Works with** Pocket TTS and your voice setups. Piper, XTTS and xVASynth don't use the lists.\n"
                    f"- **The lists are in** `{words.folder}`.")
    gr.Markdown("Pick a row to try it, or type a new word below.")
    table_ui = gr.Dataframe(value=initial_rows or [["", "", "", "", "", ""]], headers=headers, interactive=False, wrap=True,
                            elem_classes="mantella-quiet-table") # the same thin, only-when-needed scrollbar as the other tables (style.css)
    rows_state = gr.State(initial_rows)
    reload_btn = gr.Button("Reload the lists", size="sm")
    with gr.Row():
        word_ui = gr.Text(label="Word", max_lines=1)
        say_as_ui = gr.Text(label="Say it as (e.g. Doh-vah-keen)", max_lines=1)
        kind_ui = gr.Dropdown(choices=[("Spelling", SPELLING), ("Sound symbols", IPA)], value=SPELLING, label="Kind", allow_custom_value=False)
    with gr.Row():
        for_service_ui = gr.Dropdown(choices=[ALL] + services, value=ALL, label="Only for this voice service", allow_custom_value=False)
        language_ui = gr.Text(value="", label=f"Only for this language (e.g. {config.language}; empty for all)", max_lines=1)
    with gr.Row():
        hear_service_ui = gr.Dropdown(choices=services, value=services[0] if services else None, label="Hear it with", allow_custom_value=False)
        hear_voice_ui = gr.Dropdown(choices=voice_types, value=_default_voice_type(voice_types), label="Voice type", allow_custom_value=True)
    with gr.Row():
        play_btn = gr.Button("Hear it fixed", variant="primary", size="sm")
        play_plain_btn = gr.Button("Hear it unfixed", size="sm")
        add_btn = gr.Button("Add to my list", size="sm")
    audio_ui = gr.Audio(label="Result", show_label=False, interactive=False, type="filepath")
    status_ui = gr.Markdown("" if services else "Only Pocket TTS and your voice setups use these lists. Add a voice setup to hear a word here.")

    def on_reload():
        current = rows()
        return current or [["", "", "", "", "", ""]], current, gr.update(choices=[ALL] + _newer_service_names()), gr.update(choices=_newer_service_names())

    def on_select(current_rows: list[list[str]], evt: gr.SelectData):
        row = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        if not current_rows or row >= len(current_rows):
            return [gr.update()] * 5
        word, say_as, kind, service, language, _ = current_rows[row]
        return word, say_as, kind, service if service in [ALL] + _newer_service_names() else ALL, "" if language == ALL else language

    def play(service: str, voice_type: str, word: str, spoken: str, ipa_word: tuple[str, str] | None = None):
        if not service:
            return None, "Pick a voice service to hear it with."
        if not word:
            return None, "Type a word first."
        try:
            language = config.language
            return speak_with_service(config, chat_manager_getter, service, voice_type or "MaleNord", word_line(spoken, language),
                                      use_word_lists=False, ipa_word=ipa_word), ""
        except NoSoundSymbols as e:
            return None, f"{e} Add a spelling for this word too."
        except Exception as e:
            log.warning(f"Word test failed: {e}")
            return None, f"Could not play the line: {e}"

    def on_play(service: str, voice_type: str, word: str, say_as: str, kind: str):
        info = voice_services.find(service)
        if kind == IPA and say_as:
            if info is None or not info.accepts_ipa:
                return None, f"{service} doesn't take sound symbols. Add a spelling for this word too."
            return play(service, voice_type, word, word, (word.strip(), say_as.strip())) # each service writes them its own way
        return play(service, voice_type, word, say_as or word)

    def on_play_plain(service: str, voice_type: str, word: str):
        return play(service, voice_type, word, word)

    def on_add(word: str, say_as: str, kind: str, for_service: str, language: str):
        if not word.strip() or not say_as.strip():
            return "Type the word and what to say instead.", gr.update(), gr.update()
        words.add_to_my_list(word, say_as, kind, "" if for_service == ALL else for_service, language)
        current = rows()
        return f"Added {word} to your list (my_words.csv). It is used from the next line.", current, current

    reload_btn.click(on_reload, outputs=[table_ui, rows_state, for_service_ui, hear_service_ui])
    table_ui.select(on_select, inputs=[rows_state], outputs=[word_ui, say_as_ui, kind_ui, for_service_ui, language_ui], show_progress="hidden")
    play_btn.click(on_play, inputs=[hear_service_ui, hear_voice_ui, word_ui, say_as_ui, kind_ui], outputs=[audio_ui, status_ui], show_api=False) # Gradio 4.28 can't describe an Audio player in its API page, which breaks the page
    play_plain_btn.click(on_play_plain, inputs=[hear_service_ui, hear_voice_ui, word_ui], outputs=[audio_ui, status_ui], show_api=False) # Gradio 4.28 can't describe an Audio player in its API page, which breaks the page
    add_btn.click(on_add, inputs=[word_ui, say_as_ui, kind_ui, for_service_ui, language_ui], outputs=[status_ui, table_ui, rows_state])
