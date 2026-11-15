"""Tests for BioTemplateManager dynamic-event extraction and bio expansion."""
import pytest
from unittest.mock import MagicMock
from src.bio_template_manager import BioTemplateManager


@pytest.fixture
def manager(tmp_path):
    """Create a BioTemplateManager with a temporary templates folder."""
    templates_folder = str(tmp_path / "bio_templates")
    # Pass no config_loader so tag reading is always enabled
    mgr = BioTemplateManager(templates_folder, config_loader=None)
    return mgr


class TestExpandBioWithTags:
    def test_static_tag_appended(self, manager):
        manager.templates["warrior"] = "A fierce fighter."
        result = manager.expand_bio_with_tags("Base bio.", "warrior")
        assert "Base bio." in result
        assert "## Additional info" in result
        assert "A fierce fighter." in result

    def test_dynamic_lines_excluded_from_bio(self, manager):
        manager.templates["quest"] = (
            "Involved in the main quest.\n"
            "- 1000: Defeated the first boss\n"
            "- 2000: Became champion"
        )
        result = manager.expand_bio_with_tags("Base bio.", "quest")
        assert "Involved in the main quest." in result
        assert "Defeated the first boss" not in result
        assert "Became champion" not in result

    def test_pure_dynamic_tag_not_appended(self, manager):
        """If a tag description is entirely dynamic events, nothing is appended to the bio."""
        manager.templates["events"] = "- 100: event A\n- 200: event B"
        result = manager.expand_bio_with_tags("Base bio.", "events")
        assert result == "Base bio."

    def test_tag_reading_disabled(self, manager):
        config = MagicMock()
        config.enable_character_tag_reading = False
        manager.config_loader = config
        manager.templates["warrior"] = "A fierce fighter."
        result = manager.expand_bio_with_tags("Base bio.", "warrior")
        assert result == "Base bio."

    def test_duplicate_tags_are_deduplicated(self, manager):
        manager.templates["warrior"] = "A fierce fighter."
        manager.templates["noble"] = "From a noble house."
        result = manager.expand_bio_with_tags("Base bio.", "warrior, noble, warrior")
        assert result.count("A fierce fighter.") == 1
        assert result.count("From a noble house.") == 1


class TestExtractDynamicEvents:
    def test_no_events(self, manager):
        manager.templates["warrior"] = "A fierce fighter."
        events = manager.extract_dynamic_events("warrior")
        assert events == []

    def test_extracts_events(self, manager):
        manager.templates["quest"] = (
            "Static text.\n"
            "- 300: event C\n"
            "- 100: event A\n"
            "- 200: event B"
        )
        events = manager.extract_dynamic_events("quest")
        # Should be sorted by timestamp
        assert events == [(100, "event A"), (200, "event B"), (300, "event C")]

    def test_multiple_tags(self, manager):
        manager.templates["tag1"] = "- 100: from tag1"
        manager.templates["tag2"] = "- 50: from tag2"
        events = manager.extract_dynamic_events("tag1, tag2")
        # Sorted: tag2's event (50) before tag1's (100)
        assert events == [(50, "from tag2"), (100, "from tag1")]

    def test_duplicate_tags_only_emit_events_once(self, manager):
        manager.templates["tag1"] = "- 100: from tag1"
        events = manager.extract_dynamic_events("tag1, tag1")
        assert events == [(100, "from tag1")]

    def test_empty_tags(self, manager):
        events = manager.extract_dynamic_events("")
        assert events == []

    def test_nan_tags(self, manager):
        events = manager.extract_dynamic_events(float('nan'))
        assert events == []

    def test_tag_reading_disabled(self, manager):
        config = MagicMock()
        config.enable_character_tag_reading = False
        manager.config_loader = config
        manager.templates["quest"] = "- 100: event"
        events = manager.extract_dynamic_events("quest")
        assert events == []


def _write_csv(folder, name, text):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


