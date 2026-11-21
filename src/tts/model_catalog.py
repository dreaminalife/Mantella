"""Mantella's list of the voice models each online service speaks with, the player's changes to it, and the model picked for each
service (Step 11).

The list is a file Mantella ships, data/voice_models.json. For each model it says its name, price, a note, which of Mantella's
settings it doesn't take (skips), its defaults, and a few facts the services' code asks (sound symbols, built-in voices, clip lengths).
Only text-to-speech models that speak with cloned voices go in.

The player adds models to a service's list and removes models from it on the Text-to-Speech page (ListChanges, kept in config.ini).
A model that isn't on Mantella's list is used as typed, with every setting the service has.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Mapping

from src import utils

FILE_NAME = "voice_models.json"
MALE, FEMALE = "male", "female"


def live_value(config: Any, name: str, fallback: Any = None) -> Any:
    """A setting as it is on the page right now (the definition), else the loaded value, else *fallback*"""
    try:
        return config.definitions.get_config_value_definition(name).value
    except Exception:
        return getattr(config, name, fallback)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


@dataclass(frozen=True)
class ServiceModel:
    """One model a service speaks with"""
    id: str
    name: str
    price: float | None = None # per million characters. None: not known
    note: str = "" # shown instead of the price
    ends: date | None = None # after this day the model isn't listed any more
    skips: tuple[str, ...] = () # the settings (config.ini names) it doesn't take
    defaults: Mapping[str, Any] = field(default_factory=dict) # setting name -> its value at first
    sound_symbols: bool = False # reads sound symbols (/ˈdoʊvɑːkiːn/) in a cloned voice
    voices: Mapping[str, str] = field(default_factory=dict) # its own built-in voices (male, female), for a service whose voices go with the model
    clip_seconds: float | None = None # how much speech it clones from, when it isn't the service's usual
    fill_seconds: float | None = None
    known: bool = True # False: typed in by the player, not on Mantella's list

    def takes(self, setting_name: str) -> bool:
        return setting_name not in self.skips

    def default_for(self, setting_name: str, fallback: Any) -> Any:
        return self.defaults.get(setting_name, fallback)

    def ended(self, today: date | None = None) -> bool:
        return self.ends is not None and (today or date.today()) > self.ends

    @classmethod
    def unknown(cls, model_id: str) -> "ServiceModel":
        """A model typed in that isn't on the list: used as typed, with every setting"""
        return cls(id=model_id, name=model_id, known=False)

    @classmethod
    def from_file(cls, data: Any) -> "ServiceModel | None":
        """One model from the file, or None when it has no ID. Fields Mantella doesn't know are left out"""
        if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"].strip():
            return None
        skips = data.get("skips") if isinstance(data.get("skips"), list) else []
        defaults = data.get("defaults") if isinstance(data.get("defaults"), dict) else {}
        voices = data.get("voices") if isinstance(data.get("voices"), dict) else {}
        model_id = data["id"].strip()
        return cls(id=model_id, name=str(data.get("name") or model_id), price=_number(data.get("price")), note=str(data.get("note") or ""),
                   ends=_date(data.get("ends")), skips=tuple(str(s) for s in skips), defaults=dict(defaults),
                   sound_symbols=data.get("sound_symbols") is True, voices={str(k): str(v) for k, v in voices.items() if v},
                   clip_seconds=_number(data.get("clip_seconds")), fill_seconds=_number(data.get("fill_seconds")))


@dataclass(frozen=True)
class ServiceList:
    """One service's models, from the file"""
    default: str
    models: tuple[ServiceModel, ...]
    clones_per_model: bool = False # a cloned voice only works with the model it was cloned for (Qwen)

    @classmethod
    def from_file(cls, data: Any) -> "ServiceList | None":
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            return None
        models = tuple(m for m in (ServiceModel.from_file(d) for d in data["models"]) if m is not None)
        if not models:
            return None
        default = str(data.get("default") or "")
        if default not in {m.id for m in models}:
            default = models[0].id
        return cls(default, models, data.get("clones_per_model") is True)


@dataclass(frozen=True)
class CatalogCopy:
    """The file's services"""
    services: Mapping[str, ServiceList]

    @classmethod
    def parse(cls, text: str) -> "CatalogCopy | None":
        """None when it isn't a list Mantella can use"""
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
            return None
        services = {str(name): found for name, raw in data["services"].items() if (found := ServiceList.from_file(raw)) is not None}
        return cls(services) if services else None

    @classmethod
    def read(cls, path: str) -> "CatalogCopy | None":
        try:
            with open(path, encoding="utf-8") as file:
                return cls.parse(file.read())
        except OSError:
            return None


