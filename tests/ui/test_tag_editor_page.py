"""Builds the Tag Editor tab and drives its handlers the way the UI would (docs/tag_editor.md)."""
import os
import re

import gradio as gr
import pandas as pd
import pytest

from src.config.config_loader import ConfigLoader
from src.ui import settings_ui_constructor
from src.ui.tag_editor_files import format_event_date, parse_event_date
from src.ui.tag_editor_page import build_tag_editor_page


class _Page:
    """The built tab: its handlers by name, and the components they read and write"""

    def __init__(self, config: ConfigLoader):
        with gr.Blocks() as self.blocks:
            build_tag_editor_page(config)
        self.handlers = {}
        for block_fn in self.blocks.fns:
            self.handlers[block_fn.fn.__name__] = block_fn
        save = self.handlers["on_save"]
        self.fields = save.inputs[1:]  # text, events, 10 outcome boxes, announce, outcome mode, notes, source
        self.dropdown = self.handlers["on_pick"].inputs[0]
        self.message = save.outputs[-1]
        self.snapshot_component = save.inputs[0]

    def call(self, name: str, *args):
        return self.handlers[name].fn(*args)


def _fields(text="", events=None, outcomes=None, announce=True, notes="", mode="default", source=""):
    table = pd.DataFrame(events or [], columns=["Date", "Timestamp", "Event"])
    return (text, table, *(outcomes or [""] * 10), announce, mode, notes, source)


@pytest.fixture
def page(tmp_path, default_config: ConfigLoader, monkeypatch):
    monkeypatch.setattr(settings_ui_constructor, "_game_manager_ref", None)
    default_config.save_folder = str(tmp_path)
    default_config.mod_path_base = str(tmp_path)
    return _Page(default_config)


def _snapshot(page: _Page, update: dict) -> dict:
    return update[page.snapshot_component]


def _picker_message(page):
    return [c for c in page.handlers["on_pick"].outputs if getattr(c, "elem_id", "") == "tag-editor-picker-message"][0]


def _new_tag_message(page):
    return [c for c in page.handlers["on_create"].outputs if getattr(c, "elem_id", "") == "tag-editor-new-tag-message"][0]


def test_create_writes_the_fields_on_screen_and_opens_the_new_tag(page, tmp_path):
    created = page.call("on_create", "tag_editor_page_test", {},
                        *_fields(text="Shared knowledge.", events=[["2026-11-06 14:30", "", "Something\nhappened"]], notes="mine"))

    path = os.path.join(str(tmp_path), "data", "Skyrim", "bio_templates", "tag_editor.csv")
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    row = df[df["tag"] == "tag_editor_page_test"].iloc[0]
    assert row["description"].startswith("Shared knowledge.\n- ")
    assert row["description"].endswith(": Something happened")
    assert row["notes"] == "mine"
    message = created[_new_tag_message(page)]
    assert "Created `tag_editor_page_test` in `tag_editor.csv`" in message
    assert "hasn't connected to the game yet" in message
    # The file was created by this save, so there's no copy from before it to mention
    assert ".bak" not in message

    opened = _snapshot(page, created)
    assert opened["tag"] == "tag_editor_page_test"
    assert opened["text"] == "Shared knowledge."
    assert opened["events"][0][2] == "Something happened"


def test_create_from_an_open_tag_copies_its_fields_and_leaves_it_unchanged(page, tmp_path):
    personal = tmp_path / "data" / "Skyrim" / "bio_templates"
    personal.mkdir(parents=True, exist_ok=True)
    original = "tag,description,notes\ntag_editor_source,Source text.,Source note.\n"
    (personal / "mine.csv").write_text(original, encoding="utf-8")
    snapshot = _snapshot(page, page.call("on_pick", "tag_editor_source", {}, *_fields()))

    created = page.call("on_create", "tag_editor_copy", snapshot, *_fields(text="Edited copy.", notes="Source note."))

    assert (personal / "mine.csv").read_text(encoding="utf-8") == original
    rows = pd.read_csv(personal / "tag_editor.csv", dtype=str, keep_default_na=False).to_dict("records")
    assert rows == [{"tag": "tag_editor_copy", "description": "Edited copy.", "notes": "Source note."}]
    assert "`tag_editor_source` wasn't changed" in created[_new_tag_message(page)]
    assert _snapshot(page, created)["tag"] == "tag_editor_copy"


