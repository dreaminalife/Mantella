"""The Actions section of the Conversation page: which actions NPCs can use in each kind of conversation (see src/actions/action_rules.py)"""
from src.config.config_value_constraint import ConfigValueConstraintResult
from src.config.types.config_value import ConfigValue, ConfigValueTag
from src.config.types.config_value_multi_selection import ConfigValueMultiSelection
from src.conversation.action import Action

# The kinds of conversation that each have their own action switches, as used in the setting keys.
# NPC Initiative uses One-on-one, Say Something to the Room uses Shout, and Talk, Nearby and Random Events those of the conversation they happen in
ONE_ON_ONE = "one_on_one"
GROUP = "group"
SHOUT = "shout"
NPC_EVENT = "npc_event" # Only while the player watches. Once they step in, it is a Shout or a group conversation
DIRECTOR = "director"
RADIANT = "radiant" # Skyrim's own small talk between two NPCs
ACTION_KINDS = [ONE_ON_ONE, GROUP, SHOUT, NPC_EVENT, DIRECTOR, RADIANT]
KIND_NAMES = {ONE_ON_ONE: "One-on-one and NPC Initiative",GROUP: "Group", SHOUT: "Shout", NPC_EVENT: "NPC Events", DIRECTOR: "Director Mode", RADIANT: "Radiant Conversations"}
WITHOUT_PLAYER = {NPC_EVENT, DIRECTOR, RADIANT} # The NPCs don't know the player is there

# Off by default in every kind of conversation, as in the original Mantella
OFF_BY_DEFAULT = {"AbsolveCrime", "AddToConversation", "Brawl", "CancelTravel", "CastSpell", "CheckDirections", "CollectIngredients", "Emote", "Follow",
                  "LeadTo", "Look", "Loot", "ReportCrime", "ShareConversation", "Teleport", "TravelTo", "Unfollow", "Wait"}
# Loaded, but never offered to NPCs:
# - Look needs Mantella to take a screenshot only when an NPC asks for one, which it can't do yet
# - EndConversation: only the player ends a conversation (the end conversation keyword, or in game), so an NPC never ends a scene on them
NOT_OFFERED = {"mantella_npc_vision", "mantella_end_conversation"}


def actions_key(kind: str) -> str:
    return f"actions_{kind}"


def is_on_by_default(action: Action, kind: str) -> bool:
    """The original Mantella's default: its one-on-one switch for One-on-one, multi-NPC for Group and Shout, and radiant (NPCs talking among
    themselves) for the kinds without the player"""
    return action.name not in OFF_BY_DEFAULT and is_allowed_in(action, kind)


def default_ticks(actions: list[Action], kind: str) -> list[str]:
    return [a.name for a in actions if is_on_by_default(a, kind)]


# The old Actions list of the Other tab (active_actions): the names it used, and the actions they are now
OLD_ACTION_NAMES = {"Offended": ["Attack", "StandDown"], "Forgiven": ["StandDown"]}
OLD_ORIGINAL_ACTIONS = ["Follow", "Inventory", "Offended", "Forgiven"] # The original Mantella's actions the old list had
OLD_KEYWORDS = {"offended_npc_response": ("Offended", "Attack"), "forgiven_npc_response": ("Forgiven", "StandDown")} # Their old default keyword and the new one
# The original Mantella's actions. Any other action file comes from another mod (e.g. Actions For Mantella's Sex, Trade and Undress), which the old list also had
ORIGINAL_ACTIONS = {"AbsolveCrime", "AddToConversation", "Attack", "Barter", "Brawl", "CancelTravel", "CastSpell", "CheckDirections", "CollectIngredients",
                    "Emote", "EndConversation", "Flee", "Follow", "Inventory", "LeadTo", "Listen", "Look", "Loot", "MoveTo", "ReportCrime",
                    "ShareConversation", "StandDown", "Teleport", "TravelTo", "Unfollow", "Wait"}


