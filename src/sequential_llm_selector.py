"""
Sequential LLM Selector for per-request round-robin selection.

Iterates through user-defined pools in listed order, wrapping around after the
last model. Indices reset when a new conversation starts.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from src.model_profile_manager import ModelProfileManager
from src.random_llm_selector import LLMSelection


class SequentialLLMSelector:
    """Selects LLMs from pools in order, wrapping around and skipping invalid entries."""

    def __init__(self, profile_manager: Optional[ModelProfileManager] = None):
        self.profile_manager = profile_manager or ModelProfileManager()
        self._indices: Dict[str, int] = {
            "one_on_one": 0,
            "multi_npc": 0,
        }

    def reset(self) -> None:
        """Return both pools to the first model. Called when a new conversation starts."""
        self._indices["one_on_one"] = 0
        self._indices["multi_npc"] = 0

    def select_next_from_one_on_one_pool(
        self,
        config: Any,
        fallback_service: str,
        fallback_model: str,
        fallback_params: Dict[str, Any],
        fallback_token_count: int
    ) -> LLMSelection | None:
        return self._select_next(
            conversation_type="one_on_one",
            pool=getattr(config, "sequential_llm_pool_one_on_one", []),
            fallback_params=fallback_params,
            fallback_token_count=fallback_token_count,
            apply_profile=getattr(config, "apply_profile_one_on_one", False),
        )

    def select_next_from_multi_npc_pool(
        self,
        config: Any,
        fallback_service: str,
        fallback_model: str,
        fallback_params: Dict[str, Any],
        fallback_token_count: int
    ) -> LLMSelection | None:
        return self._select_next(
            conversation_type="multi_npc",
            pool=getattr(config, "sequential_llm_pool_multi_npc", []),
            fallback_params=fallback_params,
            fallback_token_count=fallback_token_count,
            apply_profile=getattr(config, "apply_profile_multi_npc", False),
        )

    def _select_next(
        self,
        conversation_type: str,
        pool: Any,
        fallback_params: Dict[str, Any],
        fallback_token_count: int,
        apply_profile: bool
    ) -> LLMSelection | None:
        pool = self._normalize_pool(pool, conversation_type)
        if not pool:
            logging.debug(f"Sequential LLM pool is empty for {conversation_type}; skipping sequential selection")
            return None

        start_index = self._indices.get(conversation_type, 0) % len(pool)
        for offset in range(len(pool)):
            index = (start_index + offset) % len(pool)
            selected_llm = pool[index]
            if not isinstance(selected_llm, dict) or "service" not in selected_llm or "model" not in selected_llm:
                logging.error(f"Invalid LLM entry in sequential {conversation_type} pool: {selected_llm}")
                continue

            self._indices[conversation_type] = (index + 1) % len(pool)
            return self._selection_from_entry(
                selected_llm,
                conversation_type,
                fallback_params,
                fallback_token_count,
                apply_profile,
            )

        logging.warning(f"No valid LLM entries in sequential {conversation_type} pool")
        return None

    def _normalize_pool(self, pool: Any, conversation_type: str) -> List[Dict[str, Any]]:
        if isinstance(pool, str):
            try:
                pool = json.loads(pool)
            except json.JSONDecodeError as e:
                logging.error(f"Error parsing sequential {conversation_type} LLM pool JSON: {e}")
                return []
        if not isinstance(pool, list):
            logging.warning(f"Invalid sequential {conversation_type} LLM pool")
            return []
        return pool

    def _selection_from_entry(
        self,
        selected_llm: Dict[str, Any],
        conversation_type: str,
        fallback_params: Dict[str, Any],
        fallback_token_count: int,
        apply_profile: bool
    ) -> LLMSelection:
        service = selected_llm["service"]
        model = selected_llm["model"]
        profile_params = fallback_params if fallback_params else {}
        if apply_profile:
            profile_params = self.profile_manager.apply_profile_to_params(
                service=service,
                model=model,
                fallback_params=fallback_params,
                random_enabled=True
            )

        has_profile = apply_profile and self.profile_manager.has_profile(service, model)
        profile_status = "with profile" if has_profile else "without profile"
        logging.info(f"Sequentially selected LLM for {conversation_type}: {service}/{model} ({profile_status})")
        logging.info(f"Sequential LLM Parameters: {profile_params}")

        return LLMSelection(
            service=service,
            model=model,
            parameters=profile_params,
            token_count=fallback_token_count,
            from_profile=has_profile
        )