def test_source_column_is_created_saved_and_counts_as_unsaved(page, tmp_path):
    created = page.call("on_create", "tag_editor_source_test", {}, *_fields(text="Text.", source="Interesting NPCs"))
    path = os.path.join(str(tmp_path), "data", "Skyrim", "bio_templates", "tag_editor.csv")
    rows = pd.read_csv(path, dtype=str, keep_default_na=False).to_dict("records")
    assert rows == [{"tag": "tag_editor_source_test", "description": "Text.", "source": "Interesting NPCs"}]
    snapshot = _snapshot(page, created)
    assert snapshot["cells"]["source"] == "Interesting NPCs"

    blocked = page.call("on_pick", "warrior", snapshot, *_fields(text="Text.", source="Another mod"))
    assert "unsaved changes" in blocked[_picker_message(page)]

    saved = page.call("on_save", snapshot, *_fields(text="Text.", source="Another mod"))
    assert "Saved source to `tag_editor.csv`" in saved[page.message]
    assert pd.read_csv(path, dtype=str, keep_default_na=False).iloc[0]["source"] == "Another mod"
    assert _snapshot(page, saved)["cells"]["source"] == "Another mod"

    # Global search finds the tag by its source, and the result opens it
    search = page.handlers["on_search"]
    results_table, result_rows, summary = search.outputs
    found = page.call("on_search", "another MOD")
    assert found[result_rows] == [["tag_editor_source_test", "source", "Another mod", "Personal, tag_editor.csv"]]
    clicked = page.call("on_result_select", found[result_rows], _Selected(0, 1, "source"))
    assert clicked == "tag_editor_source_test"


def test_source_from_a_mod_file_shows_where_it_comes_from(page_with_mod_tag, tmp_path):
    page = page_with_mod_tag
    mod_file = tmp_path / "SKSE" / "Plugins" / "MantellaSoftware" / "data" / "Skyrim" / "bio_templates" / "mod.csv"
    mod_file.write_text("tag,description,source\ntag_editor_mod_test,Mod text.,Some Mod\n", encoding="utf-8")
    snapshot = _snapshot(page, page.call("on_pick", "tag_editor_mod_test", {}, *_fields()))
    assert snapshot["cells"]["source"] == "Some Mod"

    saved = page.call("on_save", snapshot, *_fields(text="Mod text.", source="My label"))
    source_from = [c for c in page.handlers["on_save"].outputs if getattr(c, "label", None) is None
                   and "tag-editor-source" in (c.elem_classes or [])]
    assert any(saved.get(c, "") == "From: Personal, `tag_editor.csv`. Clearing it brings back the Mod value" for c in source_from)
    assert "Some Mod" in mod_file.read_text(encoding="utf-8")


def test_text_typed_into_the_empty_form_counts_as_unsaved(page):
    blocked = page.call("on_pick", "warrior", {}, *_fields(text="Typed but no tag open."))

    assert "isn't saved anywhere yet" in blocked[_picker_message(page)]
    assert page.snapshot_component not in blocked
    assert "Fields emptied" in page.call("on_discard", {}, *_fields(text="Typed but no tag open."))[page.message]
    assert "Nothing to discard" in page.call("on_discard", {}, *_fields())[page.message]


def test_nothing_to_save_when_nothing_changed(page):
    page.call("on_create", "tag_editor_page_test", {}, *_fields(text="Text."))
    snapshot = _snapshot(page, page.call("on_pick", "tag_editor_page_test", {}, *_fields()))

    result = page.call("on_save", snapshot, *_fields(text="Text."))

    assert result[page.message].endswith(" · Nothing to save.")


def test_each_result_starts_with_the_time_it_finished(page):
    """Saving twice gives the same words, so the time is what shows the second save went through"""
    page.call("on_create", "tag_editor_page_test", {}, *_fields(text="Text."))
    snapshot = _snapshot(page, page.call("on_pick", "tag_editor_page_test", {}, *_fields()))

    saved = page.call("on_save", snapshot, *_fields(text="Changed."))[page.message]

    assert re.match(r"\*\*\d\d:\d\d:\d\d\*\* · Saved ", saved)


