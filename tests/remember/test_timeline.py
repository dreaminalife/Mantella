"""Tests for summary-block parsing, dynamic tag event extraction, and
chronological timeline merging.
"""
import pytest
from src.remember.summaries import (
    parse_summary_blocks,
    build_merged_timeline,
    _strip_timestamp_markers,
    get_new_events,
    _build_new_events_section,
)
from src.bio_template_manager import _split_static_and_dynamic, _DYNAMIC_EVENT_RE


# ---------------------------------------------------------------------------
# _DYNAMIC_EVENT_RE
# ---------------------------------------------------------------------------

class TestDynamicEventRegex:
    def test_matches_simple_event(self):
        m = _DYNAMIC_EVENT_RE.match("- 1739270400: Became king")
        assert m is not None
        assert m.group(1) == "1739270400"
        assert m.group(2) == "Became king"

    def test_matches_with_extra_spaces(self):
        m = _DYNAMIC_EVENT_RE.match("-  123:  some event  ")
        assert m is not None
        assert m.group(1) == "123"

    def test_no_match_without_dash(self):
        assert _DYNAMIC_EVENT_RE.match("1739270400: Became king") is None

    def test_no_match_without_timestamp(self):
        assert _DYNAMIC_EVENT_RE.match("- Became king") is None

    def test_no_match_regular_bullet(self):
        assert _DYNAMIC_EVENT_RE.match("- The player visited the tavern") is None


# ---------------------------------------------------------------------------
# _split_static_and_dynamic
# ---------------------------------------------------------------------------

class TestSplitStaticAndDynamic:
    def test_pure_static(self):
        desc = "Known for exceptional combat prowess."
        static, dynamic = _split_static_and_dynamic(desc)
        assert static == desc
        assert dynamic == []

    def test_pure_dynamic(self):
        desc = "- 100: event A\n- 200: event B"
        static, dynamic = _split_static_and_dynamic(desc)
        assert static == ""
        assert dynamic == [(100, "event A"), (200, "event B")]

    def test_mixed(self):
        desc = "Some static text.\n- 1000: dynamic event\nMore static."
        static, dynamic = _split_static_and_dynamic(desc)
        assert "Some static text." in static
        assert "More static." in static
        assert "dynamic event" not in static
        assert dynamic == [(1000, "dynamic event")]

    def test_empty_description(self):
        static, dynamic = _split_static_and_dynamic("")
        assert static == ""
        assert dynamic == []


# ---------------------------------------------------------------------------
# parse_summary_blocks
# ---------------------------------------------------------------------------

class TestParseSummaryBlocks:
    def test_empty_file(self):
        legacy, timestamped = parse_summary_blocks("")
        assert legacy == []
        assert timestamped == []

    def test_legacy_only(self):
        text = "- event A\n\n- event B\n\n"
        legacy, timestamped = parse_summary_blocks(text)
        assert legacy == ["- event A", "- event B"]
        assert timestamped == []

    def test_timestamped_only(self):
        text = "ts=100\n- event A\n\nts=200\n- event B\n\n"
        legacy, timestamped = parse_summary_blocks(text)
        assert legacy == []
        assert timestamped == [(100, "- event A"), (200, "- event B")]

    def test_mixed_legacy_and_timestamped(self):
        text = "- legacy event\n\nts=300\n- new event\n\n"
        legacy, timestamped = parse_summary_blocks(text)
        assert legacy == ["- legacy event"]
        assert timestamped == [(300, "- new event")]

    def test_preserves_legacy_order(self):
        text = "- first\n\n- second\n\n- third\n\n"
        legacy, _ = parse_summary_blocks(text)
        assert legacy == ["- first", "- second", "- third"]

    def test_multiline_block(self):
        text = "ts=500\n- line one\n- line two\n\n"
        legacy, timestamped = parse_summary_blocks(text)
        assert legacy == []
        assert len(timestamped) == 1
        assert timestamped[0][0] == 500
        assert "line one" in timestamped[0][1]
        assert "line two" in timestamped[0][1]

    def test_no_trailing_newline(self):
        text = "- legacy A\n\nts=100\n- timestamped B"
        legacy, timestamped = parse_summary_blocks(text)
        assert legacy == ["- legacy A"]
        assert timestamped == [(100, "- timestamped B")]

    def test_ts_marker_only_block_is_ignored(self):
        """A block containing only a ts= marker with no content should be skipped."""
        text = "ts=999\n\n- legacy event\n\n"
        legacy, timestamped = parse_summary_blocks(text)
        assert legacy == ["- legacy event"]
        assert timestamped == []

    def test_ts_marker_mid_block_starts_new_block(self):
        """Handle files missing blank lines before a new ts marker."""
        text = "- legacy event\nts=200\n- timestamped event\n\n"
        legacy, timestamped = parse_summary_blocks(text)
        assert legacy == ["- legacy event"]
        assert timestamped == [(200, "- timestamped event")]


