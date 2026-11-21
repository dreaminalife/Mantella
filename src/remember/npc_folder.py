import os
import src.utils as utils


class NpcFolder:
    """Which folder holds an NPC's memories (summaries, heard lines) in one playthrough.
    The 'Name - ref_id' folder when it exists, else the older 'Name' folder when only that exists, else a new 'Name - ref_id' folder.
    Everything kept for an NPC must use the same choice: writing into a new 'Name - ref_id' folder next to an older 'Name' one would
    make the summaries switch to the new, empty folder"""

    def __init__(self, conversation_folder_path: str):
        self.__conversation_folder_path: str = conversation_folder_path

    @staticmethod
    def base_name(name: str) -> str:
        """The name without the number the game adds to repeated names ("Whiterun Guard 2" -> "Whiterun Guard")"""
        return utils.remove_trailing_number(name)

    def path(self, world_id: str, name: str, ref_id: str) -> str:
        base_name = NpcFolder.base_name(name)
        name_ref_path = self.__folder(world_id, f'{base_name} - {ref_id}')
        if os.path.exists(name_ref_path):
            return name_ref_path
        name_path = self.__folder(world_id, base_name)
        if os.path.exists(name_path):
            return name_path
        return name_ref_path

    def __folder(self, world_id: str, folder_name: str) -> str:
        return os.path.join(self.__conversation_folder_path, world_id, folder_name).replace(os.sep, '/')
