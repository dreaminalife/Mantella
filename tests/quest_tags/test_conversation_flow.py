"""End-to-end test of quest tags in Mantella: whole conversations, sent as Mantella-Spell sends them, with made-up answers.

Covers the doc's in-game tests on the Mantella side (docs/quest_tags_1_user_edits.md, "Tests in the game"):
the list delivered with the start reply and the catch-up (15), the first check as background (4), announcements
with the player's line (1, 10), what the NPC with the tag is told next time (2), and loading an older save (3).
No AI or TTS is called: NPC lines are never generated, and the conversation is short enough that no summary is written.
"""
import csv
import json
import os
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.config.config_loader import ConfigLoader
from src.conversation.conversation import Conversation
from src.game_manager import GameStateManager
from src.games.skyrim import Skyrim
from src.http.communication_constants import communication_constants as comm_consts
from src.llm.messages import UserMessage
from src.output_manager import ChatManager
from src.quest_tags import history

WORLD_ID = "Test1"
NPC_NAME = "Quest Test Npc"
BATTLE_TEXT = "{player_name} won the battle."
SAADIA_TEXT = "Saadia has died."
QUIET_TEXT = "Only the quiet tag's owner knows this."

TEMPLATE_ROWS = [
    ["tag", "description", "potential_paste_1", "code_1", "potential_paste_2", "code_2", "announce"],
    ["zz_battle", "", "", "", BATTLE_TEXT, "getstagedone CWObj 100=1", ""],
    ["zz_saadia", "", SAADIA_TEXT, '"0D7505".getdead=1', "", "", ""],
    ["zz_quiet", "", QUIET_TEXT, "getstagedone CWObj 100=1", "", "", "no"],
]
NPC_OVERRIDE = {"name": NPC_NAME, "base_id": "FF0101", "ref_id": "FF0102", "race": "Nord", "gender": "Male",
                "voice_model": "MaleNord", "bio": "A test NPC.", "tags": "zz_battle, zz_saadia, zz_quiet"}