def is_allowed_in(action: Action, kind: str) -> bool:
    """The original Mantella's switch for where the action can be used: one-on-one, multi-NPC (Group and Shout), or radiant (NPCs among themselves)"""
    if kind == ONE_ON_ONE:
        return action.use_in_on_on_one
    if kind in (GROUP, SHOUT):
        return action.use_in_multi_npc
    return action.use_in_radiant


def carried_over_ticks(actions: list[Action], kind: str, old_ticked: list[str]) -> list[str]:
    """A kind's switches for someone updating from the old Actions list. An action that was on in it is on wherever the original Mantella allows it
    (Follow: One-on-one, Group and Shout), one that was off stays off, and the actions new to them start from the defaults

    Args:
        old_ticked (list[str]): the names ticked in the old list
    """
    in_old_list = {new_name for name in OLD_ORIGINAL_ACTIONS for new_name in OLD_ACTION_NAMES.get(name, [name])}
    in_old_list |= {a.name for a in actions if a.name not in ORIGINAL_ACTIONS}
    ticked = {new_name for name in old_ticked for new_name in OLD_ACTION_NAMES.get(name, [name])}
    result = []
    for action in actions:
        if action.name in in_old_list:
            if action.name in ticked and is_allowed_in(action, kind):
                result.append(action.name)
        elif is_on_by_default(action, kind):
            result.append(action.name)
    return result


class ConfigValueActionTicks(ConfigValueMultiSelection):
    """A kind's action switches. An action whose file is gone (e.g. another mod's, removed) is dropped from config.ini instead of failing it"""
    def parse(self, config_value: str) -> ConfigValueConstraintResult:
        names = [x.strip() for x in config_value.split(",")] if config_value.strip() else []
        self.value = [name for name in names if name in self.options]
        return ConfigValueConstraintResult()


class ActionDefinitions:
    # Its own part of the Conversation page, at the end, after the conversation sections: hence the heading
    SECTION_DESCRIPTION = ("### Actions\n"
                           "What NPCs can do while they talk: an NPC starts its line with the action's name (\"Follow: Lead the way\") and does it in the game. "
                           "Each kind of conversation has its own switches. NPC Initiative uses the One-on-one switches, Say Something to the Room the Shout ones, "
                           "and Talk, Nearby and Random Events those of the conversation they happen in. Each NPC acts only for themselves: nobody follows, waits or "
                           "walks over because someone else said so.\n\n"
                           "Follow, Inventory, Attack and StandDown also need Allow Follow, Allow Inventory and Allow Aggro on in the in-game menu (NPC Actions).")

    KIND_DESCRIPTIONS = {
        ONE_ON_ONE: "A conversation with one NPC, also one the NPC started (NPC Initiative).",
        GROUP: "A conversation with several NPCs, not shouting. Also an NPC Event you stepped into while not shouting.",
        SHOUT: "A conversation while shouting, also with one NPC, and Say Something to the Room. Also an NPC Event you stepped into.",
        NPC_EVENT: "An NPC Event while you watch.",
        DIRECTOR: "A conversation you direct (Director Mode), with one NPC or several.",
        RADIANT: "Skyrim's own small talk between two NPCs.",
    }
    WITHOUT_PLAYER_NOTE = (" The NPCs don't know you're there, so actions that would involve you are left out, whatever is ticked: Attack, Brawl, Flee, "
                           "Teleport, Inventory, Barter, Follow, Unfollow and MoveTo.")

    @staticmethod
    def get_actions_config_value(actions: list[Action], kind: str) -> ConfigValue:
        description = ActionDefinitions.KIND_DESCRIPTIONS[kind] + (ActionDefinitions.WITHOUT_PLAYER_NOTE if kind in WITHOUT_PLAYER else "")
        options = [a.name for a in actions]
        return ConfigValueActionTicks(actions_key(kind), f"Actions: {KIND_NAMES[kind]}", description, default_ticks(actions, kind), options,
                                      tags=[ConfigValueTag.tick_boxes])
