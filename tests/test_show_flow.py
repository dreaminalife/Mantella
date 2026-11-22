"""Show (hotkey or Conversation Wheel): during a conversation the player shows the NPCs what they see. The game asks Mantella right away
to take a picture of the game window, and the picture goes with the player's next line. Whole conversations on the pretend game and
models of tests/test_actions_flow.py, with the picture taking replaced by a set picture"""
import logging
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from src.config.config_loader import ConfigLoader
from src.config.definitions.action_definitions import ACTION_KINDS, actions_key
from src.game_manager import GameStateManager
from src.http.communication_constants import communication_constants as comm_consts
from src.llm.client_base import ProviderError
from src.llm.image_client import VisionCapture
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage
from tests.test_actions_flow import ActionGame, ScriptedModel, offers
from tests.test_conversation_events_flow import HULDA, MIKAEL, YSOLDA, manager # noqa: F401 (fixtures)

SHOWN_NOTE = "shows you what they are looking at"


class ShowModel(ScriptedModel):
    """ScriptedModel that also keeps the picture each NPC reply request was sent with: the VisionCapture, or None without a picture.
    With fail_with_picture, a request with a picture fails, as with a model that can't read pictures"""
    def __init__(self):
        super().__init__()
        self.pictures: list[VisionCapture | None] = []
        self.fail_with_picture = False

    async def streaming_call(self, messages, is_multi_npc: bool, response_started_at: float | None = None, tools: list[dict] | None = None):
        picture = getattr(messages, "vision_capture", None) if getattr(self, "_image_client", None) is not None else None
        self.pictures.append(picture)
        if picture is not None and self.fail_with_picture:
            raise ProviderError("This model can't read images")
        reply = super().streaming_call(messages, is_multi_npc, response_started_at, tools)
        try:
            async for part in reply:
                yield part
        finally:
            await reply.aclose() # Frees the reply lock right away when Mantella closes this stream, as with a real client


@pytest.fixture
def model() -> ShowModel:
    return ShowModel()


@pytest.fixture
def camera(manager: GameStateManager) -> MagicMock:
    """What takes the pictures of the game window. Its pictures are "picture 1", "picture 2"..."""
    chat_manager = manager._GameStateManager__chat_manager
    taker = MagicMock()
    taker.get_image.side_effect = [f"picture {i}" for i in range(1, 20)]
    chat_manager._ChatManager__picture_taker = taker
    chat_manager._ChatManager__show_client = MagicMock() # Adds the picture to the request. Not called by the pretend model
    return taker


@pytest.fixture
def game(manager: GameStateManager, default_config: ConfigLoader) -> ActionGame:
    default_config.automatic_greeting = False
    default_config.vision_enabled = False # Show works with Vision off, as most players have it
    for kind in ACTION_KINDS:
        setattr(default_config, actions_key(kind), ["Look", "Wait"])
    return ActionGame(manager, [YSOLDA])


