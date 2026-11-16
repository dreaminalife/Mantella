"""Advanced Actions: what goes with an NPC's reply request so the AI model can pick actions with their details (tool calling), and what is
needed to read its picks back"""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from src.actions.function_manager import FunctionManager
from src.config.definitions.action_definitions import WITHOUT_PLAYER
from src.conversation.action import Action
if TYPE_CHECKING:
    from src.characters_manager import Characters
    from src.conversation.context import Context


@dataclass
class ToolRequest:
    tools: list[dict] # The tools sent to the model
    actions: list[Action] # The actions they are for: only these are kept from the model's picks
    include_player: bool # False while the NPCs don't know the player is there: nothing may name the player
    free_places: int | None # How many more people AddToConversation can call over (Most People in Conversation)
    game: Any # The Gameable, for the names and lists the actions need (Emote's animations, ShareConversation's recipient)
    several_speakers: bool = False # One reply speaks for several NPCs: each action names who does it


def free_places(context: Context, include_player: bool) -> int:
    """How many more people can join before Most People in Conversation, the player included while they take part"""
    characters: Characters = context.npcs_in_conversation
    npcs = len([c for c in characters.get_all_characters() if not c.is_player_character])
    count = npcs + (1 if include_player and characters.contains_player_character() else 0)
    return max(0, int(getattr(context.config, "shout_max_people", 6)) - count)


def tool_request_for(conversation_type: Any, context: Context, game: Any, several_speakers: bool = False) -> ToolRequest | None:
    """The tools for the next reply in this conversation, or None if Advanced Actions is off or the NPCs can't use any action here

    Args:
        several_speakers (bool): one reply speaks for several NPCs (a group conversation without Game Master Mode, a radiant conversation)
    """
    actions: list[Action] = conversation_type.tool_actions(context)
    if not actions:
        return None
    include_player = conversation_type.action_kind() not in WITHOUT_PLAYER
    places = free_places(context, include_player)
    try:
        base_game_name = context.config.game.base_game.display_name
    except Exception:
        base_game_name = "Skyrim"
    tools = FunctionManager.generate_tools(actions, context.npcs_in_conversation, game, base_game_name, include_player, places, several_speakers)
    if not tools:
        return None
    return ToolRequest(tools, actions, include_player, places, game, several_speakers)