def test_a_result_with_nothing_to_say_clears_the_working_line(page):
    page.call("on_create", "tag_editor_page_test", {}, *_fields(text="Text."))

    opened = page.call("on_pick", "tag_editor_page_test", {}, *_fields())

    assert opened[_picker_message(page)] == ""


def test_a_save_to_an_existing_file_names_its_backup(page):
    page.call("on_create", "tag_editor_page_test", {}, *_fields(text="Text."))
    snapshot = _snapshot(page, page.call("on_pick", "tag_editor_page_test", {}, *_fields()))

    result = page.call("on_save", snapshot, *_fields(text="Changed."))

    assert "A copy of the file from before this change is kept as `tag_editor.csv.bak`, in the same folder." in result[page.message]


def test_unsaved_changes_keep_the_current_tag(page):
    page.call("on_create", "tag_editor_page_test", {}, *_fields(text="Text."))
    snapshot = _snapshot(page, page.call("on_pick", "tag_editor_page_test", {}, *_fields()))

    result = page.call("on_pick", "warrior", snapshot, *_fields(text="Edited but not saved."))

    # Right under the tag list, where the switch was tried, not under Save at the end of the form
    assert "unsaved changes to `tag_editor_page_test`" in result[_picker_message(page)]
    assert page.snapshot_component not in result


def test_create_rejects_commas_and_existing_names(page):
    # Shown next to the New tag box at the end of the page, where the name was typed
    new_tag_message = _new_tag_message(page)
    assert "commas" in page.call("on_create", "a,b", {}, *_fields())[new_tag_message]
    page.call("on_create", "tag_editor_page_test", {}, *_fields(text="Text."))

    assert "already exists" in page.call("on_create", "tag_editor_page_test", {}, *_fields())[new_tag_message]


def test_delete_needs_a_second_click_and_removes_only_personal_rows(page, tmp_path):
    snapshot = _snapshot(page, page.call("on_create", "tag_editor_page_test", {}, *_fields(text="Text.")))

    asked = page.call("on_remove", snapshot)
    assert "Confirm delete" in asked[page.message]
    path = os.path.join(str(tmp_path), "data", "Skyrim", "bio_templates", "tag_editor.csv")
    assert "tag_editor_page_test" in pd.read_csv(path, dtype=str)["tag"].tolist()

    deleted = page.call("on_confirm_remove", _snapshot(page, asked))

    assert "Deleted `tag_editor_page_test`" in deleted[page.message]
    assert "tag_editor_page_test" not in pd.read_csv(path, dtype=str, keep_default_na=False)["tag"].tolist()
    assert os.path.exists(path + ".bak")


@pytest.fixture
def page_with_mod_tag(tmp_path, default_config: ConfigLoader, monkeypatch):
    mod_folder = tmp_path / "SKSE" / "Plugins" / "MantellaSoftware" / "data" / "Skyrim" / "bio_templates"
    mod_folder.mkdir(parents=True)
    (mod_folder / "mod.csv").write_text("tag,description,notes\ntag_editor_mod_test,Mod text.,Mod note.\n", encoding="utf-8")
    monkeypatch.setattr(settings_ui_constructor, "_game_manager_ref", None)
    default_config.save_folder = str(tmp_path)
    default_config.mod_path_base = str(tmp_path)
    return _Page(default_config)


def test_override_then_back_to_original(page_with_mod_tag, tmp_path):
    page = page_with_mod_tag
    snapshot = _snapshot(page, page.call("on_pick", "tag_editor_mod_test", {}, *_fields()))
    assert snapshot["text"] == "Mod text."

    saved = page.call("on_save", snapshot, *_fields(text="My text.", notes="Mod note."))
    snapshot = _snapshot(page, saved)
    assert snapshot["text"] == "My text."
    mod_file = tmp_path / "SKSE" / "Plugins" / "MantellaSoftware" / "data" / "Skyrim" / "bio_templates" / "mod.csv"
    assert "Mod text." in mod_file.read_text(encoding="utf-8")

    asked = page.call("on_remove", snapshot)
    assert "Confirm reset" in asked[page.message]
    remove_btn = [c for c in page.handlers["on_remove"].outputs if getattr(c, "elem_id", "") == "tag-editor-remove"][0]
    assert saved[remove_btn]["value"] == "Reset to mod version"
    restored = page.call("on_confirm_remove", _snapshot(page, asked))

    assert _snapshot(page, restored)["text"] == "Mod text."
    assert "back to the original" in restored[page.message]