@pytest.fixture
def quest_manager_factory(tmp_path):
    """BioTemplateManager reading base, mod and personal folders, with Auto Quest Tag Outcomes set"""
    from types import SimpleNamespace
    from src.config.definitions.llm_definitions import AutoQuestTagOutcomesEnum

    def make(mode=AutoQuestTagOutcomesEnum.STATIC):
        config = SimpleNamespace(
            mod_path_base=str(tmp_path / "mod") + "/",
            save_folder=str(tmp_path / "personal") + "/",
            enable_character_tag_reading=True,
            auto_quest_tag_outcomes=mode,
        )
        return BioTemplateManager(str(tmp_path / "base"), config_loader=config)
    return make


QUEST_HEADER = "tag,description,potential_paste_1,code_1,potential_paste_2,code_2,announce\n"


class TestQuestOutcomes:
    def test_empty_description_tag_is_kept(self, tmp_path, quest_manager_factory):
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER + 'saadia_status,,Saadia has died.,"""0D7505"".getdead=1",Saadia has disappeared.,getstagedone MS08 201=1,\n')
        manager = quest_manager_factory()
        assert manager.get_outcome_codes() == {"saadia_status": {1: '"0D7505".getdead=1', 2: "getstagedone MS08 201=1"}}
        assert manager.get_outcome_text("saadia_status", 2) == "Saadia has disappeared."

    def test_files_are_merged_cell_by_cell(self, tmp_path, quest_manager_factory):
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER + "battle,,Won.,getstagedone CWObj 100=1,Lost.,getstagedone CWObj 200=1,\n")
        mod_folder = tmp_path / "mod" / "SKSE" / "Plugins" / "MantellaSoftware" / "data" / "Skyrim" / "bio_templates"
        _write_csv(mod_folder, "q.csv", QUEST_HEADER + "battle,,,getstagedone CWObj 110=1,,,\n")
        personal_folder = tmp_path / "personal" / "data" / "Skyrim" / "bio_templates"
        _write_csv(personal_folder, "q.csv", QUEST_HEADER + "battle,,My wording.,,,,no\n")
        manager = quest_manager_factory()
        assert manager.get_outcome_codes()["battle"] == {1: "getstagedone CWObj 110=1", 2: "getstagedone CWObj 200=1"}
        assert manager.get_outcome_text("battle", 1) == "My wording."
        assert manager.get_outcome_text("battle", 2) == "Lost."
        assert "battle" in manager.no_announce

    def test_later_duplicate_row_in_one_file_wins_cell_by_cell(self, tmp_path, quest_manager_factory):
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER + "greybeards,First.,,,,,\ngreybeards,,,,,,\n")
        manager = quest_manager_factory()
        assert manager.get_template("greybeards") == "First."

    def test_static_adds_outcome_right_before_the_description(self, tmp_path, quest_manager_factory):
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER + "battle,Hand-written.,Won.,getstagedone CWObj 100=1,,,\n")
        manager = quest_manager_factory()
        assert manager.get_outcome_codes() == {"battle": {1: "getstagedone CWObj 100=1"}}
        outcomes = {"battle": (1, 500)}
        assert manager.expand_bio_with_tags("Base bio.", "battle", outcomes) == "Base bio.\n\n## Additional info\nWon.\nHand-written."
        assert manager.extract_dynamic_events("battle", outcomes) == []

    def test_static_puts_outcome_on_its_own_line_before_a_description_of_several_lines(self, tmp_path, quest_manager_factory):
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER + 'battle,"Knows:\n- The walls are old.",Won.,getstagedone CWObj 100=1,,,\n')
        manager = quest_manager_factory()
        bio = manager.expand_bio_with_tags("Base bio.", "battle", {"battle": (1, 500)})
        # The test file is written with Windows line ends, which the description keeps
        assert bio.replace("\r\n", "\n") == "Base bio.\n\n## Additional info\nWon.\nKnows:\n- The walls are old."

    def test_description_is_used_alone_while_no_outcome_is_true(self, tmp_path, quest_manager_factory):
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER + "battle,Hand-written.,Won.,getstagedone CWObj 100=1,,,\n")
        manager = quest_manager_factory()
        assert manager.expand_bio_with_tags("Base bio.", "battle", {}) == "Base bio.\n\n## Additional info\nHand-written."

    def test_dynamic_keeps_description_in_bio_and_dates_outcome_with_hand_written_events(self, tmp_path, quest_manager_factory):
        from src.config.definitions.llm_definitions import AutoQuestTagOutcomesEnum
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER + 'battle,"Hand-written.\n- 100: Scouts were seen.\n- 900: The dead were buried.",Won.,getstagedone CWObj 100=1,,,\n')
        manager = quest_manager_factory(AutoQuestTagOutcomesEnum.DYNAMIC)
        outcomes = {"battle": (1, 500)}
        assert manager.expand_bio_with_tags("Base bio.", "battle", outcomes) == "Base bio.\n\n## Additional info\nHand-written."
        assert manager.extract_dynamic_events("battle", outcomes) == [(100, "Scouts were seen."), (500, "Won."), (900, "The dead were buried.")]

    def test_custom_placeholder_is_never_used(self, tmp_path, quest_manager_factory):
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER + "battle,,custom,getstagedone CWObj 100=1,,,\n")
        manager = quest_manager_factory()
        assert manager.get_outcome_text("battle", 1) is None
        assert manager.expand_bio_with_tags("Base bio.", "battle", {"battle": (1, 500)}) == "Base bio."

    def test_static_adds_outcome_to_bio_only(self, tmp_path, quest_manager_factory):
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER + "battle,,Won for {player_name}.,getstagedone CWObj 100=1,,,\n")
        manager = quest_manager_factory()
        outcomes = {"battle": (1, 500)}
        assert manager.expand_bio_with_tags("Base bio.", "battle", outcomes) == "Base bio.\n\n## Additional info\nWon for {player_name}."
        assert manager.extract_dynamic_events("battle", outcomes) == []

    def test_dynamic_adds_dated_event_only(self, tmp_path, quest_manager_factory):
        from src.config.definitions.llm_definitions import AutoQuestTagOutcomesEnum
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER + "battle,,Won.,getstagedone CWObj 100=1,,,\n")
        manager = quest_manager_factory(AutoQuestTagOutcomesEnum.DYNAMIC)
        outcomes = {"battle": (1, 500)}
        assert manager.expand_bio_with_tags("Base bio.", "battle", outcomes) == "Base bio."
        assert manager.extract_dynamic_events("battle", outcomes) == [(500, "Won.")]

    def test_dynamic_puts_outcome_true_before_tracking_in_bio(self, tmp_path, quest_manager_factory):
        """Time 0 means already true at the first check: background in the bio, not a memory placed before every summary"""
        from src.config.definitions.llm_definitions import AutoQuestTagOutcomesEnum
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER
                   + "battle,,Won.,getstagedone CWObj 100=1,,,\n"
                   + "letter,,Delivered.,getstagedone FreeformRiverwood01 240=1,,,\n")
        manager = quest_manager_factory(AutoQuestTagOutcomesEnum.DYNAMIC)
        outcomes = {"battle": (1, 0), "letter": (1, 500)}
        assert manager.expand_bio_with_tags("Base bio.", "battle,letter", outcomes) == "Base bio.\n\n## Additional info\nWon."
        assert manager.extract_dynamic_events("battle,letter", outcomes) == [(500, "Delivered.")]

    def test_off_adds_nothing(self, tmp_path, quest_manager_factory):
        from src.config.definitions.llm_definitions import AutoQuestTagOutcomesEnum
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER + "battle,,Won.,getstagedone CWObj 100=1,,,\n")
        manager = quest_manager_factory(AutoQuestTagOutcomesEnum.OFF)
        outcomes = {"battle": (1, 500)}
        assert manager.expand_bio_with_tags("Base bio.", "battle", outcomes) == "Base bio."
        assert manager.extract_dynamic_events("battle", outcomes) == []

    def test_hand_written_events_identical_with_static_and_dynamic(self, tmp_path, quest_manager_factory):
        from src.config.definitions.llm_definitions import AutoQuestTagOutcomesEnum
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER
                   + 'diary,"Keeps a diary.\n- 100: Wrote a page.",,,,,\n'
                   + "battle,,Won.,getstagedone CWObj 100=1,,,\n")
        outcomes = {"battle": (1, 500)}
        static_events = quest_manager_factory(AutoQuestTagOutcomesEnum.STATIC).extract_dynamic_events("diary,battle", outcomes)
        dynamic_events = quest_manager_factory(AutoQuestTagOutcomesEnum.DYNAMIC).extract_dynamic_events("diary,battle", outcomes)
        assert static_events == [(100, "Wrote a page.")]
        assert [e for e in dynamic_events if e[1] != "Won."] == static_events


