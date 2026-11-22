"""The voice clips list and the Pocket TTS part of the Text-to-Speech page"""
from __future__ import annotations

import html
import json
import math
import os
import re
import tempfile
from typing import Any, Callable

import gradio as gr

from src.config.config_loader import ConfigLoader
from src.tts import kept_voices, pocket_tts
from src.tts.clip_service import lines_used
from src.tts.registry import VoiceServiceInfo, voice_services
from src.tts.test_line import TEST_LINE, speak_with_clip
from src.tts.voice_folders import voice_clips_folder
from src.tts.voice_picker import OWN_CLIP, VOICE_TYPE_CLIP, VoicePick
from src.tts.voice_setups import read_key_file, save_key_file
from src.tts.model_catalog import ServiceModels, with_model_limits
from src.tts.online_voices import cloned_voices
from src.tts.voice_style import (BOSON_EXPRESSIVENESS, HEYGEN_EXPRESSIVENESS, HEYGEN_SIMILARITY, INWORLD_DELIVERY, IODINE_TEMPERATURE,
                                 SPEED_DEFAULT, SPEED_HIGH, SPEED_LABEL, SPEED_LOW, STYLE_SETTINGS, ServiceStyle, StyleSetting, style_for)
from src.voice_clips.clip_store import (CANDIDATES, MOST_CHOSEN, NPCS, NPCS_SEEN_FILE, VOICE_TYPES, ClipInfo,
                                        ClipStore, clip_seconds, lines_within, shared_clip_store)
from src.voice_clips.picks import KEEP_MINE, TAKE_THEIRS, export_picks, import_picks
from src.voice_clips.preparing import preparation_for
from src.voice_clips.requests import LINES_TO_HEAR, MOST_MORE, clip_requests_for
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VOICES)

LATER = "the next time you talk to someone in game"
PITCH_STEPS = 4 # semitones up or down on the page
SAME_AS_PAGE = "" # a style setting's choice that takes the voice's own value away
# One Talking speed slider for whichever service is shown: it stands for that service's own speed setting (voice_style.SPEEDS, SETUP_SPEED)
SPEED_SLOT = StyleSetting("talking_speed", "", SPEED_LABEL, SPEED_DEFAULT, low=SPEED_LOW, high=SPEED_HIGH)
STYLE_INFO = {
    HEYGEN_SIMILARITY.name: "How closely the voice copies its clips. Changing it clones the voice again.",
    HEYGEN_EXPRESSIVENESS.name: "How much the voice rises and falls: 0 is the flattest, 1 the liveliest.",
    INWORLD_DELIVERY.name: "Stable is even and much the same every time; Creative has a wider range of feeling.",
    BOSON_EXPRESSIVENESS.name: "How much the voice rises and falls.",
    IODINE_TEMPERATURE.name: "Lower is steadier and more alike each time, higher more varied.",
    SPEED_SLOT.name: "How fast this voice talks. 1 is its own speed; the pitch stays the same.",
}


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


CLONED_ROWS = MOST_CHOSEN # the clips a voice is cloned from, at most
OTHER_ROWS = MOST_CHOSEN + MOST_MORE # the voice's other lines: chosen ones the service can't fit, and the ones Find more clips brings

# Play button: one clip plays at a time; pressing it again stops it
PLAY_JS = ("var a=this.nextElementSibling;document.querySelectorAll('audio.vc-audio').forEach(function(x){if(x!==a){x.pause();x.currentTime=0;}});"
           "if(a.paused){a.play();}else{a.pause();a.currentTime=0;}")


def clip_voice(store: ClipStore, kind: str, key: str) -> VoicePick | None:
    """The voice as NPCs use it, so one made in game is reused"""
    clip = store.clip(kind, key)
    return VoicePick(OWN_CLIP if kind == NPCS else VOICE_TYPE_CLIP, key, clip) if clip else None


def how_long(seconds: float) -> str:
    return f"{seconds / 60:g} minutes" if seconds >= 120 else f"{math.ceil(seconds - 0.01):.0f} s"


def takes(info: VoiceServiceInfo) -> str:
    """What a service takes from a voice's clips, for the line under the list"""
    return f"up to {how_long(info.clip_seconds)} from the top" if info.clip_seconds else "the top clip"


def clip_url(store: ClipStore, kind: str, key: str, path: str) -> str:
    """Where the page's play button gets the clip (voice_files_route)"""
    try:
        version = os.stat(path).st_mtime_ns
    except OSError:
        version = 0
    clip = store.clip(kind, key)
    if clip and os.path.abspath(clip) == os.path.abspath(path):
        return f"/voice_clips/{kind}/{key}?v={version}"
    return f"/voice_clips/{kind}/{key}/{os.path.splitext(os.path.basename(path))[0]}?v={version}"