def test_clearing_brings_back_the_mod_value_or_says_it_cant_be_blanked(page_with_mod_tag):
    page = page_with_mod_tag
    snapshot = _snapshot(page, page.call("on_pick", "tag_editor_mod_test", {}, *_fields()))
    snapshot = _snapshot(page, page.call("on_save", snapshot, *_fields(text="My text.", notes="Mod note.")))

    cleared = page.call("on_save", snapshot, *_fields(text="", notes=""))

    assert "the Mod value comes back" in cleared[page.message]
    assert "Notes comes from a Mod file and can't be blanked" in cleared[page.message]
    assert _snapshot(page, cleared)["text"] == "Mod text."


class _FakeGame:
    def __init__(self, folder: str, versions: tuple[str, str]):
        self.conversation_folder_path = folder
        self.character_df = pd.DataFrame([{"name": "Lydia", "base_id": "0A2C94", "tags": "tag_editor_page_test", "tags_overwrite": ""}])
        self.versions = versions
        self.refreshed_with = None

    def reload_bio_templates(self):
        return self.versions

    def refresh_characters_with_tag(self, characters, tag, tag_outcomes=None):
        self.refreshed_with = (characters, tag)
        return ["Lydia"]


class _FakeGameManager:
    def __init__(self, game, talk):
        self.game = game
        self.active_conversation = talk


class _FakeTalk:
    class context:
        world_id = "Player1"

        class npcs_in_conversation:
            @staticmethod
            def get_all_characters():
                return ["lydia character"]


@pytest.mark.parametrize("versions, paused", [(("v1", "v2"), True), (("v1", "v1"), False)])
def test_save_during_a_conversation_refreshes_npcs_and_warns_when_checks_pause(page, tmp_path, monkeypatch, versions, paused):
    game = _FakeGame(str(tmp_path), versions)
    monkeypatch.setattr(settings_ui_constructor, "_game_manager_ref", _FakeGameManager(game, _FakeTalk()))
    snapshot = _snapshot(page, page.call("on_create", "tag_editor_page_test", {}, *_fields(text="Text.")))

    saved = page.call("on_save", snapshot, *_fields(text="Changed."))

    assert game.refreshed_with == (["lydia character"], "tag_editor_page_test")
    assert "Lydia, in the current conversation, use the new version from their next reply." in saved[page.message]
    assert ("quest progress won't be checked until the next conversation starts" in saved[page.message]) is paused
    npc_list = [component for component in page.handlers["on_save"].outputs if getattr(component, "elem_id", "") == "tag-editor-npc-list"][0]
    assert saved[npc_list] == "Lydia (0A2C94)"
    npc_section = [component for component in page.handlers["on_save"].outputs if getattr(component, "elem_id", "") == "tag-editor-npc-section"][0]
    assert saved[npc_section]["label"] == "Used by 1 NPC"


def test_delete_sees_files_edited_by_hand_after_the_tab_loaded(page, tmp_path):
    snapshot = _snapshot(page, page.call("on_create", "tag_editor_page_test", {}, *_fields(text="Text.")))
    by_hand = tmp_path / "data" / "Skyrim" / "bio_templates" / "by_hand.csv"
    by_hand.write_text("tag,description\ntag_editor_page_test,Also here.\n", encoding="utf-8")

    asked = page.call("on_remove", snapshot)
    assert "`by_hand.csv`" in asked[page.message]
    page.call("on_confirm_remove", _snapshot(page, asked))

    assert "tag_editor_page_test" not in pd.read_csv(by_hand, dtype=str, keep_default_na=False)["tag"].tolist()


