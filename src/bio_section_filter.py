import logging
import re


def normalize_bio_section_name(section_name: str) -> str:
    return " ".join(section_name.strip().lower().split())


def filter_markdown_top_level_sections(
    bio_text: str,
    sections_to_exclude: set[str],
) -> tuple[str, set[str]]:
    """Remove selected top-level markdown sections from bio text.

    A top-level bio section is treated as a markdown header line in the form
    `## Section Name`. Lines under that section are removed until the next
    top-level `## ...` header.
    """
    if not bio_text or not sections_to_exclude:
        return bio_text, set()

    lines = bio_text.splitlines()
    header_regex = re.compile(r"^##\s+(.+?)\s*$")

    headers: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        match = header_regex.match(line)
        if not match:
            continue
        normalized_header = normalize_bio_section_name(match.group(1))
        headers.append((idx, normalized_header))

    if not headers:
        return bio_text, set()

    remove_ranges: list[tuple[int, int]] = []
    matched_sections: set[str] = set()
    for i, (start_idx, normalized_header) in enumerate(headers):
        if normalized_header not in sections_to_exclude:
            continue
        end_idx = headers[i + 1][0] if i + 1 < len(headers) else len(lines)
        remove_ranges.append((start_idx, end_idx))
        matched_sections.add(normalized_header)

    if not remove_ranges:
        return bio_text, set()

    filtered_lines: list[str] = []
    for idx, line in enumerate(lines):
        should_remove = any(start <= idx < end for start, end in remove_ranges)
        if not should_remove:
            filtered_lines.append(line)

    return "\n".join(filtered_lines), matched_sections


def should_filter_conversation_bios(config, *, contains_player: bool) -> bool:
    return bool(contains_player) and bool(getattr(config, "enable_bio_section_filter", False))


def should_filter_memory_bios(config, *, contains_player: bool) -> bool:
    """Memory prompts (summary / thoughts / reflection) also need the nested toggle."""
    if not contains_player:
        return False
    return (
        bool(getattr(config, "enable_bio_section_filter", False))
        and bool(getattr(config, "enable_bio_section_filter_for_memory", False))
    )


def apply_bio_section_filter(bio: str, character_name: str, config, *, enabled: bool) -> str:
    """Filter configured top-level markdown bio sections when enabled."""
    if not bio or not enabled:
        return bio

    configured_sections = set(getattr(config, "bio_sections_to_exclude_list", []))
    if not configured_sections:
        logging.debug("Bio section filter enabled, but no sections are configured.")
        return bio

    filtered_bio, matched_sections = filter_markdown_top_level_sections(bio, configured_sections)
    missing_sections = sorted(configured_sections.difference(matched_sections))
    if missing_sections:
        logging.debug(
            f"Bio section filter: no matching '##' header found in {character_name}'s bio for: {', '.join(missing_sections)}"
        )
    return filtered_bio
