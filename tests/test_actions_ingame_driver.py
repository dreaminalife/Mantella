"""The in-game test of the actions (tests/ingame/actions/actions_ingame.py) is only run by hand, in the game. These check the parts of it
that can go wrong without the game, so a run isn't wasted: its settings exist, MantellaSoftware's model client understands the stand-in
AI service, and it expects the same checks the game's test script notes"""
import json
import os
import re
from datetime import datetime, timedelta

import pytest
from openai import OpenAI

from src.config.config_loader import ConfigLoader
from tests.ingame.actions.actions_ingame import (ADVANCED_CHECKS, ANSWERS, CHECKS, MODEL, PLAIN_ANSWER, SHARE_MARKER, TOOL_CUES, answer_for,
                                                answered_back_results, game_notes, settings_for_test, share_result, start_stand_in)
from tests.ingame.talk_settings.talk_settings_ingame import write_settings

SPELL_PATH = os.environ.get("MANTELLA_SPELL_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "Mantella-Spell"))


@pytest.mark.parametrize("advanced", [False, True])
def test_every_setting_the_test_changes_exists(tmp_path, advanced: bool):
    ConfigLoader(str(tmp_path)) # Writes a config.ini with every setting
    assert write_settings(os.path.join(tmp_path, "config.ini"), settings_for_test(5123, advanced)) == []


def test_the_advanced_run_ticks_every_action_it_picks_and_they_exist(tmp_path):
    config = ConfigLoader(str(tmp_path))
    known = {a.name for a in config.all_actions}
    ticked = settings_for_test(5123, True)["actions_one_on_one"].split(", ")
    assert set(ticked) == {name for name, _ in TOOL_CUES.values()}
    assert set(ticked) <= known, f"Not actions Mantella knows: {set(ticked) - known}"


def test_the_stand_in_answers_a_cue_with_its_action():
    assert answer_for({"messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "Prisoner: ACTIONTEST_follow Come with me."}]}) == ANSWERS["follow"]
    assert answer_for({"messages": [{"role": "user", "content": "Hello"}]}) == PLAIN_ANSWER


def test_the_stand_in_reads_a_cue_in_any_case():
    """The game's scripts keep one copy of each word whatever its case, so the game sent ACTIONTEST_Follow for ACTIONTEST_follow"""
    assert answer_for({"messages": [{"role": "user", "content": "ACTIONTEST_Follow Come with me."}]}) == ANSWERS["follow"]


def test_an_openai_client_gets_the_stand_ins_answer_streamed_and_not(tmp_path):
    server = start_stand_in(0, str(tmp_path / "stand_in.log")) # Port 0: any free port
    try:
        client = OpenAI(api_key="abc123", base_url=f"http://127.0.0.1:{server.server_address[1]}/v1")
        messages = [{"role": "user", "content": "ACTIONTEST_inventory What are you carrying?"}]
        whole = client.chat.completions.create(model=MODEL, messages=messages)
        assert whole.choices[0].message.content == ANSWERS["inventory"]
        streamed = "".join(chunk.choices[0].delta.content or "" for chunk in client.chat.completions.create(model=MODEL, messages=messages, stream=True)
                           if chunk.choices)
        assert streamed == ANSWERS["inventory"]
    finally:
        server.shutdown()


def game_test_script() -> tuple[str, str]:
    """MantellaActionTest.psc: the run with Advanced Actions off (Run), and with it on (RunAdvanced)"""
    path = os.path.join(SPELL_PATH, "Scripts", "Source", "MantellaActionTest.psc")
    if not os.path.exists(path):
        pytest.skip("Mantella-Spell not found")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    start = text.index("Function RunAdvanced(")
    end = text.index("EndFunction", start)
    return text[:start], text[start:end]


def checks_noted(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'Check\([^\n]*?, "([\w-]+)", "', text)))


def test_the_check_expects_the_checks_the_games_script_notes():
    basic, advanced = game_test_script()
    assert checks_noted(basic) == CHECKS
    assert checks_noted(advanced) == ADVANCED_CHECKS


def test_every_cue_the_games_script_says_is_one_the_stand_in_answers_with_an_action():
    _basic, advanced = game_test_script()
    said = set(re.findall(r'SayCue\(conversation, "(t[a-z]+)', advanced))
    assert said == set(TOOL_CUES), f"Said but not answered: {said - set(TOOL_CUES)}. Answered but never said: {set(TOOL_CUES) - said}"


def picks_streamed(client: OpenAI, text: str, tools: list[str]) -> tuple[str, list[tuple[str, dict]]]:
    """What MantellaSoftware reads from the stand-in's streamed answer: the line, and the actions picked, put together from their pieces"""
    offered = [{"type": "function", "function": {"name": name, "description": "", "parameters": {"type": "object", "properties": {}}}} for name in tools]
    line, calls = "", {}
    for chunk in client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": text}], stream=True, tools=offered):
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        line += delta.content or ""
        for piece in delta.tool_calls or []:
            call = calls.setdefault(piece.index or 0, {"name": "", "arguments": ""})
            if piece.function:
                call["name"] += piece.function.name or ""
                call["arguments"] += piece.function.arguments or ""
    return line, [(call["name"], json.loads(call["arguments"])) for call in calls.values()]


def test_with_advanced_actions_the_stand_in_picks_the_cues_action_with_its_details(tmp_path):
    server = start_stand_in(0, str(tmp_path / "stand_in.log"))
    try:
        client = OpenAI(api_key="abc123", base_url=f"http://127.0.0.1:{server.server_address[1]}/v1")
        assert picks_streamed(client, "Prisoner: ACTIONTEST_tmoveto@Saadia Go and stand by Saadia.", ["MoveTo", "Wait"]) == \
            (PLAIN_ANSWER, [("MoveTo", {"target": "Saadia"})])
        assert picks_streamed(client, "ACTIONTEST_tcastspell@The_Dragonborn+Healing_Hands Heal me.", ["CastSpell"]) == \
            (PLAIN_ANSWER, [("CastSpell", {"target": "The Dragonborn", "spellName": "Healing Hands"})])
        assert picks_streamed(client, "ACTIONTEST_tbarter What do you sell?", ["Wait"]) == (PLAIN_ANSWER, []) # Not offered: noted for the check
        with open(tmp_path / "action_test_requests.jsonl", encoding="utf-8") as f:
            logged = [json.loads(line) for line in f]
        assert [r["picked"]["name"] if r["picked"] else None for r in logged] == ["MoveTo", "CastSpell", None]
        assert logged[-1]["not_offered"] == "Barter"
    finally:
        server.shutdown()


def game_master_asked(text: str) -> str:
    return json.loads(answer_for({"messages": [{"role": "user", "content": text}]}))["next"]


def test_the_stand_in_game_master_gives_the_player_the_turn_when_it_may():
    assert game_master_asked("The conversation so far:\nPrisoner: ACTIONTEST_twait Wait.\nHulda: Hi.\n\nWho speaks next? Choose one of: "
                             'Hulda, Guard, Prisoner.\nReply only with JSON in this format: {"next": "<name>"}') == "Prisoner"


def test_the_stand_in_game_master_otherwise_picks_the_first_npc_and_finds_the_player_among_names_with_commas():
    # As Mantella asks it once a guard joins after ReportCrime: all on one line, the player not among the choices
    asked = ("The conversation so far: Yuji: ACTIONTEST_treport Report me to the guards, I dare you. Hulda: Alright. *Volmann, Whiterun Guard has "
             "joined the conversation* (Crime reported against the player.) Who speaks next? Choose one of: Hulda, Volmann, Whiterun Guard. "
             'In "also", list everyone else')
    assert game_master_asked(asked) == "Hulda"
    assert game_master_asked(asked.replace("Choose one of: Hulda, Volmann, Whiterun Guard.",
                                           "Choose one of: Volmann, Whiterun Guard, Hulda, Yuji.")) == "Yuji"


def test_an_answer_back_counts_only_in_the_npcs_next_line_without_actions_to_pick():
    picked = {"tools": ["CheckDirections"], "stream": True, "last_user": "ACTIONTEST_tdirections", "picked": {"name": "CheckDirections"}}
    summary = {"tools": None, "stream": False, "last_user": "Riverwood is 3 km south.", "picked": None}
    line = {"tools": None, "stream": True, "last_user": "Riverwood is 3 km south.", "picked": None}
    with_tools = {**line, "tools": ["Wait"]}
    assert answered_back_results([picked, line])["directions-answered"].startswith("PASS")
    assert answered_back_results([picked, summary])["directions-answered"].startswith("FAIL")
    assert answered_back_results([picked, with_tools])["directions-answered"].startswith("FAIL")
    assert answered_back_results([line])["directions-answered"].startswith("FAIL") # Never picked


def test_the_shared_memory_is_found_and_taken_out_again(tmp_path):
    folder = tmp_path / "data" / "Skyrim" / "conversations" / "world" / "Ysolda - 013BAB"
    folder.mkdir(parents=True)
    summary = folder / "Ysolda_summary_1.txt"
    summary.write_text(f"ts=1\n- An older memory.\n\nts=2\n- Hulda {SHARE_MARKER} with Prisoner (the player): Alright.\n\n", encoding="utf-8")
    since = datetime.now() - timedelta(minutes=1)
    assert share_result(str(tmp_path), since).startswith("PASS")
    assert summary.read_text(encoding="utf-8") == "ts=1\n- An older memory.\n\n" # Her own memories stay
    assert share_result(str(tmp_path), since).startswith("FAIL")


def test_the_check_reads_the_latest_run_from_the_games_script_log(tmp_path):
    log = tmp_path / "Papyrus.0.log"
    log.write_text("[11/14/2026 - 06:00:00PM] Action test: starting\n[11/14/2026 - 06:00:01PM] Action test: FAIL follow-on old run\n"
                   "[11/14/2026 - 06:05:00PM] Action test: starting\n[11/14/2026 - 06:05:09PM] Action test: PASS follow-on Follow: the NPC follows you\n",
                   encoding="utf-8")
    assert game_notes(str(log)) == ["starting", "PASS follow-on Follow: the NPC follows you"]


def test_the_check_reads_notes_whose_words_the_game_wrote_in_another_case(tmp_path):
    log = tmp_path / "Papyrus.0.log"
    log.write_text("[11/14/2026 - 06:05:00PM] Action test: Starting\n[11/14/2026 - 06:05:09PM] Action test: PASS Follow-on Follow: the NPC follows you\n",
                   encoding="utf-8")
    assert game_notes(str(log)) == ["Starting", "PASS Follow-on Follow: the NPC follows you"]


def test_the_talk_and_event_checks_show_why_the_game_dropped_an_npc_event(tmp_path):
    from tests.ingame.talk_settings.talk_settings_ingame import game_npc_event_notes
    log = tmp_path / "Papyrus.0.log"
    log.write_text("[11/14/2026 - 08:00:00PM] Talk test: starting\n[11/14/2026 - 08:01:00PM] Mantella NPC Event: can no longer start: old run\n"
                   "[11/14/2026 - 08:09:03PM] Talk test: starting\n[11/14/2026 - 08:10:00PM] Talk test: Shout\n"
                   "[11/14/2026 - 08:11:28PM] Mantella NPC Event: can no longer start: Lucia is in a scene\n"
                   "[11/14/2026 - 08:13:03PM] Talk test: no NPC Event this time (can no longer start: Lucia is in a scene). Run the test again\n",
                   encoding="utf-8")
    assert game_npc_event_notes("Talk test", str(log)) == [
        "Mantella NPC Event: can no longer start: Lucia is in a scene",
        "Talk test: no NPC Event this time (can no longer start: Lucia is in a scene). Run the test again"]
