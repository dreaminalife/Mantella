from typing import Any
from src.character_manager import Character
from src import utils

class Characters:
    """Manages a list of NPCs
    """
    def __init__(self):
        self.__active_characters: dict[str, Character] = {}
        self.__all_characters: dict[str, Character] = {} # Actives characters + those that were removed
        self.__last_added_character: Character | None = None
        self.__player_character: Character | None = None
        self.__nearby_npcs: list[dict[str, Any]] = [] # Who the game says is near the player and not in the conversation: {"name", "distance"}
        self.__pending_shares: list[tuple[str, str, str]] = [] # ShareConversation: (who shares, with whom, their ref id), written when the conversation ends
        self.__last_reply_actions: dict[str, list[dict[str, Any]]] = {} # Advanced Actions: by NPC name, the actions of their last reply that could pick some
    
    def __len__(self) -> int:
        return len(self.__active_characters)
    
    @utils.time_it
    def contains_character(self, character_to_check: str | Character) -> bool:
        if isinstance(character_to_check, Character):
            return self.__active_characters.__contains__(character_to_check.name)
        else:
            return self.__active_characters.__contains__(character_to_check)
    
    @property
    def last_added_character(self) -> Character | None:
        return self.__last_added_character
    
    def active_character_count(self):
        return len(self.__active_characters)
    
    @utils.time_it
    def add_or_update_character(self, new_character: Character):
        if not self.__active_characters.__contains__(new_character.name): #Is add
            self.__active_characters[new_character.name] = new_character   
            self.__all_characters[new_character.name] = new_character
            if new_character.is_player_character:
                self.__player_character = new_character
            else:
                self.__last_added_character = new_character
        else: #Is update: update transient stats + custom values
            self.__active_characters[new_character.name].is_enemy = new_character.is_enemy
            self.__active_characters[new_character.name].is_in_combat = new_character.is_in_combat
            self.__active_characters[new_character.name].relationship_rank = new_character.relationship_rank
            self.__active_characters[new_character.name].custom_character_values = new_character.custom_character_values
            self.__active_characters[new_character.name].equipment = new_character.equipment
            # Unchanged unless the bio was rebuilt, e.g. after quest tags changed in a catch-up
            self.__active_characters[new_character.name].bio = new_character.bio
    
    @utils.time_it
    def remove_character(self, character_to_remove: Character):
        if self.__active_characters.__contains__(character_to_remove.name):
            del self.__active_characters[character_to_remove.name]
            if character_to_remove.is_player_character:
                self.__player_character = None
            if character_to_remove == self.__last_added_character:
                for (name, character) in self.__active_characters.items():
                    if not character.is_player_character:
                        self.__last_added_character = character
    
    @utils.time_it
    def get_character_by_name(self, name: str) -> Character:
        return self.__active_characters[name]
        
    @utils.time_it
    def get_all_characters(self) -> list[Character]:
        return list(self.__active_characters.values())
    
    @utils.time_it
    def get_all_names(self) -> list[str]:
        return list(self.__active_characters.keys())
    
    @utils.time_it
    def contains_player_character(self) -> bool:
        return self.__player_character != None
    
    @utils.time_it
    def get_player_character(self) -> Character | None:
        return self.__player_character
    
    @utils.time_it
    def contains_multiple_npcs(self) -> bool:
        return self.active_character_count() > 2 or (self.active_character_count() == 2 and not self.contains_player_character())

    def get_player_name(self) -> str | None:
        return self.__player_character.name if self.__player_character else None

    def set_nearby_npcs(self, nearby_npcs: list[dict[str, Any]] | None):
        """Who the game says is near the player and not in the conversation, sent with each update: [{"name", "distance"}]"""
        self.__nearby_npcs = [npc for npc in (nearby_npcs or []) if isinstance(npc, dict) and npc.get('name')]

    def get_nearby_npc_names(self) -> list[str]:
        """The people nearby who are not in the conversation (Advanced Actions can name them)"""
        return [npc['name'] for npc in self.__nearby_npcs if npc['name'] not in self.__active_characters]

    def get_all_names_w_nearby(self, include_player: bool = True, include_nearby: bool = False, nearby_only: bool = False) -> list[str]:
        """The names an advanced action can name, by its scope

        Args:
            include_player (bool): the player too
            include_nearby (bool): the people nearby too
            nearby_only (bool): only the people nearby
        """
        names = [] if nearby_only else [name for name, character in self.__active_characters.items() if include_player or not character.is_player_character]
        if include_nearby or nearby_only:
            names.extend(name for name in self.get_nearby_npc_names() if name not in names)
            if include_player and nearby_only and self.__player_character and self.__player_character.name not in names:
                names.append(self.__player_character.name)
        return names

    def add_pending_share(self, sharer_name: str, recipient_name: str, recipient_ref_id: str) -> bool:
        """ShareConversation: the recipient learns about this conversation when it ends. False if they already will"""
        if any(ref_id == recipient_ref_id for _, _, ref_id in self.__pending_shares):
            return False
        self.__pending_shares.append((sharer_name, recipient_name, recipient_ref_id))
        return True

    def get_last_reply_actions(self, name: str) -> list[dict[str, Any]]:
        """Advanced Actions: what the NPC did in their last reply that could pick actions. Empty if nothing, or if they haven't had one"""
        return self.__last_reply_actions.get(name, [])

    def set_last_reply_actions(self, actions_by_name: dict[str, list[dict[str, Any]]]):
        """Advanced Actions: what each NPC who spoke or acted in a reply did in it. The others keep what they did before"""
        self.__last_reply_actions.update(actions_by_name)

    def is_share_pending(self, recipient_ref_id: str) -> bool:
        return any(ref_id == recipient_ref_id for _, _, ref_id in self.__pending_shares)

    def get_pending_shares(self) -> list[tuple[str, str, str]]:
        return self.__pending_shares.copy()

    def clear_pending_shares(self):
        self.__pending_shares.clear()