def show(manager: GameStateManager) -> bool:
    """The player presses Show. Returns whether the game is told the picture was taken"""
    reply = manager.show_picture()
    assert reply[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_SHOWRECEIVED
    return reply[comm_consts.KEY_SHOW_TAKEN]


def images(model: ShowModel) -> list[str | None]:
    return [picture.image if picture else None for picture in model.pictures]


def test_the_shown_picture_goes_with_the_next_line_only(game: ActionGame, model: ShowModel, camera: MagicMock, manager: GameStateManager):
    game.start(HULDA)
    assert show(manager)
    game.say("What do you make of that?")
    assert images(model)[-1] == "picture 1"
    assert SHOWN_NOTE in model.npc_requests[-1]
    game.say("Shall we go?")
    assert images(model)[-1] is None # Not sent again
    assert SHOWN_NOTE in model.npc_requests[-1] # The note stays, so the earlier lines still make sense


def test_nothing_is_sent_before_the_player_says_something(game: ActionGame, model: ShowModel, camera: MagicMock, manager: GameStateManager):
    game.start(HULDA)
    requests = len(model.npc_requests)
    assert show(manager)
    assert len(model.npc_requests) == requests


def test_showing_again_replaces_the_picture(game: ActionGame, model: ShowModel, camera: MagicMock, manager: GameStateManager):
    game.start(HULDA)
    show(manager)
    show(manager)
    game.say("Look at this.")
    assert images(model)[-1] == "picture 2"
    assert model.npc_requests[-1].count(SHOWN_NOTE) == 1


def test_show_does_nothing_outside_a_conversation(game: ActionGame, camera: MagicMock, manager: GameStateManager):
    assert not show(manager)
    camera.get_image.assert_not_called()


def test_show_does_nothing_while_the_player_watches_npcs_talk(game: ActionGame, model: ShowModel, camera: MagicMock, manager: GameStateManager,
                                                             default_config: ConfigLoader):
    default_config.npc_event_max_lines_without_player = 2
    game.around = [HULDA, MIKAEL, YSOLDA]
    model.participants = [HULDA.name, MIKAEL.name]
    model.picks = [HULDA.name, MIKAEL.name]
    shown: list[bool] = []
    game.at_line[1] = lambda: shown.append(show(manager))
    game.npc_event_check()
    assert shown == [False]
    camera.get_image.assert_not_called()


def test_when_the_picture_cant_be_taken_the_game_is_told_and_nothing_is_sent(game: ActionGame, model: ShowModel, camera: MagicMock,
                                                                            manager: GameStateManager):
    camera.get_image.side_effect = [None]
    game.start(HULDA)
    assert not show(manager)
    game.say("What do you make of that?")
    assert images(model)[-1] is None and SHOWN_NOTE not in model.npc_requests[-1]


def test_in_a_group_every_npc_answering_the_line_sees_the_picture(game: ActionGame, model: ShowModel, camera: MagicMock, manager: GameStateManager):
    game.start(HULDA, MIKAEL)
    show(manager)
    model.picks = [HULDA.name, MIKAEL.name]
    game.say("What do you two make of that?")
    assert images(model)[-2:] == ["picture 1", "picture 1"]
    assert camera.get_image.call_count == 1 # One picture, taken when the player pressed Show


def test_with_vision_on_the_shown_picture_is_used_and_look_isnt_needed(game: ActionGame, model: ShowModel, camera: MagicMock,
                                                                       manager: GameStateManager, default_config: ConfigLoader):
    default_config.vision_enabled = True
    manager._GameStateManager__chat_manager._ChatManager__vision_client = MagicMock()
    game.start(HULDA)
    show(manager)
    requests = len(model.npc_requests)
    model.lines = ["Look: A dragon, on the hill."]
    game.say("What do you make of that?")
    assert images(model)[requests:] == ["picture 1"] # Asked once: she sees the shown picture already, so "Look:" asks for no other
    assert game.lines_of(HULDA.name)[-1] == "A dragon, on the hill."
    model.lines = ["Look: Let me see.", "Nothing there now."]
    game.say("And now?")
    assert model.saw_screenshot[-2:] == [False, True] # Back to Look once the picture is used
    assert images(model)[-1] is None # Looking takes a new screenshot, not the shown picture


def test_a_model_that_cant_read_the_picture_answers_without_it(game: ActionGame, model: ShowModel, camera: MagicMock, manager: GameStateManager,
                                                              caplog):
    model.fail_with_picture = True
    game.start(HULDA)
    show(manager)
    with caplog.at_level(logging.DEBUG):
        game.say("What do you make of that?")
    assert images(model)[-2:] == ["picture 1", None]
    assert game.lines_of(HULDA.name) # She still answers
    assert "sent again without the picture" in caplog.text
    game.say("Shall we go?")
    assert images(model)[-1] is None


def test_in_director_mode_the_picture_is_the_scene_not_something_the_player_shows(game: ActionGame, model: ShowModel, camera: MagicMock,
                                                                                  manager: GameStateManager, default_config: ConfigLoader):
    # The player's character isn't part of the scene in Director Mode
    default_config.multi_conversation_director_mode = True
    game.start(HULDA, MIKAEL)
    show(manager)
    model.picks = [HULDA.name, MIKAEL.name]
    game.say("A dragon lands on the hill.")
    assert images(model)[-1] == "picture 1"
    assert "This is how the scene looks right now." in model.npc_requests[-1]
    assert SHOWN_NOTE not in model.npc_requests[-1]


def test_the_picture_is_shared_by_every_copy_of_the_line():
    # Game Master Mode copies the conversation for each NPC's request: a Custom Vision Model then describes the picture only once
    picture = VisionCapture("picture")
    message = UserMessage(MagicMock(), "Look at this.", "Player")
    message.shown_picture = picture
    assert deepcopy(message).shown_picture is picture


def test_a_new_line_forgets_the_pictures_shown_before(default_config: ConfigLoader):
    thread = message_thread(default_config, None)
    line = UserMessage(default_config, "Look at this.", "Player")
    line.shown_picture = VisionCapture("picture")
    thread.add_message(line)
    assert thread.shown_picture() is line.shown_picture
    thread.forget_shown_pictures()
    assert thread.shown_picture() is None

def test_replies_to_an_event_after_the_line_dont_get_the_picture_again(game: ActionGame, model: ShowModel, camera: MagicMock,
                                                                      manager: GameStateManager):
    game.start(HULDA)
    show(manager)
    game.say("What do you make of that?")
    assert images(model)[-1] == "picture 1"
    requests = len(model.pictures)
    model.picks = [HULDA.name] # The Game Master picks her to react first
    game.pick_event("conversation")
    assert len(model.pictures) > requests # Hulda reacted to the event
    assert images(model)[requests:] == [None] * (len(model.pictures) - requests)


def test_a_failed_show_never_ends_the_conversation(caplog):
    # The game ends the conversation on an error reply. A failure only means the picture wasn't taken
    from src.http.routes.mantella_route import mantella_route
    route = mantella_route.__new__(mantella_route)
    route._mantella_route__game = MagicMock(show_picture=MagicMock(side_effect=RuntimeError("window gone")))
    with caplog.at_level(logging.DEBUG):
        reply = route._mantella_route__process_show({comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_SHOW})
    assert reply == {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_SHOWRECEIVED, comm_consts.KEY_SHOW_TAKEN: False}
    assert "Show failed" in caplog.text