MODE_HEADER = "tag,description,potential_paste_1,code_1,outcome_mode\n"


class TestOutcomeMode:
    """A tag's own `outcome_mode` decides where its outcome goes, whatever the Auto Quest Tag Outcomes setting says"""

    OUTCOMES = {"battle": (1, 500), "letter": (1, 600)}

    def _two_tags(self, tmp_path, battle_mode):
        _write_csv(tmp_path / "base", "q.csv", MODE_HEADER
                   + f"battle,,Won.,getstagedone CWObj 100=1,{battle_mode}\n"
                   + "letter,,Delivered.,getstagedone FreeformRiverwood01 240=1,\n")

    def test_dynamic_tag_while_the_setting_is_static(self, tmp_path, quest_manager_factory):
        self._two_tags(tmp_path, "dynamic")
        manager = quest_manager_factory()
        assert manager.expand_bio_with_tags("Base bio.", "battle,letter", self.OUTCOMES) == "Base bio.\n\n## Additional info\nDelivered."
        assert manager.extract_dynamic_events("battle,letter", self.OUTCOMES) == [(500, "Won.")]

    def test_static_tag_while_the_setting_is_dynamic(self, tmp_path, quest_manager_factory):
        from src.config.definitions.llm_definitions import AutoQuestTagOutcomesEnum
        self._two_tags(tmp_path, "Static ")
        manager = quest_manager_factory(AutoQuestTagOutcomesEnum.DYNAMIC)
        assert manager.expand_bio_with_tags("Base bio.", "battle,letter", self.OUTCOMES) == "Base bio.\n\n## Additional info\nWon."
        assert manager.extract_dynamic_events("battle,letter", self.OUTCOMES) == [(600, "Delivered.")]

    def test_dynamic_tag_still_puts_an_outcome_true_before_tracking_in_the_bio(self, tmp_path, quest_manager_factory):
        self._two_tags(tmp_path, "dynamic")
        manager = quest_manager_factory()
        outcomes = {"battle": (1, 0)}
        assert manager.expand_bio_with_tags("Base bio.", "battle", outcomes) == "Base bio.\n\n## Additional info\nWon."
        assert manager.extract_dynamic_events("battle", outcomes) == []

    def test_off_tag_is_not_checked_and_only_its_description_is_used(self, tmp_path, quest_manager_factory):
        _write_csv(tmp_path / "base", "q.csv", MODE_HEADER
                   + "battle,Hand-written.,Won.,getstagedone CWObj 100=1,off\n"
                   + "letter,,Delivered.,getstagedone FreeformRiverwood01 240=1,\n")
        manager = quest_manager_factory()
        assert manager.get_outcome_codes() == {"letter": {1: "getstagedone FreeformRiverwood01 240=1"}}
        # An outcome recorded before the tag was set to off is left out too
        assert manager.expand_bio_with_tags("Base bio.", "battle", self.OUTCOMES) == "Base bio.\n\n## Additional info\nHand-written."
        assert manager.extract_dynamic_events("battle", self.OUTCOMES) == []

    def test_the_setting_off_wins_over_every_tag(self, tmp_path, quest_manager_factory):
        from src.config.definitions.llm_definitions import AutoQuestTagOutcomesEnum
        self._two_tags(tmp_path, "dynamic")
        manager = quest_manager_factory(AutoQuestTagOutcomesEnum.OFF)
        assert manager.expand_bio_with_tags("Base bio.", "battle,letter", self.OUTCOMES) == "Base bio."
        assert manager.extract_dynamic_events("battle,letter", self.OUTCOMES) == []

    def test_an_unknown_value_follows_the_setting(self, tmp_path, quest_manager_factory):
        self._two_tags(tmp_path, "sometimes")
        manager = quest_manager_factory()
        assert manager.outcome_modes == {}
        assert manager.extract_dynamic_events("battle", self.OUTCOMES) == []
        assert "Won." in manager.expand_bio_with_tags("Base bio.", "battle", self.OUTCOMES)

    def test_a_later_file_changes_or_undoes_an_earlier_files_choice(self, tmp_path, quest_manager_factory):
        self._two_tags(tmp_path, "dynamic")
        personal_folder = tmp_path / "personal" / "data" / "Skyrim" / "bio_templates"
        # An empty cell changes nothing; `default` goes back to following the setting
        _write_csv(personal_folder, "a.csv", MODE_HEADER + "battle,,,,\n")
        assert quest_manager_factory().outcome_modes == {"battle": "dynamic"}
        _write_csv(personal_folder, "a.csv", MODE_HEADER + "battle,,,,off\n")
        assert quest_manager_factory().outcome_modes == {"battle": "off"}
        _write_csv(personal_folder, "a.csv", MODE_HEADER + "battle,,,,default\n")
        manager = quest_manager_factory()
        assert manager.outcome_modes == {}
        assert manager.sources["battle"]["outcome_mode"][-1][2] == "default"
        manager.reload_templates()
        assert manager.outcome_modes == {}