# ---------------------------------------------------------------------------
# _strip_timestamp_markers
# ---------------------------------------------------------------------------

class TestStripTimestampMarkers:
    def test_strips_ts_lines(self):
        text = "ts=100\n- event A\n\nts=200\n- event B"
        result = _strip_timestamp_markers(text)
        assert "ts=" not in result
        assert "- event A" in result
        assert "- event B" in result

    def test_leaves_non_ts_lines_intact(self):
        text = "- some legacy event\n- another event"
        assert _strip_timestamp_markers(text) == text

    def test_empty_string(self):
        assert _strip_timestamp_markers("") == ""

    def test_ts_like_text_in_content_not_stripped(self):
        """Only lines that are *exactly* ts=<digits> should be stripped."""
        text = "- The ts=100 variable was set"
        assert _strip_timestamp_markers(text) == text


# ---------------------------------------------------------------------------
# build_merged_timeline
# ---------------------------------------------------------------------------

class TestBuildMergedTimeline:
    def test_legacy_only(self):
        result = build_merged_timeline(
            legacy_blocks=["- A", "- B"],
            timestamped_summary_blocks=[],
            dynamic_tag_events=[],
        )
        assert result == "- A\n- B"

    def test_timestamped_only(self):
        result = build_merged_timeline(
            legacy_blocks=[],
            timestamped_summary_blocks=[(200, "- B"), (100, "- A")],
            dynamic_tag_events=[],
        )
        # Should be sorted by timestamp
        assert result == "- A\n- B"

    def test_dynamic_events_only(self):
        result = build_merged_timeline(
            legacy_blocks=[],
            timestamped_summary_blocks=[],
            dynamic_tag_events=[(50, "- X"), (25, "- Y")],
        )
        assert result == "- Y\n- X"

    def test_legacy_then_timestamped(self):
        """Legacy blocks appear first, then timestamped items sorted."""
        result = build_merged_timeline(
            legacy_blocks=["- legacy"],
            timestamped_summary_blocks=[(200, "- ts event")],
            dynamic_tag_events=[(100, "- dyn event")],
        )
        lines = result.split('\n')
        assert lines[0] == "- legacy"
        assert lines[1] == "- dyn event"
        assert lines[2] == "- ts event"

    def test_merge_sort_summary_and_dynamic(self):
        """Timestamped summary blocks and dynamic tag events are interleaved by timestamp."""
        result = build_merged_timeline(
            legacy_blocks=[],
            timestamped_summary_blocks=[(100, "- summary A"), (300, "- summary C")],
            dynamic_tag_events=[(200, "- dynamic B"), (400, "- dynamic D")],
        )
        lines = result.split('\n')
        assert lines == ["- summary A", "- dynamic B", "- summary C", "- dynamic D"]

    def test_dedupe_legacy(self):
        result = build_merged_timeline(
            legacy_blocks=["- A", "- B", "- A"],
            timestamped_summary_blocks=[],
            dynamic_tag_events=[],
            dedupe_legacy=True,
        )
        assert result == "- A\n- B"

    def test_no_dedupe_legacy_by_default(self):
        result = build_merged_timeline(
            legacy_blocks=["- A", "- B", "- A"],
            timestamped_summary_blocks=[],
            dynamic_tag_events=[],
            dedupe_legacy=False,
        )
        assert result == "- A\n- B\n- A"

    def test_empty_everything(self):
        result = build_merged_timeline([], [], [])
        assert result == ""

    def test_no_timestamps_visible(self):
        """The output should never contain ts= markers or raw timestamps."""
        result = build_merged_timeline(
            legacy_blocks=["- old"],
            timestamped_summary_blocks=[(100, "- new summary")],
            dynamic_tag_events=[(200, "- new dynamic")],
        )
        assert "ts=" not in result
        assert "100" not in result
        assert "200" not in result


# ---------------------------------------------------------------------------
# Integration-style test: full flow from raw summary + dynamic events
# ---------------------------------------------------------------------------

