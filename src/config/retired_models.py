"""AI models that were Mantella's defaults but are no longer offered. A player whose settings still name one gets the current free default
the next time Mantella starts, instead of NPCs that can't answer"""
from __future__ import annotations

CURRENT_FREE_MODEL = "google/gemma-4-26b-a4b-it:free"

# Old defaults OpenRouter took down (checked November 2026): the old Single NPC Model and the old Custom Vision Model
RETIRED_MODELS = (
    "google/gemma-2-9b-it:free",
    "meta-llama/llama-3.2-11b-vision-instruct:free",
)


def replace_retired_models(value: str) -> tuple[str, list[str]]:
    """*value* with every retired model swapped for the current free default, and the retired models it named (empty when none).
    Works on a single model and on a setting that lists several"""
    found = [model for model in RETIRED_MODELS if model in value]
    for model in found:
        value = value.replace(model, CURRENT_FREE_MODEL)
    return value, found