def line_html(url: str, info: ClipInfo, path: str, tag: str = "") -> str:
    """One clip: a play button, the game line's file name and how long it is"""
    words = os.path.basename(info.path or path)
    tag_html = f'<span class="vc-tag">{html.escape(tag)}</span>' if tag else ""
    return (f'<div class="vc-line"><button class="vc-play" title="Play" onclick="{PLAY_JS}">▶</button>'
            f'<audio class="vc-audio" preload="none" src="{html.escape(url)}" onplay="this.previousElementSibling.textContent=\'■\'" '
            f'onpause="this.previousElementSibling.textContent=\'▶\'"></audio>'
            f'<span class="vc-words">{html.escape(words)}</span>{tag_html}<span class="vc-secs">{clip_seconds(path):.1f} s</span></div>')


def view_for(config: ConfigLoader, service_value: str | None) -> tuple[VoiceServiceInfo | None, str]:
    """The service Voice to change shows the clips for: the TTS Service as picked on the page, or the backup when the TTS Service
    doesn't use clips. With a note when it isn't the TTS Service"""
    chosen = voice_services.find(service_value) or voice_services.find(config.chosen_voice_service_name())
    if chosen is not None and chosen.uses_clips:
        return with_model_limits(config, chosen), "" # as the picked model clones
    name = chosen.name if chosen is not None else str(service_value or "")
    backup = voice_services.find(config.chosen_voice_service_name("tts_backup_service"))
    if backup is not None and backup.uses_clips:
        return with_model_limits(config, backup), f"{name} doesn't use clips. These are the clips of your backup, {backup.name}."
    return None, (f"{name} doesn't use clips. To change voices here, pick Pocket TTS, an online service such as ElevenLabs or HeyGen, "
                  "or one of your voice setups as the TTS Service.")


def prepares(service_value: str | None) -> bool:
    """Whether Prepare all voices is shown with this service: Pocket TTS and voice setups on this PC. Shown when no service is known"""
    info = voice_services.find(service_value)
    return info is None or (info.uses_clips and not info.runs_online)


def other_services(config: ConfigLoader, view: VoiceServiceInfo, in_game: list[VoiceServiceInfo]) -> list[tuple[VoiceServiceInfo, str]]:
    """The other services that clone from the same clips: the backup, and any other that spoke a line in game this run (set for
    single NPCs, or the TTS Service still running while another is picked on the page). (service, how it is used)"""
    found: list[tuple[VoiceServiceInfo, str]] = []
    backup = voice_services.find(config.chosen_voice_service_name("tts_backup_service"))
    if backup is not None and backup.uses_clips and backup.name != view.name:
        found.append((with_model_limits(config, backup), "backup"))
    for info in in_game:
        if info.uses_clips and info.name != view.name and all(info.name != f.name for f, _ in found):
            found.append((with_model_limits(config, info), "npcs"))
    return found


def style_heading(config: ConfigLoader, view: VoiceServiceInfo | None) -> str:
    """How this voice sounds, with the model its settings are for"""
    models = ServiceModels.of(view.name) if view is not None else None
    if models is None:
        return "#### How this voice sounds"
    return (f"#### How this voice sounds with {models.picked(config).name}\n"
            f"To set this voice for another model, make {view.name} speak with it first.")


def cloned_for_note(config: ConfigLoader, view: VoiceServiceInfo, key: str) -> str:
    """For a service whose voices only work with the model they were cloned for (Qwen): a voice cloned for another model is cloned
    again the next time it speaks"""
    models = ServiceModels.of(view.name)
    if models is None or not models.clones_per_model or not key:
        return ""
    kept = cloned_voices(config.save_folder, view.name).get(key)
    _, _, cloned_for = str((kept or {}).get("clip") or "").partition("|")
    picked = models.picked(config)
    if not kept or cloned_for == picked.id:
        return ""
    before = models.resolve(cloned_for).name if cloned_for else "another model"
    return f"Cloned for {before}. It's cloned again for {picked.name} the next time it speaks."


def others_note(others: list[tuple[VoiceServiceInfo, str]]) -> str:
    return " ".join(f"Your backup, {info.name}, uses {takes(info)}." if how == "backup" else f"{info.name}, also speaking in game, uses {takes(info)}."
                    for info, how in others)


