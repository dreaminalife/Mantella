import json

import pytest

from src.ui.conversation_table import apply_rows_to_json, delete_selected_rows, json_to_rows


def _conversation_json() -> str:
    return json.dumps([
        {"type": "join", "character": {"ref_id": "1", "name": "Lydia", "base_id": "1"}, "content": "Lydia joined the conversation"},
        {"type": "user", "text": "Hello", "player_character_name": "Dragonborn", "ingame_events": ["It is raining"], "time": ["8", "morning"], "is_system_generated": False, "is_multi_npc": False, "is_director": False},
        {"type": "assistant", "is_multi_npc": False, "is_system_generated": False, "thoughts_in_place": False, "sentences": [
            {"speaker_ref_id": "1", "speaker_name": "Lydia", "speaker_base_id": "1", "text": "Greetings, my Thane.", "sentence_type": "SPEECH", "is_system_generated": False, "actions": ["Follow"]},
            {"speaker_ref_id": "1", "speaker_name": "Lydia", "speaker_base_id": "1", "text": "She bows.", "sentence_type": "NARRATION", "is_system_generated": False, "actions": []},
            {"speaker_ref_id": "1", "speaker_name": "Lydia", "speaker_base_id": "1", "text": "He looks tired.", "sentence_type": "THOUGHT", "is_system_generated": False, "actions": []},
        ]},
        {"type": "user", "text": "Go on.", "player_character_name": "Dragonborn", "is_director": True},
        {"type": "leave", "character": {"ref_id": "1", "name": "Lydia", "base_id": "1"}, "content": "Lydia left the conversation"},
    ], indent=2)


def test_rows_label_each_line():
    assert json_to_rows(_conversation_json()) == [
        ["1", "Lydia", "Joined", "Lydia joined the conversation"],
        ["2.e1", "", "Event", "It is raining"],
        ["2", "Dragonborn", "Player", "Hello"],
        ["3.1", "Lydia", "Speech", "Greetings, my Thane."],
        ["3.2", "Lydia", "Narration", "She bows."],
        ["3.3", "Lydia", "💭 Thought", "He looks tired."],
        ["4", "Dragonborn", "Director", "Go on."],
        ["5", "Lydia", "Left", "Lydia left the conversation"],
    ]


def test_empty_json_gives_no_rows():
    assert json_to_rows("") == []


def test_unchanged_rows_round_trip():
    original = _conversation_json()
    assert json.loads(apply_rows_to_json(original, json_to_rows(original))) == json.loads(original)


def test_edited_text_is_written_back_and_other_fields_kept():
    original = _conversation_json()
    rows = json_to_rows(original)
    rows[3][3] = "Fixed speech."
    rows[5][3] = "Fixed thought."
    rows[2][3] = "Hi there"
    rows[1][3] = "It is snowing"
    rows[0][3] = "Lydia arrives"
    rows[3][1] = "Someone else"  # Only Text is written back

    result = json.loads(apply_rows_to_json(original, rows))

    assert result[0]["content"] == "Lydia arrives"
    assert result[1]["text"] == "Hi there"
    assert result[1]["ingame_events"] == ["It is snowing"]
    sentences = result[2]["sentences"]
    assert sentences[0]["text"] == "Fixed speech."
    assert sentences[0]["speaker_name"] == "Lydia"
    assert sentences[0]["actions"] == ["Follow"]
    assert sentences[2]["text"] == "Fixed thought."
    assert sentences[2]["sentence_type"] == "THOUGHT"


def test_blank_rows_from_the_table_are_ignored():
    original = _conversation_json()
    rows = json_to_rows(original) + [["", "", "", ""]]
    assert json.loads(apply_rows_to_json(original, rows)) == json.loads(original)


@pytest.mark.parametrize("change", ["remove", "add", "reorder"])
def test_structural_changes_are_rejected(change):
    original = _conversation_json()
    rows = json_to_rows(original)
    if change == "remove":
        rows.pop(2)
    elif change == "add":
        rows.append(["6", "Lydia", "Speech", "Extra"])
    else:
        rows[2], rows[3] = rows[3], rows[2]
    with pytest.raises(ValueError, match="Only the Text column"):
        apply_rows_to_json(original, rows)


