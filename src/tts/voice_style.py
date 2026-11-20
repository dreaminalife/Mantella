"""How an online service's cloned voices sound: HeyGen's Similarity and Expressiveness, Inworld's Delivery, Boson AI's Expressiveness, and
each service's Settings Mantella doesn't have yet (fields sent with every line, written by the player).

These settings belong to the model picked for the service (Step 11): each model keeps its own values, because the same number can
mean something else on another model. HeyGen's Similarity too: switching to a model with another Similarity clones each voice again
before its next line. The Text-to-Speech page shows the picked model's values; the values of the other models are
kept in a hidden setting per service (<service>_model_settings). A model on Mantella's list says which settings it doesn't take.

Each voice can have its own value too, per model, on the Voice Clips part of the page (Customization per voice type and per NPC), kept in voice_settings.json next to
its Pitch. Settings follow the voice: NPCs who share a voice type share them, an NPC with a voice of their own has their own. A
service's built-in voice always uses the page's.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.tts.model_catalog import ServiceModel, ServiceModels, live_value
from src.tts.voice_picker import OWN_CLIP, VoicePick
from src.voice_clips.clip_store import NPCS, VOICE_TYPES, ClipStore

EXTRA_REQUEST_LABEL = "Settings Mantella doesn't have yet"


@dataclass(frozen=True)
class StyleSetting:
    name: str # the config.ini setting, and the voice's own value's name in voice_settings.json
    service: str # the voice service's name
    label: str
    default: Any
    choices: tuple[str, ...] = () # a dropdown's choices. Empty: a number from low to high
    low: float = 0.0
    high: float = 1.0
    json_object: bool = False # text that must be a JSON object: the Settings Mantella doesn't have yet
    per_voice: bool = True # can also be set for one voice under Customization per voice type and per NPC
    per_model: bool = True # each model keeps its own value. False: one for every model of the service

    def clean(self, value: Any) -> Any:
        """The value if it is one this setting takes, else None"""
        if self.json_object:
            text = str(value).strip() if isinstance(value, str) else None
            return text if text is not None and (text == "" or parsed_object(text) is not None) else None
        if self.choices:
            return value if isinstance(value, str) and value in self.choices else None
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return round(number, 2) if self.low <= number <= self.high else None


def parsed_object(text: str) -> dict | None:
    """The Settings Mantella doesn't have yet as fields, or None when the text isn't a JSON object"""
    try:
        found = json.loads(text)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def extra_request_setting(service: str, key: str) -> StyleSetting:
    """Shown in a fold named EXTRA_REQUEST_LABEL, so its own name doesn't repeat it"""
    return StyleSetting(f"{key}_extra_request", service, "Fields to send", "", json_object=True, per_voice=False)


HEYGEN_SIMILARITY = StyleSetting("heygen_similarity", "HeyGen", "Similarity", "Highest", ("Medium", "High", "Very high", "Highest"))
HEYGEN_EXPRESSIVENESS = StyleSetting("heygen_expressiveness", "HeyGen", "Expressiveness", 0.5)
INWORLD_DELIVERY = StyleSetting("inworld_delivery", "Inworld", "Delivery", "Balanced", ("Stable", "Balanced", "Creative"))
BOSON_EXPRESSIVENESS = StyleSetting("boson_expressiveness", "Boson AI", "Expressiveness", "Lively", ("Flat", "Normal", "Lively"))
EXTRA_REQUESTS = {models.service: extra_request_setting(models.service, models.key) for models in ServiceModels.all()}
STYLE_SETTINGS = (HEYGEN_SIMILARITY, HEYGEN_EXPRESSIVENESS, INWORLD_DELIVERY, BOSON_EXPRESSIVENESS, *EXTRA_REQUESTS.values())
BY_NAME = {setting.name: setting for setting in STYLE_SETTINGS}


def owner(pick: VoicePick) -> tuple[str, str]:
    """Where a voice's own settings are kept: an NPC's own voice, or a voice type's (stand-ins too)"""
    return (NPCS if pick.kind == OWN_CLIP else VOICE_TYPES), pick.key


