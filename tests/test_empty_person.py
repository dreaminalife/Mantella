"""The game can send a person with no reference and no name, as it once did when a save made while a conversation started was loaded.
Mantella leaves them out instead of failing the request"""
from src.game_manager import GameStateManager
from src.http.communication_constants import communication_constants as comm_consts


def empty_person() -> dict:
    """What the game sent for that person"""
    return {comm_consts.KEY_ACTOR_BASEID: 0, comm_consts.KEY_ACTOR_CUSTOMVALUES: None, comm_consts.KEY_ACTOR_GENDER: 0,
            comm_consts.KEY_ACTOR_ISENEMY: False, comm_consts.KEY_ACTOR_ISINCOMBAT: False, comm_consts.KEY_ACTOR_ISPLAYER: False,
            comm_consts.KEY_ACTOR_NAME: "", comm_consts.KEY_ACTOR_RACE: "None", comm_consts.KEY_ACTOR_REFID: 0,
            comm_consts.KEY_ACTOR_RELATIONSHIPRANK: 0, comm_consts.KEY_ACTOR_VOICETYPE: "None"}


def test_an_empty_person_is_left_out():
    manager = object.__new__(GameStateManager)  # Nothing else of the manager is used for an empty person
    assert manager.load_character(empty_person()) is None