def test_picking_a_tag_shows_files_edited_by_hand_and_clears_a_half_typed_event(page, tmp_path):
    personal = tmp_path / "data" / "Skyrim" / "bio_templates"
    personal.mkdir(parents=True, exist_ok=True)
    (personal / "by_hand.csv").write_text("tag,description\ntag_editor_by_hand,Written by hand.\n", encoding="utf-8")

    picked = page.call("on_pick", "tag_editor_by_hand", {}, *_fields())

    assert _snapshot(page, picked)["text"] == "Written by hand."
    new_event_text = page.handlers["on_add_event"].inputs[1]
    assert picked[new_event_text] == ""


def test_an_event_line_with_a_timestamp_windows_cant_show_still_opens(page, tmp_path):
    personal = tmp_path / "data" / "Skyrim" / "bio_templates"
    personal.mkdir(parents=True, exist_ok=True)
    (personal / "odd.csv").write_text("tag,description\ntag_editor_odd,\"Text.\n- 99999999999: Far future\"\n", encoding="utf-8")

    picked = page.call("on_pick", "tag_editor_odd", {}, *_fields())

    assert _snapshot(page, picked)["events"] == [["99999999999", "99999999999", "Far future"]]


def test_clearing_several_fields_in_another_personal_file_keeps_its_backup_intact(page, tmp_path):
    personal = tmp_path / "data" / "Skyrim" / "bio_templates"
    personal.mkdir(parents=True, exist_ok=True)
    original = "tag,description,potential_paste_1,notes\ntag_editor_two_files,,Outcome text.,A note.\n"
    (personal / "a.csv").write_text(original, encoding="utf-8")
    (personal / "b.csv").write_text("tag,description\ntag_editor_two_files,Text from b.\n", encoding="utf-8")
    snapshot = _snapshot(page, page.call("on_pick", "tag_editor_two_files", {}, *_fields()))
    outcomes = [""] * 10

    page.call("on_save", snapshot, *_fields(text="Text from b.", outcomes=outcomes, notes=""))

    assert (personal / "a.csv.bak").read_text(encoding="utf-8") == original
    assert pd.read_csv(personal / "a.csv", dtype=str, keep_default_na=False).to_dict("records") == [
        {"tag": "tag_editor_two_files", "description": "", "potential_paste_1": "", "notes": ""}
    ]


def test_saving_keeps_the_exact_timestamp_of_an_untouched_event(page, tmp_path):
    personal = tmp_path / "data" / "Skyrim" / "bio_templates"
    personal.mkdir(parents=True, exist_ok=True)
    # 01:30 local time in the hour repeated when clocks go back; its shown date maps back to a different moment
    timestamp = 1793518200
    (personal / "dst.csv").write_text(f"tag,description\ntag_editor_dst,\"Old.\n- {timestamp}: Event\"\n", encoding="utf-8")
    snapshot = _snapshot(page, page.call("on_pick", "tag_editor_dst", {}, *_fields()))

    page.call("on_save", snapshot, *_fields(text="New.", events=snapshot["events"]))

    description = pd.read_csv(personal / "dst.csv", dtype=str, keep_default_na=False)["description"][0]
    assert description == f"New.\n- {timestamp}: Event"


class _Selected:
    def __init__(self, row: int, col: int, value: str):
        self.index = [row, col]
        self.value = value


def test_search_then_click_a_result_opens_the_tag(page, tmp_path):
    personal = tmp_path / "data" / "Skyrim" / "bio_templates"
    personal.mkdir(parents=True, exist_ok=True)
    (personal / "mine.csv").write_text("tag,description,notes\ntag_editor_search_a,Knows about the Zebra Cult.,\ntag_editor_search_b,,zebra notes\n", encoding="utf-8")
    search = page.handlers["on_search"]
    results_table, result_rows, summary = search.outputs

    found = page.call("on_search", "ZEBRA")

    rows = [
        ["tag_editor_search_a", "description", "Knows about the Zebra Cult.", "Personal, mine.csv"],
        ["tag_editor_search_b", "notes", "zebra notes", "Personal, mine.csv"],
    ]
    assert found[result_rows] == rows
    assert found[results_table].values.tolist() == rows
    assert "2 matches in 2 tags" in found[summary]

    clicked = page.call("on_result_select", found[result_rows], _Selected(1, 2, "zebra notes"))
    assert clicked == "tag_editor_search_b"
    opened = page.call("on_open_result", clicked, {}, *_fields())
    assert _snapshot(page, opened)["tag"] == "tag_editor_search_b"
    assert "Click its tag name" in page.call("on_open_result", None, {}, *_fields())[page.message]

    # Clicking a result for the tag that's already open: Gradio 4 fails on an empty dict, so something must be updated
    again = page.call("on_open_result", clicked, _snapshot(page, opened), *_fields(notes="zebra notes"))
    assert again and page.snapshot_component not in again