class ServiceStyle:
    """One service's Global settings settings, for the model picked for it"""

    def __init__(self, config: Any, service: str) -> None:
        self.__config = config
        self.service = service
        self.models = ServiceModels.of(service) # None: a service without a Model setting (Pocket TTS, a voice setup)

    @property
    def model(self) -> ServiceModel | None:
        return self.models.picked(self.__config) if self.models is not None else None

    def all_settings(self) -> list[StyleSetting]:
        """Every one the service has, whichever model is picked"""
        return [s for s in STYLE_SETTINGS if s.service == self.service]

    def per_model_settings(self) -> list[StyleSetting]:
        """The ones each model keeps its own value of: kept while another model is picked"""
        return [s for s in self.all_settings() if s.per_model]

    def settings(self, per_voice: bool = False) -> list[StyleSetting]:
        """The ones the picked model takes. *per_voice*: only those a voice can have its own value of"""
        model = self.model
        return [s for s in self.all_settings() if (model is None or model.takes(s.name)) and (s.per_voice or not per_voice)]

    def page_value(self, setting: StyleSetting) -> Any:
        """The setting on the Text-to-Speech page for the model the service speaks with (the saved value when the page isn't open).
        While the page shows another model's settings, the speaking model's are the kept ones"""
        model = self.model
        if model is not None and setting.per_model:
            stored = self.__stored()
            if stored.get("current") and stored["current"] != model.id:
                return self.__kept(stored, setting, model)
        return self.__shown_value(setting, model)

    def __shown_value(self, setting: StyleSetting, model: ServiceModel | None) -> Any:
        """The setting's control on the page: it holds the shown model's value"""
        cleaned = setting.clean(live_value(self.__config, setting.name, None))
        return self.default(setting, model) if cleaned is None else cleaned

    def __kept(self, stored: dict[str, Any], setting: StyleSetting, model: ServiceModel) -> Any:
        """A model's value kept while the page shows another model, or where it starts"""
        kept = stored["models"].get(model.id, {})
        value = setting.clean(kept.get(setting.name)) if isinstance(kept, dict) else None
        return self.default(setting, model) if value is None else value

    def shown_model(self) -> ServiceModel | None:
        """The model whose settings the page shows: the one picked next to Settings for, at first the one the service speaks with"""
        if self.models is None:
            return None
        current = self.__stored().get("current")
        return self.models.resolve(current) if current else self.model

    def default(self, setting: StyleSetting, model: ServiceModel | None = None) -> Any:
        """Where a model starts: what Mantella's list gives it, else the setting's own"""
        model = model or self.model
        found = setting.clean(model.default_for(setting.name, setting.default)) if model is not None and setting.per_model else None
        return setting.default if found is None else found

    def voice_own(self, clips: ClipStore, kind: str, key: str, setting: StyleSetting) -> Any:
        """The voice's own value for the picked model. None: it uses the page's"""
        return setting.clean(clips.voice_setting(kind, key, setting.name, self.__model_id(setting)))

    def set_voice_own(self, clips: ClipStore, kind: str, key: str, setting: StyleSetting, value: Any) -> None:
        """None takes the voice's own value for the picked model away"""
        clips.set_voice_setting(kind, key, setting.name, value, self.__model_id(setting))

    def __model_id(self, setting: StyleSetting) -> str | None:
        """Which model a voice's own value is kept for. None: one value for every model"""
        model = self.model
        return model.id if model is not None and setting.per_model else None

    def voice_value(self, clips: ClipStore, pick: VoicePick | None, setting: StyleSetting) -> Any:
        """The setting for a line in this voice: the voice's own value if it has one, else the page's"""
        if pick is not None and pick.clip_path and setting.per_voice:
            own = self.voice_own(clips, *owner(pick), setting)
            if own is not None:
                return own
        return self.page_value(setting)

    def extra_request(self) -> dict:
        """The picked model's Settings Mantella doesn't have yet, as fields: empty when there are none, or the model skips them"""
        setting = EXTRA_REQUESTS.get(self.service)
        if setting is None or setting not in self.settings():
            return {}
        return parsed_object(self.page_value(setting) or "{}") or {}

    # ---- each model's own values on the page ----

    def switch_model(self, new_value: Any, clips: ClipStore | None = None) -> dict[str, Any]:
        """The service speaks with another model, and the page shows its settings. Returns the values shown now, by setting name"""
        if self.models is None:
            return {}
        model_setting = self.__definition(self.models.setting)
        if model_setting is not None and model_setting.value != new_value and new_value:
            model_setting.value = str(new_value)
        return self.show_model(new_value, clips)

    def show_model(self, new_value: Any, clips: ClipStore | None = None) -> dict[str, Any]:
        """The page shows another model's settings, to change them without speaking with it: the old shown model's values are kept,
        and the new model's (or where it starts) are shown. Returns the values shown now, by setting name"""
        if self.models is None:
            return {}
        new = self.models.resolve(new_value)
        stored = self.__stored()
        old_id = stored.get("current")
        per_model = self.per_model_settings()
        if old_id == new.id:
            return {s.name: self.__shown_value(s, new) for s in per_model}
        if old_id:
            old = self.models.resolve(old_id)
            stored["models"][old_id] = {s.name: self.__shown_value(s, old) for s in per_model}
            if clips is not None:
                clips.pin_plain_values([s.name for s in per_model], old_id) # voices' own values from before Step 11
        kept = stored["models"].get(new.id, {})
        values: dict[str, Any] = {}
        for setting in per_model:
            value = setting.clean(kept.get(setting.name)) if isinstance(kept, dict) else None
            values[setting.name] = self.default(setting, new) if value is None else value
            definition = self.__definition(setting.name)
            if definition is not None and definition.value != values[setting.name]:
                definition.value = values[setting.name]
        stored["current"] = new.id
        self.__store(stored)
        return values

    def sync(self, clips: ClipStore | None = None) -> bool:
        """When Mantella starts: the values on the page belong to the picked model. A Model changed in config.ini by hand switches
        to that model's values. True when something changed"""
        if self.models is None:
            return False
        stored = self.__stored()
        picked = self.models.picked(self.__config)
        if not stored.get("current"): # the first start with Step 11: the page's values are the picked model's
            stored["current"] = picked.id
            self.__store(stored)
            return True
        if stored["current"] != picked.id:
            self.switch_model(self.models.value(self.__config) or picked.id, clips)
            return True
        return False

    def __definition(self, name: str):
        try:
            return self.__config.definitions.get_config_value_definition(name)
        except Exception:
            return None

    def __stored(self) -> dict[str, Any]:
        found = None
        try:
            found = json.loads(str(live_value(self.__config, self.models.stored_setting, "") or ""))
        except ValueError:
            pass
        if not isinstance(found, dict):
            found = {}
        if not isinstance(found.get("models"), dict):
            found["models"] = {}
        return found

    def __store(self, stored: dict[str, Any]) -> None:
        definition = self.__definition(self.models.stored_setting)
        if definition is not None:
            definition.value = json.dumps(stored, separators=(",", ":"))


def shared_values(values: Any) -> dict[str, Any]:
    """A voice's settings from a friend's voices file: the style settings Mantella knows, with values they take. A value is either
    for every model (an older file) or by model"""
    if not isinstance(values, dict):
        return {}
    found: dict[str, Any] = {}
    for name, value in values.items():
        setting = BY_NAME.get(name)
        if setting is None or not setting.per_voice:
            continue
        if isinstance(value, dict):
            if not setting.per_model:
                continue
            by_model = {str(model): cleaned for model, v in value.items() if (cleaned := setting.clean(v)) is not None}
            if by_model:
                found[name] = by_model
        elif (cleaned := setting.clean(value)) is not None:
            found[name] = cleaned
    return found