def test_invalid_raw_json_is_reported():
    with pytest.raises(ValueError, match="Raw JSON is invalid"):
        apply_rows_to_json("{not json", [])


def _sentence(text: str) -> dict:
    return {"speaker_ref_id": "1", "speaker_name": "Lydia", "speaker_base_id": "1", "text": text, "sentence_type": "SPEECH", "is_system_generated": False, "actions": []}


def _texts(json_str: str) -> list:
    return [[s["text"] for s in m["sentences"]] if m["type"] == "assistant" else m.get("text", m.get("content")) for m in json.loads(json_str)]


def test_deleting_sentences_keeps_the_rest_of_the_npc_message_and_saves_text_edits():
    original = _conversation_json()
    rows = json_to_rows(original)
    rows[4][3] = "She bows deeply."
    assert _texts(delete_selected_rows(original, rows, ["3.1", "3.3"])) == [
        "Lydia joined the conversation", "Hello", ["She bows deeply."], "Go on.", "Lydia left the conversation"]


def test_deleting_an_event_keeps_the_player_line_and_other_events():
    original = json.dumps([
        {"type": "user", "text": "Hello", "player_character_name": "Dragonborn", "ingame_events": ["One", "Two", "Three"]},
    ])
    result = json.loads(delete_selected_rows(original, json_to_rows(original), ["1.e2"]))
    assert result == [{"type": "user", "text": "Hello", "player_character_name": "Dragonborn", "ingame_events": ["One", "Three"]}]


def _events_only_json() -> str:
    """Something happened (an event, or the NPCs went on): a message with no text that only carries in-game events"""
    return json.dumps([
        {"type": "user", "text": "", "player_character_name": "", "ingame_events": ["It is raining", "A cart crashes"], "is_director": True},
        {"type": "assistant", "is_multi_npc": False, "sentences": [{"speaker_name": "Lydia", "text": "Look out!", "sentence_type": "SPEECH"}]},
    ])


def test_a_message_with_only_events_shows_only_its_events():
    assert json_to_rows(_events_only_json()) == [
        ["1.e1", "", "Event", "It is raining"],
        ["1.e2", "", "Event", "A cart crashes"],
        ["2.1", "Lydia", "Speech", "Look out!"],
    ]
    original = _events_only_json()
    assert json.loads(apply_rows_to_json(original, json_to_rows(original))) == json.loads(original)


def test_deleting_the_last_event_of_a_message_with_only_events_removes_it():
    original = _events_only_json()
    one_left = json.loads(delete_selected_rows(original, json_to_rows(original), ["1.e1"]))
    assert one_left[0]["ingame_events"] == ["A cart crashes"]
    none_left = json.loads(delete_selected_rows(original, json_to_rows(original), ["1.e1", "1.e2"]))
    assert [item["type"] for item in none_left] == ["assistant"]


def _vanilla_dialogue_json() -> str:
    return json.dumps([
        {"type": "user", "text": "Hello", "player_character_name": "Dragonborn", "ingame_events": ["It is raining"],
         "heard_dialogue": ['Nearby, Mikael says to Carlotta: "Hi."', 'Carlotta says to Mikael: "Go away."']},
        {"type": "assistant", "sentences": [_sentence("Did you hear that?")]},
        {"type": "user", "text": "", "player_character_name": "", "heard_dialogue": ['Nearby, Mikael says: "Hmph."'], "is_director": True},
        {"type": "assistant", "sentences": [_sentence("Poor Mikael.")]},
    ])