def test_clicking_a_result_after_sorting_the_table_opens_the_clicked_tag(page):
    rows = [
        ["b_tag", "description", "text of b", "Base, x.csv"],
        ["a_tag", "notes", "text of a", "Base, x.csv"],
        ["c_tag", "description", "text of c", "Base, x.csv"],
    ]
    # Sorted by tag on screen: a_tag is now the first row
    assert page.call("on_result_select", rows, _Selected(0, 0, "a_tag")) == "a_tag"
    assert page.call("on_result_select", rows, _Selected(0, 2, "text of a")) == "a_tag"
    assert page.call("on_result_select", rows, _Selected(0, 1, "notes")) == "a_tag"
    # "description" is on two rows, and position 1 shows "notes" in the found order: can't tell which was clicked
    assert page.call("on_result_select", rows, _Selected(1, 1, "description")) is None
    # Not sorted: the position agrees with the clicked text
    assert page.call("on_result_select", rows, _Selected(2, 1, "description")) == "c_tag"


def test_save_refuses_to_overwrite_a_field_changed_in_the_file_since_opening(page, tmp_path):
    personal = tmp_path / "data" / "Skyrim" / "bio_templates"
    personal.mkdir(parents=True, exist_ok=True)
    path = personal / "mine.csv"
    path.write_text("tag,description\ntag_editor_conflict,\"Text.\n- 1739270400: Old event\"\n", encoding="utf-8")
    snapshot = _snapshot(page, page.call("on_pick", "tag_editor_conflict", {}, *_fields()))
    by_hand = "tag,description\ntag_editor_conflict,\"Text, edited by hand.\n- 1739270400: Old event\"\n"
    path.write_text(by_hand, encoding="utf-8")

    result = page.call("on_save", snapshot, *_fields(text="Text.", events=snapshot["events"] + [["2026-11-06 14:30", "", "New event"]]))

    assert "Not saved" in result[page.message] and "tag text and events" in result[page.message]
    assert path.read_text(encoding="utf-8") == by_hand
    assert not os.path.exists(str(path) + ".bak")


def test_save_still_works_when_another_field_changed_in_the_file(page, tmp_path):
    personal = tmp_path / "data" / "Skyrim" / "bio_templates"
    personal.mkdir(parents=True, exist_ok=True)
    path = personal / "mine.csv"
    path.write_text("tag,description,notes\ntag_editor_conflict,Text.,\n", encoding="utf-8")
    snapshot = _snapshot(page, page.call("on_pick", "tag_editor_conflict", {}, *_fields()))
    path.write_text("tag,description,notes\ntag_editor_conflict,Text.,note by hand\n", encoding="utf-8")

    result = page.call("on_save", snapshot, *_fields(text="Text. More."))

    assert "Saved" in result[page.message]
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert df.to_dict("records") == [{"tag": "tag_editor_conflict", "description": "Text. More.", "notes": "note by hand"}]


def test_search_with_nothing_found(page):
    search = page.handlers["on_search"]
    found = page.call("on_search", "no tag says this 12345")
    assert "Nothing found" in found[search.outputs[2]]


def test_an_empty_when_box_dates_the_event_at_the_moment_add_is_clicked(page, monkeypatch):
    from src.ui import tag_editor_page
    clicked_at = 1793518200
    monkeypatch.setattr(tag_editor_page.time, "time", lambda: clicked_at + 0.7)
    add = page.handlers["on_add_event"]
    events_table, when_box, timestamp_box = add.inputs[0], add.inputs[2], add.inputs[3]

    result = page.call("on_add_event", pd.DataFrame(columns=["Date", "Timestamp", "Event"]), "Something", "", "")

    assert result[events_table]["value"].values.tolist() == [[format_event_date(clicked_at), str(clicked_at), "Something"]]
    assert result[when_box] == "" and result[timestamp_box] == ""


