"""The voice clips list and the Pocket TTS part of the Text-to-Speech page"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from typing import Any, Callable

import gradio as gr

from src.config.config_loader import ConfigLoader
from src.tts import pocket_tts
from src.tts.test_line import TEST_LINE, speak_with_clip
from src.tts.voice_folders import voice_clips_folder
from src.tts.voice_picker import OWN_CLIP, TRIAL_PREFIX, VOICE_TYPE_CLIP, VoicePick
from src.tts.voice_setups import read_key_file, save_key_file
from src.voice_clips.clip_store import NPCS, VOICE_TYPES, ClipInfo, ClipStore, shared_clip_store
from src.voice_clips.picks import KEEP_MINE, TAKE_THEIRS, export_picks, import_picks
from src.voice_clips.preparing import preparation_for
from src.voice_clips.requests import clip_requests_for

LATER = "the next time you talk to someone in game"


def spaced(name: str) -> str:
    """FemaleYoungEager -> Female Young Eager"""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def clip_choices(store: ClipStore) -> list[tuple[str, str]]:
    """The voices to change: voice types (shared by their NPCs) and NPCs with a voice of their own, by name.
    Two NPCs of the same name are told apart by their game ID"""
    npcs = store.entries(NPCS)
    names = [e.info.name or e.key for e in npcs]
    choices = [(f"{spaced(e.info.name or e.key)} (voice type)", f"{VOICE_TYPES}/{e.key}") for e in store.entries(VOICE_TYPES)]
    for e, name in zip(npcs, names):
        where = f", {e.info.npc_plugin} {e.info.npc_local_id}" if names.count(name) > 1 and e.info.npc_plugin else ""
        choices.append((f"{name} (own voice{where})", f"{NPCS}/{e.key}"))
    return choices


def npc_choices(store: ClipStore) -> list[tuple[str, str]]:
    have_own = {e.key for e in store.entries(NPCS)}
    found = [(f"{v.get('name') or k} ({v.get('voice_type', '')})", k) for k, v in store.npcs_seen().items() if k not in have_own]
    return sorted(found, key=lambda c: c[0].lower())


def about_clip(kind: str, key: str, info: ClipInfo) -> str:
    """What the player hears in the first player: the clip the voice is cloned from now"""
    whose = f"{info.name or key}'s voice" if kind == NPCS else f"The {spaced(info.name or key)} voice"
    return f"**▶ {whose} is cloned from this clip right now.**"


TRYING_NOTE = "**▶ Trying another clip.** Not used yet. Press **Clone from this clip** to use it."


def clip_voice(store: ClipStore, kind: str, key: str, line_to_hear: str | None = None) -> VoicePick | None:
    """The voice to try: the clip as NPCs use it (the same voice, so one made in game is reused), or a line from the list before it is used.
    A line from the list gets a name of its own, so trying it never throws away the voice made from the clip"""
    pick_kind = OWN_CLIP if kind == NPCS else VOICE_TYPE_CLIP
    if line_to_hear:
        return VoicePick(pick_kind, f"{TRIAL_PREFIX}{key}", line_to_hear)
    clip = store.clip(kind, key)
    return VoicePick(pick_kind, key, clip) if clip else None


def render_voice_clips_editor(config: ConfigLoader, chat_manager_getter: Callable[[], Any] = lambda: None) -> None:
    store = shared_clip_store(voice_clips_folder(config.save_folder))
    requests = clip_requests_for(store)
    preparation = preparation_for(store, requests)
    with gr.Accordion("How it works", open=False, elem_classes="voice-clips-subsection"):
        gr.Markdown("- **NPCs of the same voice type share one clip**, so they sound alike. You can give anyone their own voice below.\n"
                    "- **Voice mods carry over.** Clips are taken from what your game plays, so mod voices and voice replacers are cloned too.\n"
                    "- **Some changes wait for the game.** They happen the next time you talk to someone in game or load a save.\n"
                    "- **A new voice type** uses a stand-in voice for about its first line. **Prepare all voices**, at the bottom, avoids that.\n"
                    "- **NPCs with no voice lines**, such as an unvoiced follower, use the voice type in their character file "
                    "(the skyrim_voice_folder column), or else one that fits their race and gender.\n"
                    "- **Works with** Pocket TTS and your voice setups. Piper, XTTS and xVASynth don't use clips.\n"
                    "- **Needs** MantellaVoiceClips.dll, which comes with the game add-on.\n"
                    f"- **Clips are saved in** `{store.folder}`.")

    gr.Markdown("### Change a voice\n"
                "Pick a voice to hear the clip it's cloned from.")
    with gr.Accordion("How to change a voice", open=False, elem_classes="voice-clips-subsection"):
        gr.Markdown("Every voice is cloned from a clip: a few seconds of speech from your game. To make a voice sound different, "
                    "clone it from another clip.\n\n"
                    "1. Pick the voice you want to change. You hear the clip it's cloned from now.\n"
                    "2. Pick a clip under **Other clips you can clone from** to hear it.\n"
                    "3. Press **Hear the cloned voice** to hear how the voice would sound.\n"
                    "4. Like it? Press **Clone from this clip**. NPCs sound like that from their next line.\n\n"
                    "Changed your mind? The old clip is now in the list. Pick it and clone from it again.\n\n"
                    f"**No clips in the list?** Press **Find more clips**. 4 more show up {LATER}; then press **Refresh**. "
                    "Clips you removed before are skipped. Press **Bring back removed clips** to let them be found again.")
    with gr.Row(equal_height=False, elem_classes="voice-clips-row"):
        clip_ui = gr.Dropdown(choices=clip_choices(store), value=None, label="Voice to change", allow_custom_value=False, scale=4,
                              info="A voice type is shared by every NPC who has it. NPCs with a voice of their own are listed by name.")
        reload_btn = gr.Button("Refresh", size="sm", scale=1, min_width=160)
    about_ui = gr.Markdown("")
    clip_audio_ui = gr.Audio(label="Clip", show_label=False, interactive=False, type="filepath", visible=False)
    with gr.Row(): # only for an NPC's own voice: what to do with the voice above, named after the NPC and their voice type when shown
        voice_type_btn = gr.Button("Give this NPC's voice to their whole voice type", size="sm", visible=False)
        remove_btn = gr.Button("Go back to their voice type's voice", size="sm", visible=False)
    candidate_ui = gr.Dropdown(choices=[], value=None, label="Other clips you can clone from", allow_custom_value=False)
    trying_note_ui = gr.Markdown(TRYING_NOTE, visible=False)
    candidate_audio_ui = gr.Audio(label="Clip from the list", show_label=False, interactive=False, type="filepath", visible=False)
    with gr.Row():
        use_line_btn = gr.Button("Clone from this clip", size="sm", interactive=False)
        more_btn = gr.Button("Find more clips", size="sm", interactive=False)
        bring_back_btn = gr.Button("Bring back removed clips", size="sm", interactive=False)
    with gr.Row(equal_height=False, elem_classes="voice-clips-row"):
        sentence_ui = gr.Text(value=TEST_LINE, label="Test sentence", max_lines=2, scale=4)
        speak_btn = gr.Button("Hear the cloned voice", size="sm", scale=1, min_width=160, interactive=False)
    change_msg = gr.Markdown("") # above the player, so it says what the player plays
    spoken_ui = gr.Audio(label="Spoken", show_label=False, interactive=False, type="filepath", visible=False)

    gr.Markdown("### Give an NPC their own voice\n"
                "Make one NPC sound different from the rest of their voice type.")
    with gr.Accordion("How to give an NPC their own voice", open=False, elem_classes="voice-clips-subsection"):
        gr.Markdown("Each NPC shares one voice with everyone of their voice type, so changing it changes them all.\n\n"
                    "1. Pick the NPC and press **Give this NPC their own voice**. It's cloned from their own lines, "
                    f"and ready {LATER}.\n"
                    "2. They now show up by name under **Change a voice**. Change their voice there without touching anyone else.\n\n"
                    "**To undo**, pick them under **Change a voice** and press **Go back to the … voice**.\n\n"
                    "**Want their voice for everyone of their voice type?** Press **Give …'s voice to every … NPC** there.")
    with gr.Row(equal_height=False, elem_classes="voice-clips-row"):
        npc_ui = gr.Dropdown(choices=npc_choices(store), value=None, label="NPCs who have spoken", allow_custom_value=False, scale=4)
        give_btn = gr.Button("Give this NPC their own voice", size="sm", scale=1, min_width=160)
    give_msg = gr.Markdown("")

    with gr.Accordion("Share voices with a friend", open=False, elem_classes="voice-clips-subsection"):
        gr.Markdown("Like how your NPCs sound? Save your voices to a small file and send it to a friend. "
                    "When they load it, their NPCs get the same voices. Voices from mods they don't have are skipped.")
        export_btn = gr.Button("Save my voices to a file", size="sm")
        export_file_ui = gr.File(label="Your voices file", interactive=False, visible=False)
        gr.Markdown(f"**Got a file from a friend?** Load it here. Their voices are ready {LATER}.")
        with gr.Row(equal_height=False):
            import_file_ui = gr.File(label="Friend's voices file", type="filepath", file_types=[".json"], height=120)
            when_ui = gr.Radio(choices=[("Keep mine", KEEP_MINE), ("Use theirs", TAKE_THEIRS)], value=KEEP_MINE,
                               label="For voices you both have")
        import_btn = gr.Button("Load their voices", size="sm")
        share_msg = gr.Markdown("")

    gr.Markdown("### Prepare all voices\n"
                "Clone every voice ahead of time, so nobody starts with a stand-in. It starts the next time you talk to someone in game "
                "or load a save, then works in the background while you play: about 10 minutes for 100 voice types with Pocket TTS.")
    prepare_btn = gr.Button("Prepare all voices", size="sm")
    prepare_msg = gr.Markdown(preparation.progress(), elem_classes="voice-clips-progress")
    # The count refreshes every 3 seconds while the page is open. Gradio marks a refreshing part with a green border, which an empty
    # count squashes into a green line; style.css hides it (voice-clips-progress)
    page = gr.context.Context.root_block
    if page is not None:
        page.load(preparation.progress, outputs=[prepare_msg], every=3, show_progress="hidden", show_api=False)

    def split(choice: str | None) -> tuple[str, str]:
        kind, _, key = (choice or "").partition("/")
        return kind, key

    def show(choice: str | None, message: str = ""):
        """Everything in "Change a voice" for the picked clip"""
        kind, key = split(choice)
        pocket_tts.remove_trial_voices(config.save_folder, keep=key or None) # voices made to hear other clips' lists aren't needed any more
        if not key:
            return ("", gr.update(value=None, visible=False), gr.update(choices=[], value=None), gr.update(value=None, visible=False),
                    gr.update(interactive=False), gr.update(interactive=False), gr.update(visible=False), gr.update(interactive=False),
                    gr.update(value=None, visible=False), message, gr.update(interactive=False), gr.update(visible=False), gr.update(visible=False))
        lines =[(f"{os.path.basename(info.path) or os.path.basename(path)} ({info.seconds:.1f}s)", path) for path, info in store.candidates(kind, key)]
        clip = store.clip(kind, key)
        info = store.info(kind, key)
        about = about_clip(kind, key, info) + more_lines_note(kind, key, bool(lines))
        return (about, gr.update(value=clip, visible=bool(clip)), gr.update(choices=lines, value=None),
                gr.update(value=None, visible=False), gr.update(interactive=False), gr.update(interactive=True),
                gr.update(visible=kind == NPCS, value=f"Go back to the {spaced(info.voice_type)} voice" if info.voice_type else "Go back to their voice type's voice"),
                gr.update(interactive=bool(clip)), gr.update(value=None, visible=False), message, gr.update(interactive=bool(info.turned_down)), gr.update(visible=False),
                gr.update(visible=kind == NPCS and bool(info.voice_type), value=f"Give {info.name or 'this NPC'}'s voice to every {spaced(info.voice_type)} NPC"))

    def more_lines_note(kind: str, key: str, has_lines: bool) -> str:
        if requests.waiting_for_more(kind, key):
            return f"\n\nYou asked for more clips. They show up {LATER}; then press **Refresh**."
        if requests.no_more_lines(kind, key) and not has_lines:
            return "\n\nThere are no more clips for this voice, so this clip stays as it is."
        return ""

    def on_reload(choice: str | None):
        """The lists, and the picked clip with the lines the game sent since"""
        return gr.update(choices=clip_choices(store)), gr.update(choices=npc_choices(store)), *show(choice)

    def on_candidate(path: str | None):
        return gr.update(value=path, visible=bool(path)), gr.update(interactive=bool(path)), gr.update(visible=bool(path))

    def on_speak(choice: str | None, candidate: str | None, sentence: str):
        kind, key = split(choice)
        pick = clip_voice(store, kind, key, candidate) if key else None
        if pick is None:
            return gr.update(value=None, visible=False), "Pick a voice clip first."
        service = config.tts_service_name
        try:
            path = speak_with_clip(config, chat_manager_getter, service, pick, (sentence or "").strip() or TEST_LINE)
        except Exception as e:
            logging.warning(f"Hear it speak failed: {e}")
            return gr.update(value=None, visible=False), f"Could not speak: {e}"
        which = "the clip you're trying" if candidate else "the clip in use right now"
        return gr.update(value=path, visible=True), f"**▶ The test sentence, in the voice {service} cloned from {which}.**"

    def on_use_line(choice: str | None, candidate: str | None):
        kind, key = split(choice)
        if not key or not candidate:
            return show(choice, "Pick a clip from the list first.")
        store.choose_candidate(kind, key, candidate)
        pocket_tts.use_trial_voice(config.save_folder, key, store.clip(kind, key) or "")
        return show(choice, "Done. The next line spoken uses the voice cloned from this clip. The old clip is in the list if you want it back.")

    def on_bring_back(choice: str | None):
        kind, key = split(choice)
        if not key:
            return show(choice, "Pick a voice first.")
        count = store.bring_back_turned_down(kind, key)
        return show(choice, f"{count} removed clip(s) can be found again. Press **Find more clips**: they may show up {LATER}.")

    def on_more(choice: str | None):
        kind, key = split(choice)
        if not key:
            return "Pick a voice clip first."
        requests.ask_for_more(kind, key)
        return f"4 more clips show up {LATER}. Then press **Refresh** to see them."

    def on_remove(choice: str | None):
        kind, key = split(choice)
        if kind != NPCS:
            return gr.update(), gr.update(), *show(choice, "Only an NPC's own voice can be removed. To change a voice type's voice, clone from another clip in the list instead.")
        store.remove(kind, key)
        pocket_tts.remove_saved_voices(config.save_folder, key)
        return (gr.update(choices=clip_choices(store), value=None), gr.update(choices=npc_choices(store)),
                *show(None, "Removed. The NPC sounds like the rest of their voice type again."))

    def on_use_for_voice_type(choice: str | None):
        kind, key = split(choice)
        if kind != NPCS:
            return gr.update(), gr.update(), *show(choice, "Pick an NPC with a voice of their own first.")
        npc = store.info(kind, key)
        try:
            voice_type = store.use_npc_clip_for_voice_type(key)
        except ValueError as e:
            return gr.update(), gr.update(), *show(choice, str(e))
        pocket_tts.move_saved_voice(config.save_folder, key, voice_type, store.clip(VOICE_TYPES, voice_type) or "")
        pocket_tts.remove_saved_voices(config.save_folder, key)
        new_choice = f"{VOICE_TYPES}/{voice_type}"
        name = spaced(npc.voice_type)
        return (gr.update(choices=clip_choices(store), value=new_choice), gr.update(choices=npc_choices(store)),
                *show(new_choice, f"Done. Every {name} NPC sounds like this from their next line, {npc.name or 'this NPC'} included. "
                                  f"The old {name} clip is in the list if you want it back."))

    def on_give(npc: str | None):
        seen = store.npcs_seen().get(npc or "")
        if not seen:
            return "Pick an NPC first."
        requests.ask_for_own_clip(seen.get("plugin", ""), seen.get("local_id", ""), seen.get("name", ""), seen.get("voice_type", ""))
        return f"{seen.get('name') or 'This NPC'} gets their own voice {LATER}."

    def on_export():
        path = os.path.join(tempfile.gettempdir(), "mantella_voices.json")
        with open(path, "w", encoding="utf-8") as file:
            json.dump(export_picks(store), file, indent=2)
        return gr.update(value=path, visible=True), "Saved. Download the file above and send it to your friend."

    def on_import(uploaded: str | None, when_different: str):
        if not uploaded:
            return "Add your friend's file first."
        try:
            with open(uploaded, encoding="utf-8") as file:
                asked, kept = import_picks(store, requests, json.load(file), when_different)
        except Exception as e:
            return f"Could not load it: {e}"
        kept_note = f" You kept {kept} of your own." if kept else ""
        return f"{asked} voice(s) arrive {LATER}.{kept_note}"

    def on_prepare():
        if not preparation.in_use:
            return "Pick a voice service that copies voices, such as Pocket TTS, as the TTS Service first, then load a save in game."
        return preparation.prepare_all()

    shown = [about_ui, clip_audio_ui, candidate_ui, candidate_audio_ui, use_line_btn, more_btn, remove_btn, speak_btn, spoken_ui, change_msg, bring_back_btn, trying_note_ui, voice_type_btn]
    # show_api=False: Gradio 4.28 can't describe an Audio player or a file in its API page, which breaks the page.
    # The clip list uses input, not change: change also fires when a button sets the list, and would wipe that button's message
    clip_ui.input(show, inputs=[clip_ui], outputs=shown, show_api=False)
    reload_btn.click(on_reload, inputs=[clip_ui], outputs=[clip_ui, npc_ui, *shown], show_api=False)
    candidate_ui.change(on_candidate, inputs=[candidate_ui], outputs=[candidate_audio_ui, use_line_btn, trying_note_ui], show_api=False)
    use_line_btn.click(on_use_line, inputs=[clip_ui, candidate_ui], outputs=shown, show_api=False)
    bring_back_btn.click(on_bring_back, inputs=[clip_ui], outputs=shown, show_api=False)
    more_btn.click(on_more, inputs=[clip_ui], outputs=[change_msg], show_api=False)
    speak_btn.click(on_speak, inputs=[clip_ui, candidate_ui, sentence_ui], outputs=[spoken_ui, change_msg], show_api=False)
    remove_btn.click(on_remove, inputs=[clip_ui], outputs=[clip_ui, npc_ui, *shown], show_api=False)
    voice_type_btn.click(on_use_for_voice_type, inputs=[clip_ui], outputs=[clip_ui, npc_ui, *shown], show_api=False)
    give_btn.click(on_give, inputs=[npc_ui], outputs=[give_msg], show_api=False)
    export_btn.click(on_export, outputs=[export_file_ui, share_msg], show_api=False)
    import_btn.click(on_import, inputs=[import_file_ui, when_ui], outputs=[share_msg], show_api=False)
    prepare_btn.click(on_prepare, outputs=[prepare_msg], show_api=False)


def render_pocket_tts_panel(config: ConfigLoader) -> None:
    language = pocket_tts.kyutai_language(config.language)
    supported = (config.language or "en").lower()[:2] in pocket_tts.LANGUAGES
    language_note = "" if supported else (f"**Your Language setting ({config.language}) is not one Pocket TTS speaks.** "
                                          "It speaks English, French, German, Portuguese, Italian, Spanish and Dutch.\n\n")
    gr.Markdown(
        language_note
        + "**Turn on voice cloning.** Kyutai's cloning model needs a free Hugging Face sign-in, once:\n"
        "1. Make an account at [huggingface.co](https://huggingface.co/join).\n"
        f"2. Accept Kyutai's terms on [their model page]({pocket_tts.MODEL_PAGE}).\n"
        f"3. Make a token with read access on [your tokens page]({pocket_tts.TOKEN_PAGE}), paste it below and press **Save token**.\n\n"
        "Without it, Pocket TTS still speaks, but every NPC uses one of its built-in voices.\n\n"
        "Pocket TTS is made by [Kyutai](https://kyutai.org) (code MIT, model CC BY 4.0). Kyutai's terms forbid copying a real person's voice without their consent.")
    with gr.Row():
        token_ui = gr.Textbox(value="", type="password", label="Hugging Face token", placeholder="hf_...", max_lines=1)
        save_btn = gr.Button("Save token", size="sm")
        check_btn = gr.Button("Check my sign-in", size="sm")
    status_ui = gr.Markdown("")

    def describe(access: str) -> str:
        if access == pocket_tts.ACCESS_OK:
            return f"Your sign-in works: Pocket TTS can clone voices ({language})."
        if access == pocket_tts.ACCESS_UNKNOWN:
            return "Hugging Face didn't answer. Check your internet connection and try again."
        return pocket_tts.ACCESS_MESSAGES[access]

    def on_check():
        return describe(pocket_tts.check_access(read_key_file(pocket_tts.KEY_FILE), language))

    def on_save(token: str):
        token = (token or "").strip()
        if not token:
            return gr.update(), "Paste your token first."
        try:
            saved = save_key_file(pocket_tts.KEY_FILE, token)
        except OSError as e:
            return gr.update(), f"Could not save the token: {e}"
        config.notify_voice_services_changed() # Pocket TTS starts again with the new token before the next line
        return "", f"Saved in `{saved}`. {describe(pocket_tts.check_access(token, language))}"

    # The box is emptied after saving, and never filled with the saved token, so it isn't shown on screen
    save_btn.click(on_save, inputs=[token_ui], outputs=[token_ui, status_ui], show_api=False)
    check_btn.click(on_check, outputs=[status_ui], show_api=False)