def render_voice_clips_editor(config: ConfigLoader, chat_manager_getter: Callable[[], Any] = lambda: None, service_ui: Any = None,
                              model_ui: Any = None) -> None:
    """*service_ui*: the TTS Service setting's dropdown, so Voice to change redraws when another service is picked.
    *model_ui*: changes when another Model is picked above, so the voice's settings redraw for that model."""
    store = shared_clip_store(voice_clips_folder(config.save_folder))
    requests = clip_requests_for(store)
    preparation = preparation_for(store, requests)
    with gr.Accordion("How it works", open=False, elem_classes="help-fold"):
        gr.Markdown("- **NPCs of the same voice type share one voice**, so they sound alike. You can give anyone their own voice below.\n"
                    "- **Voice mods carry over.** Clips are taken from what your game plays, so mod voices and voice replacers are cloned too.\n"
                    "- **Some changes wait for the game.** They happen the next time you talk to someone in game or load a save.\n"
                    "- **A new voice type** uses a stand-in voice for about its first line. **Prepare all voices**, at the bottom, avoids that.\n"
                    "- **NPCs with no voice lines**, such as an unvoiced follower, use the voice type in their character file "
                    "(the skyrim_voice_folder column), or else one that fits their race and gender.\n"
                    "- **Works with** Pocket TTS, ElevenLabs, Inworld, HeyGen, Qwen, Boson AI, Iodine and your voice setups. Piper, XTTS and xVASynth "
                    "don't use clips.\n"
                    "- **Pitch and the settings beside it** belong to the voice: every NPC who shares it sounds the same. "
                    "The settings beside Pitch are kept for each model.\n"
                    "- **Needs** MantellaVoiceClips.dll, which comes with the game add-on.\n"
                    f"- **Clips are saved in** `{store.folder}`.")

    with gr.Accordion("How to change a voice", open=False, elem_classes="help-fold"):
        gr.Markdown("Every voice is cloned from clips: a few seconds of speech each, from your game. Pocket TTS and voice setups use one clip; "
                    "Inworld up to 30 seconds of clips; ElevenLabs up to 2 minutes. The list shows the clips your TTS Service uses.\n\n"
                    "1. Pick the voice you want to change. Press **▶** to hear a clip.\n"
                    "2. Pocket TTS and setups: press **Use this one** on another clip. Inworld and ElevenLabs: **Add** a clip, or **Take out** "
                    "one that sounds wrong (a shout, a whisper).\n"
                    "3. Press **Hear the voice** to hear how NPCs sound now. They sound like that from their next line.\n\n"
                    "Changed your mind? A clip you took out or swapped is under **Other clips**. Add it again.\n\n"
                    f"**Want other clips?** Pick how many and press **Find more clips**. The new ones take the place of the other clips {LATER}.")
    clip_ui = gr.Dropdown(choices=clip_choices(store), value=None, label="Voice to change", allow_custom_value=False,
                          info="Pick a voice to hear the clips it's cloned from. A voice type is shared by every NPC who has it. "
                               "NPCs with a voice of their own are listed by name.")
    view_note_ui = gr.Markdown("", visible=False)
    with gr.Group(visible=False, elem_classes="vc-lists") as lists_ui:
        cloned_head_ui = gr.Markdown("")
        cloned_rows = []
        for _ in range(CLONED_ROWS):
            with gr.Row(visible=False, equal_height=True, elem_classes="vc-row") as row:
                line = gr.HTML("")
                button = gr.Button("Take out", size="sm", scale=0, min_width=130, elem_classes="vc-button")
            cloned_rows.append((row, line, button))
        other_head_ui = gr.Markdown("")
        other_rows = []
        for _ in range(OTHER_ROWS):
            with gr.Row(visible=False, equal_height=True, elem_classes="vc-row") as row:
                line = gr.HTML("")
                button = gr.Button("Add", size="sm", scale=0, min_width=130, elem_classes="vc-button")
            other_rows.append((row, line, button))
        with gr.Row(elem_classes="vc-more"):
            more_btn = gr.Button("Find more clips", size="sm", scale=0, min_width=160)
            gr.HTML('<span class="vc-more-label">How many</span>', elem_classes="vc-more-label-box")
            more_count_ui = gr.Number(value=LINES_TO_HEAR, minimum=1, maximum=MOST_MORE, step=1, precision=0, label="How many",
                                      show_label=False, container=False, scale=0, min_width=72, elem_classes="vc-more-count")
    others_ui = gr.Markdown("")
    style_head_ui = gr.Markdown("#### How this voice sounds", visible=False)
    # Pitch, then the shown service's style settings (voice_style.py) for this voice only, side by side: each starts at the page's
    style_uis: list[tuple[StyleSetting, Any, Any]] = [] # (setting, its control, the button that goes back to the page's: sliders only)
    with gr.Row(equal_height=True):
        pitch_ui = gr.Slider(minimum=-PITCH_STEPS, maximum=PITCH_STEPS, step=0.5, value=0, label="Pitch", visible=False, # shown once a voice is picked
                             info="Deeper below 0, higher above 0. 0 keeps the clips' own. Heard with Hear the voice, and in game from the next line.")
        for setting in [*(s for s in STYLE_SETTINGS if s.per_voice and s.label != SPEED_LABEL), SPEED_SLOT]:
            if setting.choices:
                control = gr.Dropdown(choices=[], value=None, label=setting.label, info=STYLE_INFO[setting.name], visible=False, allow_custom_value=False)
            else:
                control = gr.Slider(minimum=setting.low, maximum=setting.high, step=0.1, value=setting.default, label=setting.label,
                                    info=STYLE_INFO[setting.name], visible=False)
            style_uis.append((setting, control, None))
    with gr.Row(): # under the sliders: shown only while the voice has its own value
        for i, (setting, control, _) in enumerate(style_uis):
            if not setting.choices:
                style_uis[i] = (setting, control, gr.Button("", size="sm", visible=False))
    with gr.Row(): # only for an NPC's own voice: what to do with the voice above, named after the NPC and their voice type when shown
        voice_type_btn = gr.Button("Give this NPC's voice to their whole voice type", size="sm", visible=False)
        remove_btn = gr.Button("Go back to their voice type's voice", size="sm", visible=False)
    with gr.Row(equal_height=False, elem_classes="voice-clips-row"):
        sentence_ui = gr.Text(value=TEST_LINE, label="Test sentence", max_lines=2, scale=4)
        speak_btn = gr.Button("Hear the voice", size="sm", scale=1, min_width=160, interactive=False)
    change_msg = gr.Markdown("") # above the player, so it says what the player plays
    spoken_ui = gr.Audio(label="Spoken", show_label=False, interactive=False, type="filepath", visible=False)
    cloned_paths_ui = gr.State([])
    other_paths_ui = gr.State([])
    seen_ui = gr.Textbox(value="", visible=False) # changes when the game sends clips, so the lists redraw by themselves

    gr.Markdown("### Give an NPC their own voice\n"
                "Make one NPC sound different from the rest of their voice type.")
    with gr.Accordion("How to give an NPC their own voice", open=False, elem_classes="help-fold"):
        gr.Markdown(
                    "1. Pick the NPC and press **Give this NPC their own voice**. It's cloned from their own lines, "
                    f"and ready {LATER}.\n"
                    "2. They now show up by name under **Voice to change**. Change their voice there without touching anyone else.\n\n"
                    "**To undo**, pick them under **Voice to change** and press **Go back to the … voice**.\n\n"
                    "**Want their voice for everyone of their voice type?** Press **Give …'s voice to every … NPC** there.")
    with gr.Row(equal_height=False, elem_classes="voice-clips-row"):
        npc_ui = gr.Dropdown(choices=npc_choices(store), value=None, label="NPCs who have spoken", allow_custom_value=False, scale=4)
        give_btn = gr.Button("Give this NPC their own voice", size="sm", scale=1, min_width=160)
    give_msg = gr.Markdown("")

    with gr.Accordion("Share voices with a friend", open=False, elem_classes="voice-clips-subsection"):
        gr.Markdown("Like how your NPCs sound? Save your voices to a small file and send it to a friend. "
                    "When they load it, their NPCs get the same voices. Voices from mods they don't have are skipped. "
                    "Each voice's pitch and sound settings come with it, for every model.")
        export_btn = gr.Button("Save my voices to a file", size="sm")
        export_file_ui = gr.File(label="Your voices file", interactive=False, visible=False)
        gr.Markdown(f"**Got a file from a friend?** Load it here. Their voices are ready {LATER}.")
        with gr.Row(equal_height=False):
            import_file_ui = gr.File(label="Friend's voices file", type="filepath", file_types=[".json"], height=120)
            when_ui = gr.Radio(choices=[("Keep mine", KEEP_MINE), ("Use theirs", TAKE_THEIRS)], value=KEEP_MINE,
                               label="For voices you both have")
        import_btn = gr.Button("Load their voices", size="sm")
        share_msg = gr.Markdown("")

    # Only for a service on this PC: online services clone each voice the first time an NPC speaks
    with gr.Column(visible=prepares(service_ui.value if service_ui is not None else None)) as prepare_part:
        gr.Markdown("### Prepare all voices\n"
                    "Clone every voice ahead of time, so nobody starts with a stand-in.")
        with gr.Accordion("How it works", open=False, elem_classes="help-fold"):
            gr.Markdown(f"- **Starts** {LATER} or load a save, then works in the background while you play.\n"
                        "- **Takes** about 10 minutes for 100 voice types with Pocket TTS.\n"
                        "- **For** Pocket TTS and voice setups on your PC. Online services (ElevenLabs, Inworld, HeyGen, Qwen, Boson AI, Iodine) clone "
                        "each voice the first time an NPC speaks, so they don't need it.")
        prepare_btn = gr.Button("Prepare all voices", size="sm")
        prepare_msg = gr.Markdown(preparation.progress(), elem_classes="voice-clips-progress")

    def split(choice: str | None) -> tuple[str, str]:
        kind, _, key = (choice or "").partition("/")
        return kind, key

    def still_fits(view: VoiceServiceInfo, used: list[str], path: str) -> bool:
        """Whether the service takes *path* too, added below the clips it uses: counted the way the voice is cloned, gaps included"""
        return lines_within([clip_seconds(p) for p in [*used, path]], view.clip_seconds) == len(used) + 1

    def page_text(style: ServiceStyle, setting: StyleSetting) -> str:
        value = style.page_value(setting)
        return f"{value:g}" if isinstance(value, float) else str(value)

    def real_setting(style: ServiceStyle | None, setting: StyleSetting) -> StyleSetting | None:
        """The setting a control stands for with this service: the Talking speed slider is the service's own"""
        if setting is not SPEED_SLOT:
            return setting
        return style.speed_setting() if style is not None else None

    def style_of(service_value: str | None) -> ServiceStyle | None:
        view, _ = view_for(config, service_value)
        return style_for(config, view) if view is not None else None

    def style_updates(kind: str, key: str, view: VoiceServiceInfo | None) -> list:
        """The shown service's style settings for its picked model, with the voice's own values for that model"""
        style = style_for(config, view) if view is not None else None
        shown_settings = style.settings(per_voice=True) if style is not None and key else []
        updates: list = []
        for slot, control, back in style_uis:
            setting = real_setting(style, slot)
            if setting is None or setting not in shown_settings:
                updates.append(gr.update(visible=False))
                if back is not None:
                    updates.append(gr.update(visible=False))
                continue
            own = style.voice_own(store, kind, key, setting)
            if back is None:
                choices = [(f"Same as the Text-to-Speech page ({page_text(style, setting)})", SAME_AS_PAGE), *((c, c) for c in setting.choices)]
                updates.append(gr.update(visible=True, choices=choices, value=SAME_AS_PAGE if own is None else own))
            else:
                updates.append(gr.update(visible=True, value=style.page_value(setting) if own is None else own))
                updates.append(gr.update(visible=own is not None, value=f"Use the Text-to-Speech page's setting ({page_text(style, setting)})"))
        return updates

    def show(choice: str | None, service_value: str | None = None, message: str | None = ""):
        """Everything under Voice to change for the picked voice, as the service shown uses its clips. *message* None keeps the message"""
        kind, key = split(choice)
        kept = gr.update() if message is None else message
        view, view_note = view_for(config, service_value)
        lines = store.voice_lines(kind, key) if key else []
        if not lines or view is None:
            note = view_note if view is None else ""
            hidden = [gr.update(visible=False), gr.update(), gr.update()]
            return (gr.update(value=note, visible=bool(note)), gr.update(visible=False), "", *(hidden * CLONED_ROWS), "", *(hidden * OTHER_ROWS), "",
                    gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(value=0, visible=False),
                    *style_updates("", "", None), gr.update(interactive=False), gr.update(value=None, visible=False), kept, [], [])
        info = lines[0][1]
        several = bool(view.clip_seconds)
        used = lines_used(store, view, kind, key)
        used_set = {os.path.abspath(p) for p in used}
        others = other_services(config, view, preparation.services_in_use())
        used_by = {}
        for other, _ in others:
            for path in lines_used(store, other, kind, key):
                used_by.setdefault(os.path.abspath(path), other.name)
        rest = [(p, i) for p, i in lines if os.path.abspath(p) not in used_set] + store.other_lines(kind, key)
        total = sum(clip_seconds(p) for p in used)

        # Cloned from
        if several:
            head = f"**Cloned from** · {total:.0f} s of speech ({view.name} takes up to {how_long(view.clip_seconds)})"
            if view.fill_seconds and total < view.fill_seconds:
                head += f" · best with {view.fill_seconds:.0f} s or more"
        else:
            head = "**Cloned from**"
        cloned_for = cloned_for_note(config, view, key)
        if cloned_for:
            head += f"<br>{cloned_for}"
        cloned_updates = []
        cloned_paths = []
        for i in range(CLONED_ROWS):
            if i < len(used):
                path = used[i]
                line = next(l for p, l in lines if p == path)
                cloned_paths.append(path)
                cloned_updates += [gr.update(visible=True), line_html(clip_url(store, kind, key, path), line, path),
                                   gr.update(visible=several, interactive=len(used) > 1, value="Take out")]
            else:
                cloned_updates += [gr.update(visible=False), gr.update(), gr.update()]

        # Other clips
        full = False
        other_updates = []
        other_paths = []
        for i in range(OTHER_ROWS):
            if i < len(rest):
                path, line = rest[i]
                other_paths.append(path)
                tag = f"used by {used_by[os.path.abspath(path)]}" if os.path.abspath(path) in used_by else ""
                if several:
                    fits = still_fits(view, used, path)
                    full = full or not fits
                    button = gr.update(visible=True, interactive=fits, value="Add")
                else:
                    button = gr.update(visible=True, interactive=True, value="Use this one")
                other_updates += [gr.update(visible=True), line_html(clip_url(store, kind, key, path), line, path, tag), button]
            else:
                other_updates += [gr.update(visible=False), gr.update(), gr.update()]
        other_head = "**Other clips**"
        if requests.waiting_for_more(kind, key):
            other_head += f" · new ones show up {LATER}"
        elif not rest:
            other_head += (" · there are no other clips for this voice" if requests.no_more_lines(kind, key)
                           else " · none yet. Press **Find more clips**.")
        elif full:
            other_head += f" · {view.name} is full: take one out to add another"
        if len(rest) > OTHER_ROWS:
            other_head += f" · the first {OTHER_ROWS} of {len(rest)}"

        npc_buttons = (gr.update(visible=kind == NPCS and bool(info.voice_type), value=f"Give {info.name or 'this NPC'}'s voice to every {spaced(info.voice_type)} NPC"),
                       gr.update(visible=kind == NPCS, value=f"Go back to the {spaced(info.voice_type)} voice" if info.voice_type else "Go back to their voice type's voice"))
        return (gr.update(value=view_note, visible=bool(view_note)), gr.update(visible=True), head, *cloned_updates, other_head, *other_updates,
                others_note(others),
                *npc_buttons, gr.update(visible=True, value=style_heading(config, view)), gr.update(value=store.pitch(kind, key), visible=True),
                *style_updates(kind, key, view),
                gr.update(interactive=True), gr.update(value=None, visible=False), kept, cloned_paths, other_paths)

    def seen(choice: str | None, service_value: str | None = None) -> str:
        """When the lists last changed, in short: when it changes (the game sent clips), the lists redraw. Only the times the clip
        folders and files last changed are read, every 3 seconds; every change to a clip or its notes replaces a file in them"""
        kind, key = split(choice)
        def changed(*parts: str) -> int:
            try:
                return os.stat(os.path.join(store.folder, *parts)).st_mtime_ns
            except OSError:
                return 0
        state = (changed(VOICE_TYPES), changed(NPCS), changed(NPCS_SEEN_FILE), changed(CANDIDATES, kind, key) if key else 0,
                 requests.waiting_for_more(kind, key), requests.no_more_lines(kind, key), service_value)
        return repr(state)

    def on_seen(choice: str | None, service_value: str | None = None):
        """The game sent clips: the lists redraw, and the message and the Hear the voice player stay as they are"""
        drawn = list(show(choice, service_value, message=None))
        drawn[shown.index(spoken_ui)] = gr.update()
        return gr.update(choices=clip_choices(store)), gr.update(choices=npc_choices(store)), *drawn

    def service_of(service_value: str | None) -> VoiceServiceInfo | None:
        return view_for(config, service_value)[0]

    def on_take_out(index: int, choice: str | None, service_value: str | None, cloned_paths: list[str]):
        kind, key = split(choice)
        if not key or index >= len(cloned_paths):
            return show(choice, service_value)
        try:
            store.take_out(kind, key, cloned_paths[index])
        except ValueError as e:
            return show(choice, service_value, str(e))
        return show(choice, service_value, "Taken out. NPCs sound like this from their next line. It's under **Other clips** if you want it back.")

    def on_other(index: int, choice: str | None, service_value: str | None, other_paths: list[str]):
        kind, key = split(choice)
        view = service_of(service_value)
        if not key or view is None or index >= len(other_paths):
            return show(choice, service_value)
        path = other_paths[index]
        if not os.path.exists(path): # swapped out by Find more clips since the list was shown
            return show(choice, service_value, "That clip is gone: the game sent new ones. Here they are.")
        if not view.clip_seconds:
            store.choose_candidate(kind, key, path)
            return show(choice, service_value, "Done. NPCs sound like this from their next line. The old clip is under **Other clips** if you want it back.")
        used = lines_used(store, view, kind, key)
        if not still_fits(view, used, path):
            return show(choice, service_value, f"{view.name} is full: take one out first.")
        store.add_line(kind, key, path, len(used))
        return show(choice, service_value, "Added. NPCs sound like this from their next line.")

    def on_speak(choice: str | None, service_value: str | None, sentence: str):
        kind, key = split(choice)
        pick = clip_voice(store, kind, key) if key else None
        if pick is None:
            return gr.update(value=None, visible=False), "Pick a voice first."
        view = service_of(service_value)
        if view is None:
            return gr.update(value=None, visible=False), view_for(config, service_value)[1]
        try:
            path = speak_with_clip(config, chat_manager_getter, view.name, pick, (sentence or "").strip() or TEST_LINE)
        except Exception as e:
            log.warning(f"Hear the voice failed: {e}")
            return gr.update(value=None, visible=False), f"Could not speak: {e}"
        which = "the clips above" if view.clip_seconds and len(lines_used(store, view, kind, key)) > 1 else "the clip above"
        return gr.update(value=path, visible=True), f"**▶ The test sentence, in the voice {view.name} cloned from {which}.**"

    def whose(kind: str, key: str) -> str:
        return f"{store.info(kind, key).name or 'This NPC'}'s voice" if kind == NPCS else f"The {spaced(store.info(kind, key).name or key)} voice"

    def on_pitch(choice: str | None, semitones: float | None):
        kind, key = split(choice)
        if not key:
            return "Pick a voice first."
        store.set_pitch(kind, key, semitones or 0)
        how = "keeps the clips' own pitch" if not semitones else f"is {abs(semitones):g} step{'s' if abs(semitones) != 1 else ''} {'higher' if semitones > 0 else 'deeper'}"
        return f"{whose(kind, key)} {how} from the next line. Press **Hear the voice** to hear it."

    def set_style(slot: StyleSetting, choice: str | None, service_value: str | None, value: Any) -> str:
        """Saves the voice's own value for the picked model (None or Same as the page: takes it away) and says what changes"""
        kind, key = split(choice)
        if not key:
            return "Pick a voice first."
        style = style_of(service_value)
        setting = real_setting(style, slot)
        if style is None or setting is None:
            return "Pick a voice service that clones voices first."
        own = None if value == SAME_AS_PAGE else setting.clean(value)
        style.set_voice_own(store, kind, key, setting, own)
        if setting is HEYGEN_SIMILARITY:
            return (f"{setting.service} clones {whose(kind, key).replace('The ', 'the ', 1)} again before its next line. "
                    "Press **Hear the voice** to hear it.")
        how = f"uses the Text-to-Speech page's {setting.label}" if own is None else f"has {setting.label} {own:g}" if isinstance(own, float) \
            else f"has {setting.label} {own}"
        return f"{whose(kind, key)} {how} from the next line. Press **Hear the voice** to hear it."

    def on_style_slider(slot: StyleSetting, choice: str | None, service_value: str | None, value: float | None):
        message = set_style(slot, choice, service_value, value)
        kind, key = split(choice)
        style = style_of(service_value)
        setting = real_setting(style, slot)
        has_own = bool(key) and setting is not None and style.voice_own(store, kind, key, setting) is not None
        return message, gr.update(visible=has_own)

    def on_style_back(slot: StyleSetting, choice: str | None, service_value: str | None):
        message = set_style(slot, choice, service_value, None)
        style = style_of(service_value)
        setting = real_setting(style, slot)
        return message, gr.update(value=style.page_value(setting) if setting is not None else slot.default), gr.update(visible=False)

    def on_more(choice: str | None, service_value: str | None = None, how_many: float | None = LINES_TO_HEAR):
        kind, key = split(choice)
        if not key:
            return "Pick a voice first."
        count = max(1, min(int(how_many or LINES_TO_HEAR), MOST_MORE))
        view = service_of(service_value)
        if view is not None: # every other clip that no service in use takes is swapped, as the list shows them
            in_use = [view, *(info for info, _ in other_services(config, view, preparation.services_in_use()))]
            store.keep_chosen(kind, key, [p for info in in_use for p in lines_used(store, info, kind, key)])
        requests.ask_for_more(kind, key, count)
        return f"{count} new clip{'s' if count != 1 else ''} take{'' if count != 1 else 's'} the place of the other clips {LATER}."

    def on_remove(choice: str | None, service_value: str | None = None):
        kind, key = split(choice)
        if kind != NPCS:
            return gr.update(), gr.update(), *show(choice, service_value, "Only an NPC's own voice can be removed. To change a voice type's voice, change its clips instead.")
        store.remove(kind, key)
        kept_voices.remove_saved_voices(config.save_folder, key)
        return (gr.update(choices=clip_choices(store), value=None), gr.update(choices=npc_choices(store)),
                *show(None, service_value, "Removed. The NPC sounds like the rest of their voice type again."))

    def on_use_for_voice_type(choice: str | None, service_value: str | None = None):
        kind, key = split(choice)
        if kind != NPCS:
            return gr.update(), gr.update(), *show(choice, service_value, "Pick an NPC with a voice of their own first.")
        npc = store.info(kind, key)
        try:
            voice_type = store.use_npc_clip_for_voice_type(key)
        except ValueError as e:
            return gr.update(), gr.update(), *show(choice, service_value, str(e))
        kept_voices.move_saved_voice(config.save_folder, key, voice_type, store.clip(VOICE_TYPES, voice_type) or "")
        kept_voices.remove_saved_voices(config.save_folder, key)
        new_choice = f"{VOICE_TYPES}/{voice_type}"
        name = spaced(npc.voice_type)
        return (gr.update(choices=clip_choices(store), value=new_choice), gr.update(choices=npc_choices(store)),
                *show(new_choice, service_value, f"Done. Every {name} NPC sounds like this from their next line, {npc.name or 'this NPC'} included. "
                                                 f"The old {name} clips are under **Other clips** if you want them back."))

    def on_give(npc: str | None, service_value: str | None):
        seen_npc = store.npcs_seen().get(npc or "")
        if not seen_npc:
            return "Pick an NPC first."
        view = service_of(service_value)
        requests.ask_for_own_clip(seen_npc.get("plugin", ""), seen_npc.get("local_id", ""), seen_npc.get("name", ""), seen_npc.get("voice_type", ""),
                                  count=view.clip_lines if view else LINES_TO_HEAR)
        return f"{seen_npc.get('name') or 'This NPC'} gets their own voice {LATER}."

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
            return "Pick a voice service that clones voices, such as Pocket TTS, as the TTS Service first, then load a save in game."
        return preparation.prepare_all()

    shown = [view_note_ui, lists_ui, cloned_head_ui]
    for row, line, button in cloned_rows:
        shown += [row, line, button]
    shown.append(other_head_ui)
    for row, line, button in other_rows:
        shown += [row, line, button]
    shown += [others_ui, voice_type_btn, remove_btn, style_head_ui, pitch_ui]
    for _, control, back in style_uis:
        shown += [control] if back is None else [control, back]
    shown += [speak_btn, spoken_ui, change_msg, cloned_paths_ui, other_paths_ui]
    if service_ui is None: # the TTS Service as saved is shown
        service_ui = gr.Textbox(value="", visible=False)
    picked = [clip_ui, service_ui]
    # show_api=False: Gradio 4.28 can't describe an Audio player or a file in its API page, which breaks the page.
    # The voice list uses input, not change: change also fires when a button sets the list, and would wipe that button's message
    clip_ui.input(show, inputs=picked, outputs=shown, show_api=False)
    service_ui.change(lambda choice, service: show(choice, service, message=None), inputs=picked, outputs=shown, show_api=False)
    service_ui.change(lambda service: gr.update(visible=prepares(service)), inputs=[service_ui], outputs=[prepare_part], show_progress="hidden", show_api=False)
    if model_ui is not None: # another Model picked above: the voice's settings for that model
        model_ui.change(lambda choice, service: show(choice, service, message=None), inputs=picked, outputs=shown, show_api=False)
    for index, (_, _, button) in enumerate(cloned_rows):
        button.click(lambda choice, service, paths, i=index: on_take_out(i, choice, service, paths),
                     inputs=[*picked, cloned_paths_ui], outputs=shown, show_api=False)
    for index, (_, _, button) in enumerate(other_rows):
        button.click(lambda choice, service, paths, i=index: on_other(i, choice, service, paths),
                     inputs=[*picked, other_paths_ui], outputs=shown, show_api=False)
    more_btn.click(on_more, inputs=[*picked, more_count_ui], outputs=[change_msg], show_api=False).then(
        lambda choice, service: show(choice, service, message=None), inputs=picked, outputs=shown, show_api=False)
    pitch_ui.release(on_pitch, inputs=[clip_ui, pitch_ui], outputs=[change_msg], show_api=False) # when the slider is let go or the number typed, not while dragging
    for setting, control, back in style_uis:
        if back is None: # input, not change: change also fires when picking a voice sets the dropdown
            control.input(lambda choice, service, value, s=setting: set_style(s, choice, service, value), inputs=[*picked, control],
                          outputs=[change_msg], show_api=False)
        else:
            control.release(lambda choice, service, value, s=setting: on_style_slider(s, choice, service, value), inputs=[*picked, control],
                            outputs=[change_msg, back], show_api=False)
            back.click(lambda choice, service, s=setting: on_style_back(s, choice, service), inputs=picked, outputs=[change_msg, control, back],
                       show_api=False)
    speak_btn.click(on_speak, inputs=[*picked, sentence_ui], outputs=[spoken_ui, change_msg], show_api=False)
    remove_btn.click(on_remove, inputs=picked, outputs=[clip_ui, npc_ui, *shown], show_api=False)
    voice_type_btn.click(on_use_for_voice_type, inputs=picked, outputs=[clip_ui, npc_ui, *shown], show_api=False)
    # hidden: the redraw comes by itself, so the rows don't flash as loading after each click
    seen_ui.change(on_seen, inputs=picked, outputs=[clip_ui, npc_ui, *shown], show_api=False, show_progress="hidden")
    give_btn.click(on_give, inputs=[npc_ui, service_ui], outputs=[give_msg], show_api=False)
    export_btn.click(on_export, outputs=[export_file_ui, share_msg], show_api=False)
    import_btn.click(on_import, inputs=[import_file_ui, when_ui], outputs=[share_msg], show_api=False)
    prepare_btn.click(on_prepare, outputs=[prepare_msg], show_api=False)
    page = gr.context.Context.root_block
    if page is not None:
        # Every 3 seconds while the page is open: Prepare all voices' count, and whether the game sent clips. Gradio marks a refreshing
        # part with a green border, which an empty count squashes into a green line; style.css hides it (voice-clips-progress)
        page.load(preparation.progress, outputs=[prepare_msg], every=3, show_progress="hidden", show_api=False)
        page.load(seen, inputs=picked, outputs=[seen_ui], every=3, show_progress="hidden", show_api=False)


