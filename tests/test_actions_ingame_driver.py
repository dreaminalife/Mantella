"""The in-game test of the actions (tests/ingame/actions/actions_ingame.py) is only run by hand, in the game. These check the parts of it
that can go wrong without the game, so a run isn't wasted: its settings exist, MantellaSoftware's model client understands the stand-in
AI service, and it expects the same checks the game's test script notes"""
import os
import re

from openai import OpenAI

from src.config.config_loader import ConfigLoader
from tests.ingame.actions.actions_ingame import ANSWERS, CHECKS, MODEL, PLAIN_ANSWER, answer_for, game_notes, settings_for_test, start_stand_in
from tests.ingame.talk_settings.talk_settings_ingame import write_settings

SPELL_PATH = os.environ.get("MANTELLA_SPELL_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "Mantella-Spell"))


def test_every_setting_the_test_changes_exists(tmp_path):
    ConfigLoader(str(tmp_path)) # Writes a config.ini with every setting
    assert write_settings(os.path.join(tmp_path, "config.ini"), settings_for_test(5123)) == []


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


def test_the_check_expects_the_checks_the_games_script_notes():
    path = os.path.join(SPELL_PATH, "Scripts", "Source", "MantellaActionTest.psc")
    if not os.path.exists(path):
        import pytest
        pytest.skip("Mantella-Spell not found")
    with open(path, encoding="utf-8") as f:
        noted = re.findall(r'Check\([^\n]*?, "([\w-]+)", "', f.read())
    assert noted == CHECKS


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
