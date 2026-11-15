import csv
from pathlib import Path

from src.lorebook_manager import LorebookManager


class _ConfigStub:
    def __init__(self, mod_path_base: str, save_folder: str):
        self.mod_path_base = mod_path_base
        self.save_folder = save_folder


def _write_lorebook_csv(path: Path, rows: list[tuple[str, str]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["key", "description"])
        writer.writerows(rows)


def test_lorebook_matches_boundary_and_is_case_insensitive(tmp_path):
    base_folder = tmp_path / "base_lorebook"
    _write_lorebook_csv(
        base_folder / "lorebook.csv",
        [("Riverwood", "Village lore.")],
    )

    config = _ConfigStub(mod_path_base=str(tmp_path / "mod"), save_folder=str(tmp_path / "save"))
    manager = LorebookManager(str(base_folder), config_loader=config, game_name="Skyrim")

    matches = manager.get_matching_entries(
        [
            "We are near Riverwood, then (riverwood).",
            "Riverwoodian is not the same key.",
        ]
    )

    assert matches == [("Riverwood", "Village lore.")]


def test_lorebook_load_order_personal_overrides_mod_overrides_base(tmp_path):
    base_folder = tmp_path / "base_lorebook"
    _write_lorebook_csv(base_folder / "lorebook.csv", [("Riverwood", "Base description")])

    mod_file = (
        tmp_path
        / "mod"
        / "SKSE"
        / "Plugins"
        / "MantellaSoftware"
        / "data"
        / "Skyrim"
        / "lorebook"
        / "mod.csv"
    )
    _write_lorebook_csv(mod_file, [("Riverwood", "Mod description")])

    personal_file = (
        tmp_path
        / "save"
        / "data"
        / "Skyrim"
        / "lorebook"
        / "personal.csv"
    )
    _write_lorebook_csv(personal_file, [("Riverwood", "Personal description")])

    config = _ConfigStub(mod_path_base=str(tmp_path / "mod"), save_folder=str(tmp_path / "save"))
    manager = LorebookManager(str(base_folder), config_loader=config, game_name="Skyrim")

    matches = manager.get_matching_entries(["Riverwood"])
    assert matches == [("Riverwood", "Personal description")]


def test_lorebook_render_is_sorted_by_key(tmp_path):
    base_folder = tmp_path / "base_lorebook"
    _write_lorebook_csv(
        base_folder / "lorebook.csv",
        [
            ("Whiterun", "City lore"),
            ("Riverwood", "Village lore"),
        ],
    )

    config = _ConfigStub(mod_path_base=str(tmp_path / "mod"), save_folder=str(tmp_path / "save"))
    manager = LorebookManager(str(base_folder), config_loader=config, game_name="Skyrim")

    rendered = manager.render_lorebook_entries(["Whiterun and Riverwood are nearby."])
    assert rendered.splitlines() == [
        "- [Riverwood]: [Village lore]",
        "- [Whiterun]: [City lore]",
    ]