class ModelCatalog:
    """Mantella's list of models: the file it ships"""
    _shared: "ModelCatalog | None" = None
    _shared_lock = threading.Lock()

    def __init__(self, path: str | None = None) -> None:
        self.__copy = CatalogCopy.read(path or os.path.join(utils.resolve_path(), "data", FILE_NAME))
        if self.__copy is None:
            logging.warning(f"Mantella's list of voice models ({FILE_NAME}) is missing or broken, so only models you add can be used.")

    @classmethod
    def shared(cls) -> "ModelCatalog":
        with cls._shared_lock:
            if cls._shared is None:
                cls._shared = cls()
            return cls._shared

    def service(self, name: str) -> ServiceList | None:
        return self.__copy.services.get(name) if self.__copy else None

    def models(self, service: str, today: date | None = None) -> list[ServiceModel]:
        """The models to offer, in the file's order. A model past its end date isn't offered"""
        found = self.service(service)
        return [m for m in found.models if not m.ended(today)] if found else []

    def find(self, service: str, value: str) -> ServiceModel | None:
        """A model on the list by its ID, or by its name (what older versions of Mantella saved). Ended ones too"""
        found = self.service(service)
        if found is None or not value:
            return None
        wanted = value.strip()
        return next((m for m in found.models if m.id == wanted), None) or next((m for m in found.models if m.name.lower() == wanted.lower()), None)


@dataclass(frozen=True)
class ListChanges:
    """The player's changes to one service's list of models: the models they added, and Mantella's models they removed, by ID"""
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: Any) -> "ListChanges":
        try:
            data = json.loads(str(text or ""))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return cls()
        ids = lambda name: tuple(str(i) for i in data.get(name, []) if str(i).strip()) if isinstance(data.get(name), list) else ()
        return cls(ids("added"), ids("removed"))

    def text(self) -> str:
        return json.dumps({"added": list(self.added), "removed": list(self.removed)}, separators=(",", ":")) if self.added or self.removed else ""


