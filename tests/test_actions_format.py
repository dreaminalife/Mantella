"""How actions travel to the game: each one is {"identifier": ...}, with "arguments" only for advanced actions.
Without arguments the game does the action on the NPC who spoke (MantellaConversation.RaiseActionEvent), as before"""
import json
from typing import Any

import pytest

from src.conversation.conversation_serializer import deserialize_persistent_messages
from src.game_manager import GameStateManager
from src.http.communication_constants import communication_constants as comm_consts
from src.llm.message_thread import message_thread
from src.llm.sentence_content import has_action
from tests.test_conversation_events_flow import HULDA, FakeGame, FakeModel, manager # noqa: F401 (manager is a fixture)


class LineModel(FakeModel):
    """FakeModel whose NPCs say the given lines"""

    def __init__(self):
        super().__init__()
        self.npc_lines: list[str] = []

    async def streaming_call(self, messages: message_thread, is_multi_npc: bool, response_started_at: float | None = None):
        if not self.npc_lines:
            async for text in super().streaming_call(messages, is_multi_npc, response_started_at):
                yield text
            return
        yield self.npc_lines.pop(0)


class ActionGame(FakeGame):
    """FakeGame that keeps the actions as the game receives them"""

    def __init__(self, manager: GameStateManager):
        super().__init__(manager)
        self.talk_actions: list[tuple[str, str, list[dict[str, Any]]]] = [] # (speaker, line, actions) of each NPC line

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        reply = super().send(request)
        if reply.get(comm_consts.KEY_REPLYTYPE) == comm_consts.KEY_REPLYTYPE_NPCTALK and reply.get(comm_consts.KEY_REPLYTYPE_NPCTALK):
            talk = reply[comm_consts.KEY_REPLYTYPE_NPCTALK]
            self.talk_actions.append((talk[comm_consts.KEY_ACTOR_SPEAKER], talk[comm_consts.KEY_ACTOR_LINETOSPEAK], talk[comm_consts.KEY_ACTOR_ACTIONS]))
        return reply


@pytest.fixture
def model() -> LineModel:
    return LineModel()


@pytest.fixture
def game(manager: GameStateManager) -> ActionGame:
    return ActionGame(manager)


def test_an_action_in_an_npcs_line_reaches_the_game_without_arguments(game: ActionGame, model: LineModel):
    model.npc_lines = ["Hello there.", "Inventory: Have a look."]
    game.start(HULDA)
    game.say("What do you have?")
    speaker, line, actions = game.talk_actions[-1]
    assert (speaker, line) == (HULDA.name, "Have a look.")
    assert actions == [{"identifier": comm_consts.ACTION_NPC_INVENTORY}]


def test_typing_an_actions_name_makes_the_npc_do_it(game: ActionGame, model: LineModel):
    game.start(HULDA)
    reply = game.send({comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_PLAYERINPUT, comm_consts.KEY_REQUESTTYPE_PLAYERINPUT: "Inventory",
                       comm_consts.KEY_ACTORS: game.actors(), comm_consts.KEY_CONTEXT: game.context()})
    assert reply[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_NPCACTION
    assert reply[comm_consts.KEY_REPLYTYPE_NPCACTION] == {comm_consts.KEY_ACTOR_SPEAKER: HULDA.name,
                                                          comm_consts.KEY_ACTOR_ACTIONS: [{"identifier": comm_consts.ACTION_NPC_INVENTORY}]}


def test_has_action_ignores_the_arguments():
    actions = [{"identifier": comm_consts.ACTION_NPC_FOLLOW, "arguments": {"source": ["Lydia"]}}]
    assert has_action(actions, comm_consts.ACTION_NPC_FOLLOW)
    assert not has_action(actions, comm_consts.ACTION_NPC_WAIT)


def test_a_conversation_saved_before_actions_had_arguments_still_loads(default_config):
    saved = [{"type": "assistant", "sentences": [{"speaker_ref_id": "1", "speaker_name": "Hulda", "speaker_base_id": "1", "text": "Lead on.",
                                                  "sentence_type": "SPEECH", "actions": [comm_consts.ACTION_NPC_FOLLOW]}]}]
    message = deserialize_persistent_messages(json.dumps(saved), default_config)[0]
    sentence = message._AssistantMessage__sentences[0]
    assert sentence.actions == [{"identifier": comm_consts.ACTION_NPC_FOLLOW}]
