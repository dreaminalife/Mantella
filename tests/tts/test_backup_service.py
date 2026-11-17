"""Who speaks a line when a voice service fails"""
from unittest.mock import MagicMock

import pytest
import soundfile as sf

import src.output_manager as output_manager
from src.character_manager import Character
from src.config.definitions.tts_definitions import TTSEnum
from src.games.equipment import Equipment
from src.llm.sentence_content import SentenceContent, SentenceTypeEnum
from src.output_manager import ChatManager
from src.tts.clip_service import ClipVoiceService
from src.tts.registry import TRAINED, VoiceServiceInfo, VoiceServiceRegistry
from src.tts.ttsable import TTSable
from tests.tts.fake_voice_server import tone_wav

spoken_by: list[str] = []


class FakeNewService(ClipVoiceService):
    failing: set[str] = set()

    def built_in_voice(self, gender):
        return "alba" if gender == 1 else "george"

    def speak(self, text, voice, out_path):
        spoken_by.append(self.info.name)
        if self.info.name in FakeNewService.failing:
            raise ConnectionError("internet down")
        with open(out_path, "wb") as f:
            f.write(tone_wav())


class FakeOldService(TTSable):
    """Like Piper, XTTS or xVASynth: not one of the newer services"""
    failing: set[str] = set()

    def __init__(self, config, name):
        super().__init__(config)
        self.name = name

    def change_voice(self, voice, in_game_voice=None, csv_in_game_voice=None, advanced_voice_model=None, voice_accent=None, voice_gender=None, voice_race=None):
        self._last_voice = voice

    def tts_synthesize(self, voiceline, final_voiceline_file, synth_options):
        spoken_by.append(self.name)
        if self.name in FakeOldService.failing:
            raise RuntimeError("program crashed")
        with open(final_voiceline_file, "wb") as f:
            f.write(tone_wav())


@pytest.fixture
def registry(monkeypatch):
    spoken_by.clear()
    FakeNewService.failing = set()
    FakeOldService.failing = set()
    registry = VoiceServiceRegistry()
    for name in ("Online", "Setup", "Backup"):
        registry.add_built_in(VoiceServiceInfo(name, factory=lambda config, game, info: FakeNewService(config, info)))
    registry.add_built_in(VoiceServiceInfo("Old", factory=lambda config, game, info: FakeOldService(config, "Old"), kind=TRAINED, legacy_enum=TTSEnum.XVASYNTH))
    monkeypatch.setattr(output_manager, "voice_services", registry)
    return registry


def manager(voice_config, registry, default: str, backup: str = "Backup") -> ChatManager:
    voice_config.tts_service_name = default
    voice_config.tts_backup_service_name = backup
    default_service = registry.create(registry.find(default), voice_config, None)
    return ChatManager(voice_config, default_service, MagicMock())


def lydia(tts_service: str = "") -> Character:
    return Character("000A2C94", "000A2C94", "Lydia", 1, "<Race: NordRace (00013746)>", False, "", False, False, 0, False,
                     "FemaleEvenToned", "Female Even Toned", "FemaleEvenToned", "", "", Equipment({}), {}, tts_service=tts_service)


def say(chat_manager: ChatManager, character: Character):
    return chat_manager.generate_sentence(SentenceContent(character, "As you wish, my Thane.", SentenceTypeEnum.SPEECH, False))


def test_a_working_service_speaks_once(voice_config, registry):
    sentence = say(manager(voice_config, registry, "Online"), lydia())
    assert spoken_by == ["Online"] and sentence.voice_file and not sentence.error_message
    assert sf.info(sentence.voice_file).channels == 1


def test_a_failing_newer_service_is_tried_twice_then_the_backup_speaks(voice_config, registry):
    FakeNewService.failing = {"Online"}
    sentence = say(manager(voice_config, registry, "Online"), lydia())
    assert spoken_by == ["Online", "Online", "Backup"] and sentence.voice_file and not sentence.error_message


def test_an_npcs_own_newer_service_goes_to_the_backup_not_the_tts_service(voice_config, registry):
    FakeNewService.failing = {"Setup"}
    say(manager(voice_config, registry, "Online"), lydia(tts_service="setup"))
    assert spoken_by == ["Setup", "Setup", "Backup"]


def test_an_npcs_own_old_service_goes_to_the_tts_service_as_before(voice_config, registry):
    FakeOldService.failing = {"Old"}
    FakeNewService.failing = {"Online"}
    sentence = say(manager(voice_config, registry, "Online"), lydia(tts_service="Old"))
    assert spoken_by == ["Old", "Online", "Online", "Backup"] and not sentence.error_message


def test_when_everything_fails_the_line_has_an_error(voice_config, registry):
    FakeNewService.failing = {"Online", "Backup"}
    sentence = say(manager(voice_config, registry, "Online"), lydia())
    assert spoken_by == ["Online", "Online", "Backup", "Backup"] and sentence.error_message


def test_a_backup_that_is_the_failing_service_is_not_tried_again(voice_config, registry):
    FakeNewService.failing = {"Online"}
    sentence = say(manager(voice_config, registry, "Online", backup="Online"), lydia())
    assert spoken_by == ["Online", "Online"] and sentence.error_message


def test_an_unknown_name_in_the_tts_service_column_uses_the_tts_service(voice_config, registry, caplog):
    say(manager(voice_config, registry, "Online"), lydia(tts_service="nobody"))
    assert spoken_by == ["Online"] and "Invalid TTS service 'nobody'" in caplog.text


def test_the_npc_speaks_with_their_voice_type_clip(voice_config, registry, tmp_path):
    import os
    os.makedirs(tmp_path / "voice_clips" / "voice_types")
    with open(tmp_path / "voice_clips" / "voice_types" / "FemaleEvenToned.wav", "wb") as f:
        f.write(tone_wav())
    (tmp_path / "voice_clips" / "voice_types" / "FemaleEvenToned.json").write_text("{}", encoding="utf-8") # what Mantella writes next to a clip
    chat_manager = manager(voice_config, registry, "Online")
    service = chat_manager.tts
    picks = []
    original = service.prepare_voice
    service.prepare_voice = lambda pick: picks.append(pick) or original(pick)
    say(chat_manager, lydia())
    assert picks[0].key == "femaleeventoned" and picks[0].clip_path.endswith("FemaleEvenToned.wav")


def test_stopping_stops_every_service_but_the_one_kept(voice_config, registry):
    chat_manager = manager(voice_config, registry, "Online")
    say(chat_manager, lydia(tts_service="setup")) # starts the NPC's own service too
    stopped = []
    for name in ("Online", "Setup"):
        instance = chat_manager._get_or_create_tts(registry.find(name))
        instance.stop = (lambda n=name: stopped.append(n))
    chat_manager.stop_voice_services(keep=chat_manager.tts)
    assert stopped == ["Setup"]
