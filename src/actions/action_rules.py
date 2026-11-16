"""Which actions an NPC can use in a conversation: the switches of its kind of conversation on the Conversation page (Actions section), without
the actions that would involve the player while the NPCs don't know the player is there. The prompt lists the basic ones, and only these are
taken from an NPC's line, so both always agree. With Advanced Actions on, the AI model also gets them as tools (see tool_actions_for)"""
from typing import Any
from src.config.definitions.action_definitions import ACTION_KINDS, NOT_OFFERED, WITHOUT_PLAYER, actions_key, default_ticks
from src.conversation.action import Action

# The basic actions whose game script acts on the player: the speaker attacks, brawls with, runs from or jumps to the player, opens a menu for the
# player, follows the player or walks to them
INVOLVE_PLAYER = {"mantella_npc_offended", "mantella_npc_brawl", "mantella_npc_flee", "mantella_npc_teleport", "mantella_npc_inventory",
                  "mantella_npc_barter", "mantella_npc_follow", "mantella_npc_unfollow", "mantella_npc_moveto",
                  "mantella_npc_absolvecrime", "mantella_npc_reportcrime"} # The crimes are the player's
# Of those, the ones that can be aimed at an NPC instead when the AI model picks the action with its details (Advanced Actions): their target
AIMABLE = {"mantella_npc_offended", "mantella_npc_flee", "mantella_npc_teleport", "mantella_npc_moveto"}

LISTEN = "mantella_npc_listen" # Gives the player more time to speak. Never sent to the game
LOOK = "mantella_npc_vision" # The NPC sees a screenshot of the player's view. Never sent to the game
ADD_TO_CONVERSATION = "mantella_npc_addtoconversation" # Its source is who gets called over, not who acts


def all_actions(config: Any) -> list[Action]:
    """Every action loaded from data/actions that NPCs can be given: the basic ones and the ones only Advanced Actions can use"""
    actions = getattr(config, "all_actions", None)
    if not isinstance(actions, list):
        return []
    vision = getattr(config, "vision_enabled", False) is True # Look needs Vision on
    return [a for a in actions if a.identifier not in NOT_OFFERED and (vision or a.identifier != LOOK)]


def offers_look(config: Any, kind: str) -> bool:
    """Look is offered in this kind of conversation: the NPCs only get a screenshot when they use it, instead of with every reply"""
    return any(a.identifier == LOOK for a in actions_for(config, kind) + tool_actions_for(config, kind))


def ticked_names(config: Any, kind: str) -> set[str]:
    """The names ticked for a kind of conversation, or its defaults if the config has none (e.g. a test config)"""
    names = getattr(config, actions_key(kind), None)
    if isinstance(names, (list, tuple, set, frozenset)):
        return set(names)
    return set(default_ticks(all_actions(config), kind))


def actions_for(config: Any, kind: str) -> list[Action]:
    """The basic actions NPCs can use in this kind of conversation (see action_definitions.ACTION_KINDS): the ones the prompt lists and an NPC
    starts its line with"""
    if kind not in ACTION_KINDS:
        return []
    ticked = ticked_names(config, kind)
    return [a for a in all_actions(config) if a.is_basic and a.name in ticked and not (kind in WITHOUT_PLAYER and a.identifier in INVOLVE_PLAYER)]


def is_advanced_on(config: Any) -> bool:
    return getattr(config, "advanced_actions_enabled", False) is True


def tool_actions_for(config: Any, kind: str) -> list[Action]:
    """With Advanced Actions on: the actions the AI model can pick, with their details, in this kind of conversation. The basic ones too, so
    they can be aimed at someone. Where the NPCs don't know the player is there, the ones that act on the player whoever is named are left
    out, and the ones aimed at someone stay, aimed only at NPCs (see FunctionManager.generate_tools). Listen is the player's"""
    if kind not in ACTION_KINDS or not is_advanced_on(config):
        return []
    ticked = ticked_names(config, kind)
    result = []
    for action in all_actions(config):
        if action.name not in ticked or action.identifier == LISTEN:
            continue
        if kind in WITHOUT_PLAYER and action.identifier in INVOLVE_PLAYER and action.identifier not in AIMABLE:
            continue
        result.append(action)
    return result