def test_an_event_can_be_added_by_timestamp_and_typing_one_box_fills_the_other(page):
    add = page.handlers["on_add_event"]

    result = page.call("on_add_event", pd.DataFrame(columns=["Date", "Timestamp", "Event"]), "Something", "", "1793518200")

    assert result[add.inputs[0]]["value"].values.tolist() == [[format_event_date(1793518200), "1793518200", "Something"]]
    assert page.call("on_new_event_timestamp", "1793518200") == format_event_date(1793518200)
    assert page.call("on_new_event_date", "2026-11-06 14:30") == str(parse_event_date("2026-11-06 14:30"))
    assert page.call("on_new_event_date", "2026-11-0") == ""


def test_editing_an_event_timestamp_in_the_table_updates_its_date(page):
    shown = [[format_event_date(1739270400), "1739270400", "Event"]]
    edited = pd.DataFrame([[format_event_date(1739270400), "1793518200", "Event"]], columns=["Date", "Timestamp", "Event"])

    result = page.call("on_events_edited", edited, shown)

    table = page.handlers["on_add_event"].inputs[0]
    assert result[table]["value"].values.tolist() == [[format_event_date(1793518200), "1793518200", "Event"]]


def _events(*rows):
    return pd.DataFrame([list(row) for row in rows], columns=["Date", "Timestamp", "Event"])


def test_click_an_event_then_remove_event_takes_its_row_out(page):
    first = [format_event_date(1739270400), "1739270400", "First"]
    second = [format_event_date(1793518200), "1793518200", "Second"]
    table = page.handlers["on_add_event"].inputs[0]

    # Clicked the text of the second row while the table was sorted newest first (shown at position 0)
    clicked = page.call("on_event_select", _events(first, second), _Selected(0, 2, "Second"))
    selected_state, button = page.handlers["on_event_select"].outputs
    selected = clicked[selected_state]
    assert selected == second
    # The button says which event it will remove
    assert clicked[button]["value"] == f'Remove "Second" ({second[0]})' and clicked[button]["interactive"]
    result = page.call("on_remove_event", _events(first, second), selected)

    assert result[table]["value"].values.tolist() == [first]
    assert "Event removed" in result[page.message]
    assert "Click an event" in page.call("on_remove_event", _events(first), None)[page.message]
    assert result[button]["interactive"] is False


def test_a_stale_empty_row_report_after_add_event_keeps_the_new_event(page):
    # Gradio fills an empty table with one blank row and can report it after Add event has set the new value.
    # Taking "cleared" rows out on that report wiped the event just added
    table = page.handlers["on_add_event"].inputs[0]
    added = page.call("on_add_event", _events(["", "", ""]), "Something", "", "1793518200")
    assert added[table]["value"].values.tolist() == [[format_event_date(1793518200), "1793518200", "Something"]]

    result = page.call("on_events_edited", _events(["", "", ""]), [[format_event_date(1793518200), "1793518200", "Something"]])

    assert table not in result


def test_bad_event_date_is_reported_not_raised(page):
    result = page.call("on_add_event", pd.DataFrame(columns=["Date", "Timestamp", "Event"]), "Something", "someday", "")

    assert "isn't a date" in result[page.message]


def test_an_edit_report_that_changes_nothing_does_not_send_the_table_back(page):
    # Setting the table can make the browser report an edit; answering with the table again made the page flicker
    row = [format_event_date(1739270400), "1739270400", "Event"]
    table = page.handlers["on_add_event"].inputs[0]

    result = page.call("on_events_edited", _events(row), [row])

    assert table not in result and result