class ServiceModels:
    """One online service's Model setting: which model is picked, and the list to pick from"""
    _by_service: dict[str, "ServiceModels"] = {}

    def __init__(self, service: str, key: str, fallback: str) -> None:
        self.service = service # the service's name, as on the page and in the file
        self.key = key # the start of its settings' names in config.ini, e.g. heygen
        self.fallback = fallback # its model when the list has none for it
        ServiceModels._by_service[service] = self

    @classmethod
    def of(cls, service: str) -> "ServiceModels | None":
        return cls._by_service.get(service)

    @classmethod
    def all(cls) -> list["ServiceModels"]:
        return list(cls._by_service.values())

    @property
    def setting(self) -> str:
        """The Model setting in config.ini"""
        return f"{self.key}_model"

    @property
    def stored_setting(self) -> str:
        """The hidden setting that keeps each model's own values of the Global settings settings (voice_style.ServiceStyle)"""
        return f"{self.key}_model_settings"

    @property
    def catalog(self) -> ModelCatalog:
        return ModelCatalog.shared()

    @property
    def clones_per_model(self) -> bool:
        found = self.catalog.service(self.service)
        return bool(found and found.clones_per_model)

    def default(self) -> ServiceModel:
        found = self.catalog.service(self.service)
        model = self.catalog.find(self.service, found.default) if found else None
        return model or ServiceModel(self.fallback, self.fallback)

    def resolve(self, value: Any) -> ServiceModel:
        """A Model setting's value as a model: one on the list (by ID or name), else the ID as typed. Empty: the default"""
        value = str(value or "").strip()
        if not value:
            return self.default()
        return self.catalog.find(self.service, value) or ServiceModel.unknown(value)

    def value(self, config: Any) -> str:
        return str(live_value(config, self.setting, "") or "")

    def picked(self, config: Any) -> ServiceModel:
        """The model picked now: on the page if it is open, else as loaded"""
        return self.resolve(self.value(config))

    def switched_on(self, config: Any, setting_name: str) -> bool:
        """A service-wide switch (Audio tags, Directions) that is on, on a model that takes it. A model that skips it hides the
        switch and acts as if it's off, keeping what is saved"""
        return bool(getattr(config, setting_name, False)) and self.picked(config).takes(setting_name)

    # ---- the list to pick from: Mantella's, with the player's changes ----

    @property
    def list_setting(self) -> str:
        """The hidden setting that keeps the player's changes to the list (ListChanges)"""
        return f"{self.key}_model_list"

    def changes(self, config: Any) -> ListChanges:
        return ListChanges.parse(live_value(config, self.list_setting, "")) if config is not None else ListChanges()

    def offered(self, config: Any = None) -> list[ServiceModel]:
        """The models to pick from: Mantella's (not the ones the player removed), then the ones the player added"""
        changes = self.changes(config)
        found = [m for m in self.catalog.models(self.service) if m.id not in changes.removed]
        ids = {m.id for m in found}
        return found + [self.resolve(i) for i in changes.added if i not in ids]

    def in_list(self, config: Any, model_id: str) -> bool:
        return any(m.id == model_id for m in self.offered(config))

    def add(self, config: Any, value: str) -> ServiceModel | None:
        """Adds a model by its ID (or the name of one of Mantella's models) and returns it. None when it's empty or already listed"""
        if not str(value or "").strip():
            return None
        model = self.resolve(value)
        if self.in_list(config, model.id):
            return None
        changes = self.changes(config)
        if model.id in changes.removed: # one of Mantella's, removed before
            self.__save(config, replace(changes, removed=tuple(i for i in changes.removed if i != model.id)))
        else:
            self.__save(config, replace(changes, added=(*changes.added, model.id)))
        return model

    def remove(self, config: Any, model_id: str) -> bool:
        """Takes a model off the list. Its settings are kept, for when it's added again. False when it isn't listed, or is the last one"""
        offered = self.offered(config)
        if len(offered) <= 1 or all(m.id != model_id for m in offered):
            return False
        changes = self.changes(config)
        if model_id in changes.added:
            self.__save(config, replace(changes, added=tuple(i for i in changes.added if i != model_id)))
        else:
            self.__save(config, replace(changes, removed=(*changes.removed, model_id)))
        return True

    def put_back(self, config: Any) -> None:
        """Mantella's models the player removed are listed again"""
        self.__save(config, replace(self.changes(config), removed=()))

    def first_offered(self, config: Any) -> ServiceModel:
        """The model to speak with when the picked one is removed: the default, else the first on the list"""
        offered = self.offered(config)
        default = self.default()
        return default if not offered or any(m.id == default.id for m in offered) else offered[0]

    def __save(self, config: Any, changes: ListChanges) -> None:
        config.definitions.get_config_value_definition(self.list_setting).value = changes.text()
        self.refresh_options(config.definitions.get_config_value_definition(self.setting), config)

    # ---- the Model setting ----

    def options(self, config: Any = None) -> tuple[list[str], dict[str, str]]:
        """The dropdown's choices: model IDs, shown by name. Ended models have a name too, so one still picked shows as Turbo Free"""
        found = self.catalog.service(self.service)
        return [m.id for m in self.offered(config)], {m.id: m.name for m in (found.models if found else ())}

    def choices(self, current: Any = None, config: Any = None) -> list[tuple[str, str]]:
        """(shown name, saved ID) for the dropdown, with the picked model too when it isn't offered any more (ended)"""
        options, labels = self.options(config)
        current = str(current or "").strip()
        if current and current not in options and current in labels:
            options = [*options, current]
        return [(labels.get(o, o), o) for o in options]

    def config_value(self, description: str):
        """The Model setting: the models on the list, and any model ID typed in"""
        from src.config.types.config_value_selection import ConfigValueSelection
        options, labels = self.options()
        return ConfigValueSelection(self.setting, "Model", description, self.default().id, options, allows_free_edit=True,
                                    option_labels=labels).in_folded_part(self.service)

    def refresh_options(self, selection, config: Any = None) -> None:
        """The dropdown offers the list with the player's changes"""
        options, labels = self.options(config)
        selection.set_options(options, option_labels=labels)

    def with_limits(self, config: Any, info):
        """The service as the picked model clones: its own clip lengths when the list gives them"""
        model = self.picked(config)
        changes = {}
        if model.clip_seconds:
            changes["clip_seconds"] = model.clip_seconds
        if model.fill_seconds:
            changes["fill_seconds"] = model.fill_seconds
        return replace(info, **changes) if changes else info


ELEVENLABS_MODELS = ServiceModels("ElevenLabs", "elevenlabs", "eleven_v4_turbo")
INWORLD_MODELS = ServiceModels("Inworld", "inworld", "inworld-tts-2")
HEYGEN_MODELS = ServiceModels("HeyGen", "heygen", "heygen-voice-1-turbo")
QWEN_MODELS = ServiceModels("Qwen", "qwen", "qwen-audio-3.1-tts-plus")
BOSON_MODELS = ServiceModels("Boson AI", "boson", "higgs-tts-3")
IODINE_MODELS = ServiceModels("Iodine", "iodine", "v2")


def with_model_limits(config: Any, info):
    """A service's info with the picked model's clip lengths. Services without a Model setting are as they are"""
    if info is None:
        return None
    models = ServiceModels.of(info.name)
    return models.with_limits(config, info) if models is not None else info