class TestCurlyApostrophes:
    """Spreadsheets turn ' into ’ as you type: either spelling of a tag name must match the other"""

    def test_curly_npc_tag_finds_straight_template(self, tmp_path, quest_manager_factory):
        _write_csv(tmp_path / "base", "t.csv", "tag,description\nrisaad's_caravan,Travels with the caravan.\n")
        result = quest_manager_factory().expand_bio_with_tags("Base bio.", "risaad’s_caravan")
        assert "Travels with the caravan." in result

    def test_straight_npc_tag_finds_curly_template(self, tmp_path, quest_manager_factory):
        _write_csv(tmp_path / "base", "t.csv", "tag,description\nrisaad’s_caravan,Travels with the caravan.\n")
        result = quest_manager_factory().expand_bio_with_tags("Base bio.", "risaad's_caravan")
        assert "Travels with the caravan." in result

    def test_both_spellings_count_as_one_tag(self, tmp_path, quest_manager_factory):
        _write_csv(tmp_path / "base", "t.csv", "tag,description\nrisaad's_caravan,Travels with the caravan.\n")
        result = quest_manager_factory().expand_bio_with_tags("Base bio.", "risaad's_caravan, risaad‘s_caravan")
        assert result.count("Travels with the caravan.") == 1

    def test_files_with_either_spelling_are_merged(self, tmp_path, quest_manager_factory):
        _write_csv(tmp_path / "base", "q.csv", QUEST_HEADER + "risaad's_caravan,,Risaad left.,getstagedone Q 10=1,,,\n")
        _write_csv(tmp_path / "personal" / "data" / "Skyrim" / "bio_templates", "q.csv",
                   QUEST_HEADER + "risaad’s_caravan,,Risaad packed up and left.,,,,no\n")
        manager = quest_manager_factory()
        assert manager.get_outcome_codes() == {"risaad's_caravan": {1: "getstagedone Q 10=1"}}
        assert manager.get_outcome_text("risaad’s_caravan", 1) == "Risaad packed up and left."
        assert manager.no_announce == {"risaad's_caravan"}
        assert "Risaad packed up and left." in manager.expand_bio_with_tags("Base bio.", "risaad’s_caravan", {"risaad's_caravan": (1, 0)})
