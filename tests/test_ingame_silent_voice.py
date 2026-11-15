"""The in-game tests voice the NPCs' lines with a moment of silence (tests/ingame/silent_voice.py) so they run fast. These check, without the
game, that MantellaSoftware's own XTTS code accepts it with the settings the tests write, so a run isn't wasted on a voice error"""
import os

from src.config.config_loader import ConfigLoader
from src.config.definitions.tts_definitions import TTSEnum
from src.tts.synthesization_options import SynthesizationOptions
from src.tts.xtts import XTTS
from src import utils
from tests.ingame import silent_voice
from tests.ingame.talk_settings.talk_settings_ingame import write_settings


def test_mantellas_xtts_voices_a_line_with_the_silent_voice(tmp_path, monkeypatch):
    monkeypatch.setenv("TMP", str(tmp_path)) # Where MantellaSoftware writes the voiced lines
    server = silent_voice.start_silent_voice(0) # Port 0: any free port
    try:
        port = server.server_address[1]
        ConfigLoader(str(tmp_path)) # Writes a config.ini with every setting
        assert write_settings(os.path.join(tmp_path, "config.ini"), silent_voice.voice_settings(port)) == []
        config = ConfigLoader(str(tmp_path))
        assert config.tts_service == TTSEnum.XTTS and config.lip_generation == "disabled" and not config.fast_response_mode

        tts = XTTS(config, None)
        wav = tts.synthesize("FemaleNord", "Right behind you.", "FemaleNord", "FemaleNord", "", SynthesizationOptions(False, True))
        assert abs(utils.get_audio_duration(wav) - silent_voice.SILENCE_SECONDS) < 0.01
    finally:
        server.shutdown()


def test_the_silent_voice_lists_the_voices_of_the_bannered_mare():
    names = silent_voice.voice_names()
    for voice in ("femalenord", "maleeventoned", "femaleeventoned", "malenord"):
        assert voice in names
