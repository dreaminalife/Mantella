from src.config.config_loader import ConfigLoader
from src.ui.tab_reorder import (
    BIO_EDITOR_TAB,
    DOCUMENTATION_TAB,
    LIVE_CONVERSATION_TAB,
    TAB_REORDER_JS,
    TAG_EDITOR_TAB,
    default_tab_names,
    merge_tab_order,
    tab_order_needs_apply,
)


def test_merge_tab_order_keeps_saved_order_and_appends_new_tabs():
    defaults = ["Game", "LLM", LIVE_CONVERSATION_TAB, BIO_EDITOR_TAB, DOCUMENTATION_TAB]
    saved = [DOCUMENTATION_TAB, "Game", "Missing Tab"]

    assert merge_tab_order(defaults, saved) == [
        DOCUMENTATION_TAB,
        "Game",
        "LLM",
        LIVE_CONVERSATION_TAB,
        BIO_EDITOR_TAB,
    ]


def test_a_tab_missing_from_the_saved_order_goes_where_it_belongs_not_at_the_end():
    # An order saved before the Tag Editor existed
    defaults = ["Game", BIO_EDITOR_TAB, TAG_EDITOR_TAB, DOCUMENTATION_TAB]
    saved = [BIO_EDITOR_TAB, "Game", DOCUMENTATION_TAB]

    assert merge_tab_order(defaults, saved) == [BIO_EDITOR_TAB, TAG_EDITOR_TAB, "Game", DOCUMENTATION_TAB]


def test_merge_tab_order_uses_defaults_when_saved_is_empty():
    defaults = ["Game", "LLM", DOCUMENTATION_TAB]
    assert merge_tab_order(defaults, []) == defaults


def test_default_tab_names_inserts_extra_tabs(tmp_path):
    config = ConfigLoader(tmp_path)
    names = default_tab_names(config.definitions.base_groups)

    assert "Game" in names
    assert "Hidden" not in names
    assert names.index("Prompt Profiles") < names.index(LIVE_CONVERSATION_TAB)
    assert names[-3:] == [BIO_EDITOR_TAB, TAG_EDITOR_TAB, DOCUMENTATION_TAB]


def test_the_live_conversation_tab_is_now_the_conversation_tab_and_its_settings_have_no_tab_of_their_own(tmp_path):
    names = default_tab_names(ConfigLoader(tmp_path).definitions.base_groups)
    assert LIVE_CONVERSATION_TAB == "Conversation" and "Live Conversation" not in names
    assert names.count("Conversation") == 1
    for section in ("All Conversations", "Group Conversation", "Shout", "Director Mode", "NPC Initiative", "Conversation Event", "Nearby Event", "Random Event", "Watched NPC Event"):
        assert section not in names


def test_a_saved_order_with_the_old_tab_name_places_the_renamed_tab_where_it_belongs():
    defaults = ["Game", "Prompt Profiles", LIVE_CONVERSATION_TAB, BIO_EDITOR_TAB]
    saved = [BIO_EDITOR_TAB, "Game", "Prompt Profiles", "Live Conversation"]
    assert merge_tab_order(defaults, saved) == [BIO_EDITOR_TAB, "Game", "Prompt Profiles", LIVE_CONVERSATION_TAB]


def test_save_ui_tab_order_persists_without_marking_config_changed(tmp_path):
    config = ConfigLoader(tmp_path)
    assert config.has_any_config_value_changed is False

    order = ["Documentation", "Game", "Bio Editor"]
    config.save_ui_tab_order(order)

    assert config.has_any_config_value_changed is False
    assert config.get_ui_tab_order() == order

    reloaded = ConfigLoader(tmp_path)
    assert reloaded.get_ui_tab_order() == order


def test_save_ui_tab_order_does_not_clear_existing_change_flag(tmp_path):
    config = ConfigLoader(tmp_path)
    config.definitions.get_config_value_definition("port").value = 12345
    assert config.has_any_config_value_changed is True

    config.save_ui_tab_order(["Game", "Documentation"])
    assert config.has_any_config_value_changed is True


def test_tab_reorder_script_is_safe_to_embed_in_html():
    assert "</script>" not in TAB_REORDER_JS.lower()


def test_tab_order_needs_apply_skips_when_already_in_saved_order():
    current = ["Documentation", "Game", "LLM"]
    assert tab_order_needs_apply(current, ["Documentation", "Game", "LLM"]) is False
    assert tab_order_needs_apply(current, None) is False
    assert tab_order_needs_apply(current, []) is False


def test_tab_order_needs_apply_when_saved_order_differs():
    current = ["Game", "LLM", "Documentation"]
    assert tab_order_needs_apply(current, ["Documentation", "Game", "LLM"]) is True


def test_tab_reorder_script_skips_dom_moves_when_order_matches():
    assert "ordersMatch(labels, desired)" in TAB_REORDER_JS
    assert "mutationTouchesTabNav" in TAB_REORDER_JS
    assert "DRAG_THRESHOLD_PX = 24" in TAB_REORDER_JS