def test_vanilla_dialogue_rows_come_after_the_events_and_before_the_player_line():
    assert json_to_rows(_vanilla_dialogue_json()) == [
        ["1.e1", "", "Event", "It is raining"],
        ["1.v1", "", "Vanilla Dialogue", 'Nearby, Mikael says to Carlotta: "Hi."'],
        ["1.v2", "", "Vanilla Dialogue", 'Carlotta says to Mikael: "Go away."'],
        ["1", "Dragonborn", "Player", "Hello"],
        ["2.1", "Lydia", "Speech", "Did you hear that?"],
        ["3.v1", "", "Vanilla Dialogue", 'Nearby, Mikael says: "Hmph."'], # Only carries Vanilla Dialogue: no row of its own
        ["4.1", "Lydia", "Speech", "Poor Mikael."],
    ]


def test_vanilla_dialogue_text_is_written_back():
    original = _vanilla_dialogue_json()
    rows = json_to_rows(original)
    rows[2][3] = 'Carlotta says to Mikael: "Leave."'
    result = json.loads(apply_rows_to_json(original, rows))
    assert result[0]["heard_dialogue"] == ['Nearby, Mikael says to Carlotta: "Hi."', 'Carlotta says to Mikael: "Leave."']
    assert result[0]["ingame_events"] == ["It is raining"]


def test_deleting_a_vanilla_dialogue_line_keeps_the_rest():
    original = _vanilla_dialogue_json()
    result = json.loads(delete_selected_rows(original, json_to_rows(original), ["1.v1"]))
    assert result[0]["heard_dialogue"] == ['Carlotta says to Mikael: "Go away."']
    assert result[0]["ingame_events"] == ["It is raining"] and result[0]["text"] == "Hello"


def test_deleting_a_player_line_removes_its_vanilla_dialogue():
    original = _vanilla_dialogue_json()
    result = json.loads(delete_selected_rows(original, json_to_rows(original), ["1"]))
    assert "Hi." not in json.dumps(result) and "Go away." not in json.dumps(result)


def test_deleting_the_last_vanilla_dialogue_line_of_a_message_with_only_that_removes_it():
    original = _vanilla_dialogue_json()
    result = json.loads(delete_selected_rows(original, json_to_rows(original), ["3.v1"]))
    assert _texts(json.dumps(result)) == ["Hello", ["Did you hear that?", "Poor Mikael."]] # The NPC messages around it are merged


def test_a_message_with_events_and_vanilla_dialogue_stays_until_both_are_deleted():
    original = json.dumps([{"type": "user", "text": "", "ingame_events": ["It is raining"], "heard_dialogue": ['Nearby, Mikael says: "Hmph."'], "is_director": True}])
    assert len(json.loads(delete_selected_rows(original, json_to_rows(original), ["1.e1"]))) == 1
    assert json.loads(delete_selected_rows(original, json_to_rows(original), ["1.e1", "1.v1"])) == []


def test_deleting_a_player_line_removes_its_events():
    original = _conversation_json()
    result = json.loads(delete_selected_rows(original, json_to_rows(original), ["2"]))
    assert "It is raining" not in [row[3] for row in json_to_rows(json.dumps(result))]
    assert "Hello" not in [row[3] for row in json_to_rows(json.dumps(result))]


def test_deleting_every_sentence_removes_the_npc_message():
    original = _conversation_json()
    assert _texts(delete_selected_rows(original, json_to_rows(original), ["3.1", "3.2", "3.3"])) == [
        "Lydia joined the conversation", "Hello", "Go on.", "Lydia left the conversation"]


def test_deleting_a_player_line_between_npc_messages_merges_them():
    original = json.dumps([
        {"type": "user", "text": "Hello", "player_character_name": "Dragonborn"},
        {"type": "assistant", "sentences": [_sentence("One.")]},
        {"type": "user", "text": "Go on.", "player_character_name": "Dragonborn"},
        {"type": "assistant", "sentences": [_sentence("Two.")]},
    ])
    assert _texts(delete_selected_rows(original, json_to_rows(original), ["3"])) == ["Hello", ["One.", "Two."]]


def test_joined_and_left_rows_can_be_deleted():
    original = _conversation_json()
    assert _texts(delete_selected_rows(original, json_to_rows(original), ["1", "5"])) == [
        "Hello", ["Greetings, my Thane.", "She bows.", "He looks tired."], "Go on."]