class TestFullTimelineFlow:
    def test_backward_compatible_legacy_file(self):
        """A file with no ts= markers should produce identical output to the
        old line-based reader (modulo block semantics)."""
        raw_summary = "- talked about the weather\n\n- discussed politics\n\n"
        legacy, ts_blocks = parse_summary_blocks(raw_summary)
        result = build_merged_timeline(legacy, ts_blocks, [], dedupe_legacy=True)
        assert result == "- talked about the weather\n- discussed politics"

    def test_chronological_ordering_with_all_sources(self):
        """
        Given:
          - 2 legacy blocks (no timestamp)
          - 2 timestamped summary blocks
          - 2 dynamic tag events
        Expected ordering:
          legacy blocks first (original order),
          then all timestamped items sorted by ts.
        """
        raw_summary = (
            "- legacy A\n\n"
            "- legacy B\n\n"
            "ts=300\n- summary C\n\n"
            "ts=500\n- summary E\n\n"
        )
        dynamic_events = [
            (400, "- dynamic D"),
            (200, "- dynamic B2"),
        ]
        legacy, ts_blocks = parse_summary_blocks(raw_summary)
        result = build_merged_timeline(legacy, ts_blocks, dynamic_events, dedupe_legacy=True)
        lines = result.split('\n')
        assert lines == [
            "- legacy A",
            "- legacy B",
            "- dynamic B2",
            "- summary C",
            "- dynamic D",
            "- summary E",
        ]

    def test_malformed_missing_blank_line_still_merges_with_dynamic(self):
        """A missing blank line before ts block should not break merge ordering."""
        raw_summary = "- legacy A\nts=300\n- summary C\n\n"
        dynamic_events = [(200, "- dynamic B")]
        legacy, ts_blocks = parse_summary_blocks(raw_summary)
        result = build_merged_timeline(legacy, ts_blocks, dynamic_events, dedupe_legacy=True)
        assert result.split('\n') == [
            "- legacy A",
            "- dynamic B",
            "- summary C",
        ]


# ---------------------------------------------------------------------------
# _build_new_events_section
# ---------------------------------------------------------------------------

class TestGetNewEvents:
    def test_no_dynamic_events(self):
        result = get_new_events(
            timestamped_summary_blocks=[(100, "- summary")],
            dynamic_tag_events=[],
        )
        assert result == []

    def test_no_new_events(self):
        """Dynamic events at or before the latest summary ts are not new."""
        result = get_new_events(
            timestamped_summary_blocks=[(500, "- summary")],
            dynamic_tag_events=[(400, "- older event"), (500, "- same ts event")],
        )
        assert result == []

    def test_all_events_new_no_summaries(self):
        """With no timestamped summaries, baseline is 0, so all events are new."""
        result = get_new_events(
            timestamped_summary_blocks=[],
            dynamic_tag_events=[(100, "event A"), (200, "event B")],
        )
        assert result == [(100, "event A"), (200, "event B")]

    def test_only_newer_events_included(self):
        result = get_new_events(
            timestamped_summary_blocks=[(300, "- summary")],
            dynamic_tag_events=[(200, "- old"), (400, "- new one"), (500, "- newer one")],
        )
        assert result == [(400, "- new one"), (500, "- newer one")]

    def test_events_sorted_by_timestamp(self):
        result = get_new_events(
            timestamped_summary_blocks=[(100, "- summary")],
            dynamic_tag_events=[(300, "- second"), (200, "- first")],
        )
        assert result == [(200, "- first"), (300, "- second")]


class TestBuildNewEventsSection:
    def test_empty_list(self):
        assert _build_new_events_section([]) == ""

    def test_formats_events(self):
        result = _build_new_events_section([(100, "event A"), (200, "event B")])
        assert "New developments since last meeting:" in result
        assert "- event A" in result
        assert "- event B" in result

    def test_bullet_prefix_added_if_missing(self):
        result = _build_new_events_section([(100, "event without dash")])
        assert "- event without dash" in result

    def test_bullet_prefix_not_doubled(self):
        result = _build_new_events_section([(100, "- already has dash")])
        assert "- already has dash" in result
        assert "- - already has dash" not in result


class TestNewEventsExcludedFromTimeline:
    def test_new_events_not_duplicated(self):
        """New events should appear only in the 'New developments' section,
        not in the main timeline."""
        ts_summaries = [(300, "- summary C")]
        dynamic = [(200, "- old B"), (400, "- new D"), (500, "- new E")]
        new = get_new_events(ts_summaries, dynamic)
        timeline = build_merged_timeline(
            legacy_blocks=[],
            timestamped_summary_blocks=ts_summaries,
            dynamic_tag_events=dynamic,
            exclude_events=new,
        )
        section = _build_new_events_section(new)
        # timeline should contain old event + summary, but NOT the new events
        assert "- old B" in timeline
        assert "- summary C" in timeline
        assert "- new D" not in timeline
        assert "- new E" not in timeline
        # section should contain new events
        assert "- new D" in section
        assert "- new E" in section

    def test_no_exclusion_when_no_new_events(self):
        """If there are no new events, the full timeline is intact."""
        ts_summaries = [(500, "- summary")]
        dynamic = [(200, "- old"), (400, "- also old")]
        new = get_new_events(ts_summaries, dynamic)
        assert new == []
        timeline = build_merged_timeline(
            legacy_blocks=["- legacy"],
            timestamped_summary_blocks=ts_summaries,
            dynamic_tag_events=dynamic,
            exclude_events=new,
        )
        assert "- legacy" in timeline
        assert "- old" in timeline
        assert "- also old" in timeline
        assert "- summary" in timeline