def pocket_tts_language_note(config: ConfigLoader) -> str:
    """Shown under Settings for when Pocket TTS doesn't speak the player's language, else empty"""
    if (config.language or "en").lower()[:2] in pocket_tts.LANGUAGES:
        return ""
    return (f"**Your Language setting ({config.language}) is not one Pocket TTS speaks.** "
            "It speaks English, French, German, Portuguese, Italian, Spanish and Dutch.")


def pocket_tts_signed_in() -> bool:
    return bool(read_key_file(pocket_tts.KEY_FILE))


def render_pocket_tts_panel(config: ConfigLoader) -> None:
    """Turn on voice cloning, folded, at the top of Global settings for Pocket TTS: open until a token is saved"""
    language = pocket_tts.kyutai_language(config.language)
    with gr.Accordion("Turn on voice cloning", open=not pocket_tts_signed_in(), elem_classes="voice-clips-subsection"):
        gr.Markdown(
            "Kyutai's cloning model needs a free Hugging Face sign-in, once:\n"
            "1. Make an account at [huggingface.co](https://huggingface.co/join).\n"
            f"2. Accept Kyutai's terms on [their model page]({pocket_tts.MODEL_PAGE}).\n"
            f"3. Make a token with read access on [your tokens page]({pocket_tts.TOKEN_PAGE}), paste it below and press **Save token**.\n\n"
            "Without it, Pocket TTS still speaks, but every NPC uses one of its built-in voices.")
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