@pytest.fixture
def game(tmp_path, default_config: ConfigLoader) -> Skyrim:
    """Skyrim with the test tags in the personal bio templates folder, and a test NPC who has them"""
    default_config.mod_path_base = str(tmp_path)
    default_config.save_folder = str(tmp_path)
    templates = tmp_path / "data" / "Skyrim" / "bio_templates"
    templates.mkdir(parents=True)
    with open(templates / "zz_test.csv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(TEMPLATE_ROWS)
    overrides = tmp_path / "data" / "Skyrim" / "character_overrides"
    overrides.mkdir(parents=True)
    (overrides / "zz_test.json").write_text(json.dumps(NPC_OVERRIDE), encoding="utf-8")
    return Skyrim(default_config)


@pytest.fixture
def manager(game: Skyrim, default_config: ConfigLoader, english_language_info, llm_client, monkeypatch) -> GameStateManager:
    # NPCs never speak: the sentence queue stays empty, so each continue request gives the player the turn
    monkeypatch.setattr(Conversation, "_Conversation__start_generating_npc_sentences", lambda self: None)
    chat_manager = ChatManager(default_config, MagicMock(), llm_client, None, "secret_keys.json")
    return GameStateManager(game, chat_manager, default_config, english_language_info, llm_client, None, "STT_SECRET_KEY.txt", "GPT_SECRET_KEY.txt")


@pytest.fixture
def actors(example_player_actor, example_npc_actor) -> list[dict[str, Any]]:
    npc = example_npc_actor.model_dump(by_alias=True, exclude_none=True)
    npc.update({comm_consts.KEY_ACTOR_NAME: NPC_NAME, comm_consts.KEY_ACTOR_BASEID: 0xFF0101, comm_consts.KEY_ACTOR_REFID: 0xFF0102,
                comm_consts.KEY_ACTOR_RACE: "[Race <NordRace (00013746)>]"})
    return [example_player_actor.model_dump(by_alias=True, exclude_none=True), npc]


class Game:
    """Stands in for Mantella-Spell and the check plugin: holds a check list, and answers it from the current game state"""

    def __init__(self, manager: GameStateManager, game: Skyrim, actors: list[dict[str, Any]]):
        self.manager = manager
        self.game = game
        self.actors = actors
        self.list_version = ""  # The plugin holds no list until Mantella sends one
        self.battle_done = 0.0
        self.saadia_dead = 0.0

    def report(self) -> dict[str, Any]:
        answers = []
        for check in self.game.quest_check_list.checks:
            answers.append(self.battle_done if check.param1 == "CWObj" else self.saadia_dead)
        return {comm_consts.KEY_QUESTCHECKS_VERSION: self.list_version, comm_consts.KEY_QUESTCHECKS_RESULTS: answers}

    def start(self) -> dict[str, Any]:
        context = {comm_consts.KEY_CONTEXT_INGAMEEVENTS: [], comm_consts.KEY_CONTEXT_LOCATION: "Whiterun", comm_consts.KEY_CONTEXT_TIME: 12, **self.report()}
        reply = self.manager.start_conversation({
            comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_STARTCONVERSATION, comm_consts.KEY_ACTORS: self.actors,
            comm_consts.KEY_CONTEXT: context, comm_consts.KEY_INPUTTYPE: comm_consts.KEY_INPUTTYPE_TEXT,
            comm_consts.KEY_STARTCONVERSATION_WORLDID: WORLD_ID})
        request: dict[str, Any] = {comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_CONTINUECONVERSATION, comm_consts.KEY_CONTINUECONVERSATION_TOPICINFOFILE: 1}
        if comm_consts.KEY_QUESTCHECKS_LIST in reply:
            # As MantellaConversation.psc does: hand the list to the plugin, and send fresh answers with the actors (the catch-up)
            self.list_version = reply[comm_consts.KEY_QUESTCHECKS_LIST][comm_consts.KEY_QUESTCHECKS_VERSION]
            request.update({**self.report(), comm_consts.KEY_ACTORS: self.actors})
        self.manager.continue_conversation(request)
        return reply

    def say(self, text: str):
        context = {comm_consts.KEY_CONTEXT_INGAMEEVENTS: ["The location is now Whiterun."], **self.report()}
        self.manager.player_input({comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_PLAYERINPUT, comm_consts.KEY_REQUESTTYPE_PLAYERINPUT: text,
                                   comm_consts.KEY_ACTORS: self.actors, comm_consts.KEY_CONTEXT: context})

    def end(self):
        self.manager.end_conversation({comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_ENDCONVERSATION})

    @property
    def talk(self) -> Conversation:
        return self.manager._GameStateManager__talk

    def npc_bio(self) -> str:
        return self.talk.context.npcs_in_conversation.get_character_by_name(NPC_NAME).bio

    def last_player_message_events(self) -> str:
        messages = self.talk._Conversation__messages.get_messages_of_type(UserMessage)
        return messages[-1].get_ingame_events_text()

    def record(self) -> dict[str, dict]:
        return history.load(history.record_path(self.game.conversation_folder_path, WORLD_ID)) or {}


@pytest.fixture
def skyrim_game(manager, game, actors) -> Game:
    return Game(manager, game, actors)


def test_list_comes_with_the_start_reply_and_the_catch_up_is_the_first_check(skyrim_game: Game):
    skyrim_game.battle_done = 1.0  # Won before tracking started
    reply = skyrim_game.start()
    assert comm_consts.KEY_QUESTCHECKS_LIST in reply
    assert skyrim_game.record()["zz_battle"] == {"current": 2, "seen": {"2": 0}}  # Background, never news
    assert BATTLE_TEXT in skyrim_game.npc_bio()  # Rebuilt after the catch-up. {player_name} is filled in when the prompt is built

    skyrim_game.end()
    assert comm_consts.KEY_QUESTCHECKS_LIST not in skyrim_game.start()  # The game's list is up to date now


def test_change_during_a_conversation_is_announced_with_the_players_line(skyrim_game: Game):
    skyrim_game.start()
    skyrim_game.battle_done = 1.0
    skyrim_game.saadia_dead = 1.0
    skyrim_game.say("Did you see that?")

    events = skyrim_game.last_player_message_events()
    assert "Prisoner won the battle." in events
    assert SAADIA_TEXT not in events  # A death is never announced
    assert QUIET_TEXT not in events  # announce = no
    record = skyrim_game.record()
    assert record["zz_battle"]["current"] == 2 and record["zz_battle"]["seen"]["2"] > 0
    assert record["zz_saadia"]["current"] == 1 and record["zz_quiet"]["current"] == 1  # Recorded all the same


def test_the_npc_with_the_tag_knows_it_in_the_next_conversation(skyrim_game: Game):
    skyrim_game.start()
    assert "won the battle" not in skyrim_game.npc_bio()
    skyrim_game.battle_done = 1.0
    skyrim_game.say("Hello.")
    skyrim_game.end()

    skyrim_game.start()
    assert BATTLE_TEXT in skyrim_game.npc_bio()  # Static, the default
    assert QUIET_TEXT in skyrim_game.npc_bio()


def test_loading_an_older_save_forgets_and_the_catch_up_updates_the_npc(skyrim_game: Game):
    skyrim_game.start()
    skyrim_game.battle_done = 1.0
    skyrim_game.say("Hello.")
    skyrim_game.end()

    # Mantella restarts (the plugin's list is gone) and an older save is loaded: the battle hasn't happened there
    skyrim_game.list_version = ""
    skyrim_game.battle_done = 0.0
    skyrim_game.start()
    assert skyrim_game.record()["zz_battle"] == {"current": 0, "seen": {}}
    assert "won the battle" not in skyrim_game.npc_bio()

    skyrim_game.battle_done = 1.0
    skyrim_game.say("Again?")
    assert "Prisoner won the battle." in skyrim_game.last_player_message_events()  # News again in this save


def test_the_games_answers_are_saved_for_troubleshooting(skyrim_game: Game):
    skyrim_game.start()
    path = os.path.join(skyrim_game.game.conversation_folder_path, WORLD_ID, "quest_check_answers.json")
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["version"] == skyrim_game.game.quest_check_list.version
    assert "found in catch-up" in saved["moments"]


def test_director_mode_keeps_the_quest_announcement_but_not_the_players_own_events(skyrim_game: Game, default_config: ConfigLoader):
    """While the player directs, the game's events about the player are left out, but quest news always gets through"""
    default_config.multi_conversation_director_mode = True
    skyrim_game.start()
    assert skyrim_game.talk.is_directed()
    skyrim_game.battle_done = 1.0
    context = {comm_consts.KEY_CONTEXT_INGAMEEVENTS: ["Prisoner picked up / took Iron Sword", "The location is now Whiterun."], **skyrim_game.report()}
    skyrim_game.manager.player_input({comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_PLAYERINPUT,
                                      comm_consts.KEY_REQUESTTYPE_PLAYERINPUT: "A messenger brings news from the front.",
                                      comm_consts.KEY_ACTORS: skyrim_game.actors, comm_consts.KEY_CONTEXT: context})

    events = skyrim_game.last_player_message_events()
    assert "Prisoner won the battle." in events # Names the player, but it is quest news
    assert "Iron Sword" not in events
