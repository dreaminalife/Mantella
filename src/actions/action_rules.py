"""Which actions an NPC can use in a conversation: the switches of its kind of conversation on the Conversation page (Actions section), without
the actions that would involve the player while the NPCs don't know the player is there. The prompt lists these, and only these are taken
from an NPC's line, so both always agree"""
from typing import Any
from src.config.definitions.action_definitions import ACTION_KINDS, NOT_OFFERED, WITHOUT_PLAYER, actions_key, default_ticks
from src.conversation.action import Action

# The basic actions whose game script acts on the player: the speaker attacks, brawls with, runs from or jumps to the player, opens a menu for the
# player, follows the player or walks to them
INVOLVE_PLAYER = {"mantella_npc_offended", "mantella_npc_brawl", "mantella_npc_flee", "mantella_npc_teleport", "mantella_npc_inventory",
                  "mantella_npc_barter", "mantella_npc_follow", "mantella_npc_unfollow", "mantella_npc_moveto",
                  "mantella_npc_absolvecrime", "mantella_npc_reportcrime"} # The crimes are the player's

LISTEN = "mantella_npc_listen" # Gives the player more time to speak. Never sent to the game


def all_actions(config: Any) -> list[Action]:
    """Every action loaded from data/actions that an NPC can start its line with"""
    actions = getattr(config, "all_actions", None)
    return [a for a in actions if a.identifier not in NOT_OFFERED] if isinstance(actions, list) else []


def ticked_names(config: Any, kind: str) -> set[str]:
    """The names ticked for a kind of conversation, or its defaults if the config has none (e.g. a test config)"""
    names = getattr(config, actions_key(kind), None)
    if isinstance(names, (list, tuple, set, frozenset)):
        return set(names)
    return set(default_ticks(all_actions(config), kind))


def actions_for(config: Any, kind: str) -> list[Action]:
    """The actions NPCs can use in this kind of conversation (see action_definitions.ACTION_KINDS)"""
    if kind not in ACTION_KINDS:
        return []
    ticked = ticked_names(config, kind)
    return [a for a in all_actions(config) if a.name in ticked and not (kind in WITHOUT_PLAYER and a.identifier in INVOLVE_PLAYER)]
