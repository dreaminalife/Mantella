import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from src.config.config_value_constraint import ConfigValueConstraintResult
from src.config.definitions.prompt_definitions import PromptDefinitions

logger = logging.getLogger(__name__)


class PromptProfileManager:
    """Named prompt overlays stored in JSON. Does not modify the Prompts tab."""

    NONE_ACTIVE_LABEL = "None (use Prompts tab)"
    RESERVED_NAMES = {NONE_ACTIVE_LABEL, "None"}

    def __init__(self, storage_path: Optional[str] = None):
        if storage_path is None:
            if getattr(sys, 'frozen', False):
                exe_dir = os.path.dirname(sys.executable)
                data_dir = Path(exe_dir) / "data"
            else:
                data_dir = Path("data")
            data_dir.mkdir(parents=True, exist_ok=True)
            self.storage_path = data_dir / "prompt_profiles.json"
        else:
            self.storage_path = Path(storage_path)

        self._profiles: dict[str, dict[str, str]] = {}
        self._active: dict[str, Optional[str]] = {}
        self._load()

    @staticmethod
    def prompt_types() -> list[tuple[str, str, list[str] | None]]:
        return PromptDefinitions.get_prompt_profile_types()

    @staticmethod
    def display_names() -> list[str]:
        return [display_name for _identifier, display_name, _allowed in PromptProfileManager.prompt_types()]

    @staticmethod
    def identifier_for_display(display_name: str) -> Optional[str]:
        for identifier, name, _allowed in PromptProfileManager.prompt_types():
            if name == display_name:
                return identifier
        return None

    @staticmethod
    def is_known_type(prompt_type: str) -> bool:
        return any(identifier == prompt_type for identifier, _name, _allowed in PromptProfileManager.prompt_types())

    @staticmethod
    def supported_variables_markdown(prompt_type: str) -> str:
        allowed = PromptDefinitions.get_allowed_variables_for_prompt_type(prompt_type)
        if allowed is None:
            return "**Supported variables:** not validated for this type (any `{variable}` is accepted)."
        if not allowed:
            return "**Supported variables:** none. This type does not accept `{variables}`."
        names = ", ".join(f"`{{{name}}}`" for name in allowed)
        return f"**Supported variables:** {names}"

    def reload(self) -> None:
        self._load()

    def _load(self) -> None:
        self._profiles = {identifier: {} for identifier, _name, _allowed in self.prompt_types()}
        self._active = {identifier: None for identifier, _name, _allowed in self.prompt_types()}
        try:
            if not self.storage_path.exists():
                logger.debug("No prompt profiles found at %s, starting empty", self.storage_path)
                return
            with open(self.storage_path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
            profiles_data = data.get("profiles", {})
            if isinstance(profiles_data, dict):
                for prompt_type, named_prompts in profiles_data.items():
                    if prompt_type not in self._profiles or not isinstance(named_prompts, dict):
                        continue
                    self._profiles[prompt_type] = {
                        str(name): str(text)
                        for name, text in named_prompts.items()
                        if str(name).strip()
                    }
            active_data = data.get("active", {})
            if isinstance(active_data, dict):
                for prompt_type, name in active_data.items():
                    if prompt_type not in self._active:
                        continue
                    if name and str(name) in self._profiles.get(prompt_type, {}):
                        self._active[prompt_type] = str(name)
                    else:
                        self._active[prompt_type] = None
            logger.debug("Loaded prompt profiles from %s", self.storage_path)
        except Exception as e:
            logger.error("Error loading prompt profiles from %s: %s", self.storage_path, e)
            self._profiles = {identifier: {} for identifier, _name, _allowed in self.prompt_types()}
            self._active = {identifier: None for identifier, _name, _allowed in self.prompt_types()}

    def _save(self) -> bool:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "metadata": {"version": "1.0"},
                "profiles": self._profiles,
                "active": self._active,
            }
            with open(self.storage_path, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error("Error saving prompt profiles: %s", e)
            return False

    def validate_text(self, prompt_type: str, text: str) -> ConfigValueConstraintResult:
        allowed = PromptDefinitions.get_allowed_variables_for_prompt_type(prompt_type)
        if allowed is None:
            return ConfigValueConstraintResult()
        return PromptDefinitions.PromptChecker(allowed).apply_constraint(text or "")

    def validate_name(self, name: str) -> ConfigValueConstraintResult:
        stripped = (name or "").strip()
        if not stripped:
            return ConfigValueConstraintResult("Enter a profile name.")
        if stripped in self.RESERVED_NAMES:
            return ConfigValueConstraintResult(f"'{stripped}' is reserved. Choose another name.")
        return ConfigValueConstraintResult()

    def get_profile_names(self, prompt_type: str) -> list[str]:
        return sorted(self._profiles.get(prompt_type, {}).keys(), key=str.lower)

    def get_profile_text(self, prompt_type: str, name: str) -> Optional[str]:
        if not name:
            return None
        return self._profiles.get(prompt_type, {}).get(name)

    def get_active_name(self, prompt_type: str) -> Optional[str]:
        name = self._active.get(prompt_type)
        if name and name in self._profiles.get(prompt_type, {}):
            return name
        return None

    def get_active_text(self, prompt_type: str) -> Optional[str]:
        name = self.get_active_name(prompt_type)
        if not name:
            return None
        return self.get_profile_text(prompt_type, name)

    def save_profile(self, prompt_type: str, name: str, text: str, overwrite: bool = True) -> tuple[bool, str]:
        if not self.is_known_type(prompt_type):
            return False, "Unknown prompt type."
        name_result = self.validate_name(name)
        if not name_result.is_success:
            return False, name_result.error_message
        text_result = self.validate_text(prompt_type, text)
        if not text_result.is_success:
            return False, text_result.error_message
        stripped_name = name.strip()
        existed = stripped_name in self._profiles[prompt_type]
        if existed and not overwrite:
            return False, f"A profile named '{stripped_name}' already exists for this prompt type."
        self._profiles[prompt_type][stripped_name] = text or ""
        if not self._save():
            return False, "Could not save prompt profiles. Check the log."
        action = "Updated" if existed else "Created"
        logger.info("%s prompt profile '%s' for %s", action, stripped_name, prompt_type)
        return True, f"{action} '{stripped_name}'."

    def save_from_editor(self, prompt_type: str, selected: Optional[str], name: str, text: str) -> tuple[bool, str, bool]:
        """Save the editor: update the selected profile, or create one if the name is new.

        Does not activate the saved profile. If the name matches a different existing
        profile, the save is rejected.

        Returns (success, message, created_new).
        """
        name_result = self.validate_name(name)
        if not name_result.is_success:
            return False, name_result.error_message, False
        text_result = self.validate_text(prompt_type, text)
        if not text_result.is_success:
            return False, text_result.error_message, False

        stripped_name = name.strip()
        selected_name = (selected or "").strip() or None
        if selected_name and stripped_name == selected_name:
            ok, msg = self.save_profile(prompt_type, stripped_name, text, overwrite=True)
            return ok, msg, False
        if stripped_name in self._profiles.get(prompt_type, {}):
            return False, f"A profile named '{stripped_name}' already exists for this prompt type.", False
        ok, msg = self.save_profile(prompt_type, stripped_name, text, overwrite=False)
        return ok, msg, ok

    def rename_profile(self, prompt_type: str, old_name: str, new_name: str) -> tuple[bool, str]:
        if not self.is_known_type(prompt_type):
            return False, "Unknown prompt type."
        if not old_name or old_name not in self._profiles.get(prompt_type, {}):
            return False, "Select a profile to rename."
        name_result = self.validate_name(new_name)
        if not name_result.is_success:
            return False, name_result.error_message
        stripped_new = new_name.strip()
        if stripped_new == old_name:
            return True, f"Profile is already named '{old_name}'."
        if stripped_new in self._profiles[prompt_type]:
            return False, f"A profile named '{stripped_new}' already exists for this prompt type."
        self._profiles[prompt_type][stripped_new] = self._profiles[prompt_type].pop(old_name)
        if self._active.get(prompt_type) == old_name:
            self._active[prompt_type] = stripped_new
        if not self._save():
            return False, "Could not save prompt profiles. Check the log."
        logger.info("Renamed prompt profile '%s' -> '%s' for %s", old_name, stripped_new, prompt_type)
        return True, f"Renamed '{old_name}' to '{stripped_new}'."

    def delete_profile(self, prompt_type: str, name: str) -> tuple[bool, str]:
        if not self.is_known_type(prompt_type):
            return False, "Unknown prompt type."
        if not name or name not in self._profiles.get(prompt_type, {}):
            return False, "Select a profile to delete."
        del self._profiles[prompt_type][name]
        was_active = self._active.get(prompt_type) == name
        if was_active:
            self._active[prompt_type] = None
        if not self._save():
            return False, "Could not save prompt profiles. Check the log."
        logger.info("Deleted prompt profile '%s' for %s", name, prompt_type)
        if was_active:
            return True, f"Deleted '{name}'. This type now uses the Prompts tab."
        return True, f"Deleted '{name}'."

    def set_active(self, prompt_type: str, name: Optional[str]) -> tuple[bool, str, bool]:
        """Activate a profile or None to use the Prompts tab.

        Returns (success, message, changed).
        """
        if not self.is_known_type(prompt_type):
            return False, "Unknown prompt type.", False
        if name:
            if name not in self._profiles.get(prompt_type, {}):
                return False, f"Profile '{name}' does not exist for this prompt type.", False
            text_result = self.validate_text(prompt_type, self._profiles[prompt_type][name])
            if not text_result.is_success:
                return False, text_result.error_message, False
        current = self.get_active_name(prompt_type)
        if current == name:
            if name:
                return True, f"'{name}' is already active.", False
            return True, "Already using the Prompts tab for this type.", False
        self._active[prompt_type] = name
        if not self._save():
            return False, "Could not save prompt profiles. Check the log.", False
        if name:
            logger.info("Activated prompt profile '%s' for %s", name, prompt_type)
            return True, f"Activated '{name}'.", True
        logger.info("Cleared active prompt profile for %s", prompt_type)
        return True, "Using the Prompts tab for this type.", True
