from types import SimpleNamespace

from src.bio_section_filter import (
    filter_markdown_top_level_sections as _filter_markdown_top_level_sections,
    normalize_bio_section_name as _normalize_bio_section_name,
    should_filter_conversation_bios,
    should_filter_memory_bios,
)
from src.conversation.context import context as Context


def test_normalize_bio_section_name():
    assert _normalize_bio_section_name("  Personal   History  ") == "personal history"


def test_filter_markdown_top_level_sections_removes_only_matching_top_level_sections():
    bio = """## Race
- Khajiit

## Appearance
- Blue eyes

## Personal History
- Born in Senchal
"""
    filtered, matched = _filter_markdown_top_level_sections(
        bio,
        {"race", "personal history"},
    )

    assert "## Race" not in filtered
    assert "Khajiit" not in filtered
    assert "## Personal History" not in filtered
    assert "Born in Senchal" not in filtered
    assert "## Appearance" in filtered
    assert "Blue eyes" in filtered
    assert matched == {"race", "personal history"}


def test_filter_markdown_top_level_sections_ignores_non_top_level_headers():
    bio = """### Race
- Khajiit

## Appearance
- Blue eyes
"""
    filtered, matched = _filter_markdown_top_level_sections(bio, {"race"})
    assert filtered == bio
    assert matched == set()


def test_context_filter_logs_missing_sections(caplog):
    ctx = Context.__new__(Context)
    ctx._context__config = SimpleNamespace(
        enable_bio_section_filter=True,
        bio_sections_to_exclude_list=["race", "personal history"],
    )
    ctx._context__npcs_in_conversation = SimpleNamespace(contains_player_character=lambda: True)

    caplog.set_level("DEBUG")
    filtered = ctx._filter_bio_sections_for_prompt(
        "## Race\n- Khajiit\n\n## Appearance\n- Blue eyes",
        "Test NPC",
    )

    assert "## Race" not in filtered
    assert "## Appearance" in filtered
    assert any(
        "no matching '##' header found in Test NPC's bio for: personal history" in record.message
        for record in caplog.records
    )


def test_context_filter_skips_when_no_player_in_conversation():
    ctx = Context.__new__(Context)
    ctx._context__config = SimpleNamespace(
        enable_bio_section_filter=True,
        bio_sections_to_exclude_list=["race"],
    )
    ctx._context__npcs_in_conversation = SimpleNamespace(contains_player_character=lambda: False)

    bio = "## Race\n- Khajiit"
    assert ctx._filter_bio_sections_for_prompt(bio, "Test NPC") == bio


def test_should_filter_memory_bios_requires_both_toggles_and_player():
    config = SimpleNamespace(
        enable_bio_section_filter=True,
        enable_bio_section_filter_for_memory=True,
    )
    assert should_filter_memory_bios(config, contains_player=True) is True
    assert should_filter_memory_bios(config, contains_player=False) is False

    config.enable_bio_section_filter_for_memory = False
    assert should_filter_memory_bios(config, contains_player=True) is False

    config.enable_bio_section_filter_for_memory = True
    config.enable_bio_section_filter = False
    assert should_filter_memory_bios(config, contains_player=True) is False


def test_should_filter_conversation_bios_ignores_memory_toggle():
    config = SimpleNamespace(
        enable_bio_section_filter=True,
        enable_bio_section_filter_for_memory=False,
    )
    assert should_filter_conversation_bios(config, contains_player=True) is True
    assert should_filter_conversation_bios(config, contains_player=False) is False