def test_save_clicked_before_the_table_filled_in_an_edited_date_saves_the_new_date(page, tmp_path):
    personal = tmp_path / "data" / "Skyrim" / "bio_templates"
    personal.mkdir(parents=True, exist_ok=True)
    (personal / "race.csv").write_text("tag,description\ntag_editor_race,\"Text.\n- 1739270400: Event\"\n", encoding="utf-8")
    snapshot = _snapshot(page, page.call("on_pick", "tag_editor_race", {}, *_fields()))

    # The date was changed, but the timestamp still has the old value: the fill-in hadn't come back yet
    result = page.call("on_save", snapshot, *_fields(text="Text.", events=[["2026-11-06 14:30", "1739270400", "Event"]]))

    assert "Saved" in result[page.message]
    description = pd.read_csv(personal / "race.csv", dtype=str, keep_default_na=False)["description"][0]
    assert description == f"Text.\n- {parse_event_date('2026-11-06 14:30')}: Event"


def test_add_event_keeps_every_event_and_shows_them_oldest_first(page):
    # The table showed a fixed number of rows (1), so a second event vanished and Save dropped it
    table = page.handlers["on_add_event"].inputs[0]
    later = [format_event_date(1793518200), "1793518200", "Later"]

    result = page.call("on_add_event", _events(later), "Earlier", "", "1739270400")

    update = result[table]
    assert update["value"].values.tolist() == [[format_event_date(1739270400), "1739270400", "Earlier"], later]
    assert update["row_count"] == (2, "fixed")


def test_outcome_mode_is_saved_shown_again_and_written_out_when_set_back(page, tmp_path):
    outcomes = ["Won.", "getstagedone CWObj 100=1"] + [""] * 8
    created = page.call("on_create", "tag_editor_mode_test", {}, *_fields(outcomes=outcomes, mode="dynamic"))
    snapshot = _snapshot(page, created)
    assert snapshot["mode"] == "dynamic"
    path = os.path.join(str(tmp_path), "data", "Skyrim", "bio_templates", "tag_editor.csv")

    def cell():
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        return df[df["tag"] == "tag_editor_mode_test"].iloc[0]["outcome_mode"]

    assert cell() == "dynamic"
    assert "Nothing to save" in page.call("on_save", snapshot, *_fields(outcomes=outcomes, mode="dynamic"))[page.message]

    saved = page.call("on_save", snapshot, *_fields(outcomes=outcomes, mode="off"))
    assert "Saved outcome_mode" in saved[page.message]
    assert cell() == "off"

    # Back to following the setting is written out, so it would also undo a base or mod file's choice
    saved = page.call("on_save", _snapshot(page, saved), *_fields(outcomes=outcomes, mode="default"))
    assert cell() == "default"
    assert _snapshot(page, saved)["mode"] == "default"


def test_a_new_tag_that_follows_the_setting_gets_no_outcome_mode_column(page, tmp_path):
    page.call("on_create", "tag_editor_plain_test", {}, *_fields(text="Plain."))

    path = os.path.join(str(tmp_path), "data", "Skyrim", "bio_templates", "tag_editor.csv")
    assert "outcome_mode" not in pd.read_csv(path, dtype=str, keep_default_na=False).columns


def test_an_unknown_outcome_mode_is_shown_and_replaced_by_save(page, tmp_path):
    personal = tmp_path / "data" / "Skyrim" / "bio_templates"
    personal.mkdir(parents=True, exist_ok=True)
    (personal / "mine.csv").write_text("tag,description,outcome_mode\ntag_editor_typo_test,Text.,dynamc\n", encoding="utf-8")

    opened = page.call("on_pick", "tag_editor_typo_test", {}, *_fields())
    snapshot = _snapshot(page, opened)
    assert snapshot["mode"] == "default"
    assert any(isinstance(value, str) and "`dynamc`" in value for value in opened.values())
    # Opening it isn't an unsaved change: another tag can still be picked
    assert "unsaved changes" not in str(page.call("on_refresh", snapshot, *_fields(text="Text.")).get(_picker_message(page), ""))

    saved = page.call("on_save", snapshot, *_fields(text="Text."))

    assert "Saved outcome_mode" in saved[page.message]
    df = pd.read_csv(personal / "mine.csv", dtype=str, keep_default_na=False)
    assert df.iloc[0]["outcome_mode"] == "default"
    assert _snapshot(page, saved)["unknown_mode"] == ""
