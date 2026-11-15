import os
import time

from src.ui.bio_editor_memory_files import (
    conversations_base_dir,
    ensure_dash_prefix,
    ensure_thought_dash_prefixes,
    find_npc_folder,
    latest_numbered_file,
    load_latest_text,
    notes_base_dir,
    pick_world_id,
    reflections_base_dir,
    save_latest_text,
    thoughts_base_dir,
)


def test_base_dirs_stay_separate(tmp_path):
    save_folder = str(tmp_path)
    assert conversations_base_dir(save_folder, "Skyrim").endswith(os.path.join("data", "Skyrim", "conversations"))
    assert thoughts_base_dir(save_folder, "Skyrim").endswith(os.path.join("data", "Skyrim", "thoughts"))
    assert reflections_base_dir(save_folder, "Skyrim").endswith(os.path.join("data", "Skyrim", "reflections"))
    assert conversations_base_dir(save_folder, "Skyrim") != thoughts_base_dir(save_folder, "Skyrim")
    assert thoughts_base_dir(save_folder, "Skyrim") != reflections_base_dir(save_folder, "Skyrim")
    assert notes_base_dir(save_folder, "Skyrim").endswith(os.path.join("data", "Skyrim", "notes"))


def test_pick_world_id_prefers_default(tmp_path):
    (tmp_path / "other").mkdir()
    (tmp_path / "default").mkdir()
    assert pick_world_id(str(tmp_path)) == "default"


def test_pick_world_id_uses_newest_when_no_default(tmp_path):
    older = tmp_path / "old_world"
    newer = tmp_path / "new_world"
    older.mkdir()
    time.sleep(0.02)
    newer.mkdir()
    assert pick_world_id(str(tmp_path)) == "new_world"


def test_pick_world_id_missing_dir():
    assert pick_world_id(os.path.join("definitely", "missing")) == "default"


def test_find_npc_folder_prefers_name_ref(tmp_path):
    world = tmp_path / "default"
    world.mkdir()
    (world / "Lydia").mkdir()
    name_ref = world / "Lydia - 000A2C8E"
    time.sleep(0.02)
    name_ref.mkdir()
    found = find_npc_folder(str(tmp_path), "default", "Lydia")
    assert found is not None
    assert found.endswith("Lydia - 000A2C8E")


def test_latest_numbered_file_picks_highest_thoughts(tmp_path):
    (tmp_path / "Lydia_thoughts_1.txt").write_text("old", encoding="utf-8")
    (tmp_path / "Lydia_thoughts_3.txt").write_text("newest", encoding="utf-8")
    (tmp_path / "Lydia_summary_9.txt").write_text("not thoughts", encoding="utf-8")
    path, n = latest_numbered_file(str(tmp_path), "Lydia", "thoughts")
    assert n == 3
    assert path.endswith("Lydia_thoughts_3.txt")


def test_ensure_dash_prefix():
    assert ensure_dash_prefix("I do not trust them.") == "- I do not trust them."
    assert ensure_dash_prefix("- already dashed") == "- already dashed"
    assert ensure_dash_prefix("  ") == ""


def test_ensure_thought_dash_prefixes_each_block():
    raw = "ts=100\nI do not trust them yet.\n\nts=200\n- They kept their word."
    prefixed = ensure_thought_dash_prefixes(raw)
    assert prefixed == "ts=100\n- I do not trust them yet.\n\nts=200\n- They kept their word."


def test_load_and_save_thoughts_are_raw(tmp_path):
    raw = "ts=100\nI do not trust them yet.\n\nts=200\nThey kept their word."
    folder = tmp_path / "default" / "Lydia - 000A2C8E"
    folder.mkdir(parents=True)
    (folder / "Lydia_thoughts_1.txt").write_text(raw + "\n", encoding="utf-8")

    loaded = load_latest_text(str(tmp_path), "default", "Lydia", "thoughts")
    assert "ts=100" in loaded
    assert "PRIVATE THOUGHT" not in loaded
    assert "I do not trust them yet." in loaded

    saved = save_latest_text(str(tmp_path), "default", "Lydia", "thoughts", loaded + "\n\nEdited by hand.")
    assert saved.endswith("Lydia_thoughts_1.txt")
    on_disk = (folder / "Lydia_thoughts_1.txt").read_text(encoding="utf-8")
    assert "- Edited by hand." in on_disk
    assert "- I do not trust them yet." in on_disk
    assert "- They kept their word." in on_disk
    assert "PRIVATE THOUGHT" not in on_disk


def test_save_thoughts_creates_name_only_folder(tmp_path):
    path = save_latest_text(str(tmp_path), "default", "Lydia", "thoughts", "ts=1\nA first thought.")
    assert path
    assert path.endswith("Lydia/Lydia_thoughts_1.txt")
    loaded = load_latest_text(str(tmp_path), "default", "Lydia", "thoughts")
    assert loaded == "ts=1\n- A first thought."


def test_save_does_not_write_into_conversations(tmp_path):
    thoughts_dir = thoughts_base_dir(str(tmp_path), "Skyrim")
    conversations_dir = conversations_base_dir(str(tmp_path), "Skyrim")
    save_latest_text(thoughts_dir, "default", "Lydia", "thoughts", "secret")
    save_latest_text(conversations_dir, "default", "Lydia", "summary", "- They talked.")
    assert (tmp_path / "data" / "Skyrim" / "thoughts" / "default" / "Lydia" / "Lydia_thoughts_1.txt").exists()
    assert (tmp_path / "data" / "Skyrim" / "conversations" / "default" / "Lydia" / "Lydia_summary_1.txt").exists()
    assert not (tmp_path / "data" / "Skyrim" / "conversations" / "default" / "Lydia" / "Lydia_thoughts_1.txt").exists()


def test_load_and_save_reflections_keep_blank_lines(tmp_path):
    raw = "ts=100\nFirst paragraph.\n\nSecond paragraph.\n\nts=200\nNewest."
    folder = tmp_path / "default" / "Lydia - 000A2C8E"
    folder.mkdir(parents=True)
    (folder / "Lydia_reflections_1.txt").write_text(raw + "\n", encoding="utf-8")

    loaded = load_latest_text(str(tmp_path), "default", "Lydia", "reflections")
    assert "ts=100" in loaded
    assert "First paragraph." in loaded
    assert "Second paragraph." in loaded
    assert "Personal Reflection" not in loaded

    saved = save_latest_text(str(tmp_path), "default", "Lydia", "reflections", loaded)
    assert saved.endswith("Lydia_reflections_1.txt")
    on_disk = (folder / "Lydia_reflections_1.txt").read_text(encoding="utf-8")
    assert "First paragraph." in on_disk
    assert "Second paragraph." in on_disk
    assert "- First paragraph." not in on_disk


def test_save_and_load_notes_verbatim_in_own_folder(tmp_path):
    notes_dir = notes_base_dir(str(tmp_path), "Skyrim")
    raw = "Met her in Whiterun.\n\nTODO: ask about Bleak Falls Barrow"
    path = save_latest_text(notes_dir, "default", "Lydia", "notes", raw)
    assert path.endswith("notes/default/Lydia/Lydia_notes_1.txt")
    assert load_latest_text(notes_dir, "default", "Lydia", "notes") == raw
    assert not (tmp_path / "data" / "Skyrim" / "thoughts").exists()
    assert not (tmp_path / "data" / "Skyrim" / "conversations").exists()
