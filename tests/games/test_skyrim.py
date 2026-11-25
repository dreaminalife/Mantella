from src.games.skyrim import Skyrim
import pytest
from src.config.config_loader import ConfigLoader
from src.llm.sentence import Sentence
from src.llm.sentence_content import SentenceContent
from src.llm.sentence_content import SentenceTypeEnum
from src.games.external_character_info import external_character_info
from src.character_manager import Character
import shutil
import os
import json
import pandas as pd


@pytest.fixture
def skyrim_test_config(tmp_path, default_config: ConfigLoader) -> ConfigLoader:
    """Configures default_config to use tmp_path for override lookups"""
    # Change folders where character overrides are searched in
    default_config.mod_path_base = str(tmp_path)
    default_config.save_folder = str(tmp_path)

    return default_config


def test_skyrim_properties(skyrim: Skyrim):
    """Test the basic properties of the skyrim class"""
    assert skyrim.extender_name == 'SKSE'
    assert skyrim.game_name_in_filepath == 'skyrim'


def test_apply_character_overrides_json_add(tmp_path, skyrim_test_config: ConfigLoader):
    """Test adding a new character via a JSON override file"""
    new_char_data = {
        "name": "Test JSON Character", "base_id": "JSON001", "race": "Nord",
        "bio": "A character added via JSON override.", "voice_model": "TestVoice",
        "skyrim_voice_folder": "TestVoiceFolder", "advanced_voice_model": "",
        "voice_accent": "en"
    }

    override_dir = tmp_path / "data" / "skyrim" / "character_overrides"
    override_dir.mkdir(parents=True, exist_ok=True)
    override_file = override_dir / "test_json_add.json"

    with open(override_file, 'w') as f:
        json.dump(new_char_data, f)

    skyrim = Skyrim(skyrim_test_config)

    # Check if the new character exists in the dataframe
    df = skyrim.character_df
    match = df[(df['name'] == "Test JSON Character") & (df['base_id'] == "JSON001")]

    assert not match.empty
    assert len(match) == 1
    assert match.iloc[0]['bio'] == "A character added via JSON override."
    assert match.iloc[0]['voice_model'] == "TestVoice"
    assert match.iloc[0]['skyrim_voice_folder'] == "TestVoiceFolder"
    assert match.iloc[0]['advanced_voice_model'] == ""
    assert match.iloc[0]['voice_accent'] == "en"


def test_apply_character_overrides_json_modify(tmp_path, skyrim_test_config: ConfigLoader):
    """Test modifying an existing character via a JSON override file"""
    override_data = {
        "name": "Lydia", "base_id": "0A2C8E", "race": "Nord",
        "bio": "This is the overridden bio for Lydia.",
        "voice_model": "OverriddenLydiaVoice"
    }

    override_dir = tmp_path / "data" / "skyrim" / "character_overrides"
    override_dir.mkdir(parents=True, exist_ok=True)
    override_file = override_dir / "test_json_modify.json"

    with open(override_file, 'w') as f:
        json.dump(override_data, f)

    skyrim = Skyrim(skyrim_test_config)

    # Check if character's data was modified
    df = skyrim.character_df
    match = df.loc[df['base_id'] == "0A2C8E"]

    assert not match.empty
    assert len(match) == 1
    assert match.iloc[0]['bio'] == "This is the overridden bio for Lydia."
    assert match.iloc[0]['voice_model'] == "OverriddenLydiaVoice"
    assert match.iloc[0]['skyrim_voice_folder'] == "FemaleEvenToned" # Check unmodified field


def test_apply_character_overrides_csv_add(tmp_path, skyrim_test_config: ConfigLoader):
    """Test adding a new character via a CSV override file"""
    csv_header = "name,base_id,race,bio,voice_model,skyrim_voice_folder,advanced_voice_model,voice_accent"
    csv_data = "Test CSV Character,CSV001,Imperial,Added via CSV,TestCSVVoice,TestCSVFolder,,en"
    csv_content = f"{csv_header}\n{csv_data}"

    override_dir = tmp_path / "SKSE" / "Plugins" / "MantellaSoftware" / "data" / "skyrim" / "character_overrides"
    override_dir.mkdir(parents=True, exist_ok=True)
    override_file = override_dir / "test_csv_add.csv"

    with open(override_file, 'w') as f:
        f.write(csv_content)

    skyrim = Skyrim(skyrim_test_config)

    # Check if the new character exists
    df = skyrim.character_df
    match = df[(df['name'] == "Test CSV Character") & (df['base_id'] == "CSV001")]

    assert not match.empty
    assert len(match) == 1
    assert match.iloc[0]['bio'] == "Added via CSV"
    assert match.iloc[0]['voice_model'] == "TestCSVVoice"
    assert match.iloc[0]['race'] == "Imperial"


def test_apply_character_overrides_csv_modify(tmp_path, skyrim_test_config: ConfigLoader):
    """Test modifying an existing character via a CSV override file"""
    csv_header = "name,base_id,race,bio,voice_model,skyrim_voice_folder,advanced_voice_model,voice_accent"
    csv_data = "Lydia,0A2C8E,Nord,Overridden Bio,OverriddenLydiaVoice,FemaleEvenToned,,en"
    csv_content = f"{csv_header}\n{csv_data}"

    override_dir = tmp_path / "SKSE" / "Plugins" / "MantellaSoftware" / "data" / "skyrim" / "character_overrides"
    override_dir.mkdir(parents=True, exist_ok=True)
    override_file = override_dir / "test_csv_modify.csv"

    with open(override_file, 'w') as f:
        f.write(csv_content)

    skyrim = Skyrim(skyrim_test_config)

    # Check if character's data was modified
    df = skyrim.character_df
    match = df.loc[df['base_id'] == "0A2C8E"]

    assert not match.empty
    assert len(match) == 1
    assert match.iloc[0]['bio'] == "Overridden Bio"
    assert match.iloc[0]['voice_model'] == "OverriddenLydiaVoice"
    assert match.iloc[0]['skyrim_voice_folder'] == "FemaleEvenToned"


def test_modify_sentence_text_for_game(skyrim: Skyrim):
    """Test text modification for Skyrim's character limit"""
    # Test with short text (under limit)
    short_text = "This is a short sentence."
    assert skyrim.modify_sentence_text_for_game(short_text) == short_text
    
    # Test with text exactly at the limit
    exact_text = "x" * 500
    assert skyrim.modify_sentence_text_for_game(exact_text) == exact_text
    
    # Test with text over the limit
    long_text = "x" * 600
    expected = ("x" * 497) + "..."
    assert skyrim.modify_sentence_text_for_game(long_text) == expected
    assert len(skyrim.modify_sentence_text_for_game(long_text)) == 500


def test_is_sentence_allowed(skyrim: Skyrim):
    """Test sentence filtering logic"""
    # 'assist' keyword should be filtered out if not first sentence
    assert skyrim.is_sentence_allowed("I will assist you with that.", 1) == False
    assert skyrim.is_sentence_allowed("I will assist you with that.", 0) == True
    
    # Normal sentences should be allowed
    assert skyrim.is_sentence_allowed("Hello there!", 0) == True
    assert skyrim.is_sentence_allowed("How are you today?", 1) == True


def test_find_best_voice_model(skyrim: Skyrim):
    """Test voice model selection logic"""
    # Test with a Nord male
    nord_voice = skyrim.find_best_voice_model(actor_race='[Race <NordRace (00013746)>]', actor_sex=0, ingame_voice_model='MaleNord')
    assert nord_voice == "MaleNord"
    
    # Test with a High Elf female
    high_elf_voice = skyrim.find_best_voice_model(actor_race="[Race <HighElfRace (12345678)>]", actor_sex=1, ingame_voice_model="FemaleElfHaughty")
    assert high_elf_voice == "FemaleElfHaughty"
    
    # Test with unknown race (should default to Nord)
    unknown_voice = skyrim.find_best_voice_model(actor_race="Unknown Race", actor_sex=0, ingame_voice_model="Unknown")
    assert unknown_voice == "Male Nord" # Default model names have spaces for readability, but are parsed without spaces


def _put_first_row(skyrim: Skyrim, **values):
    """A skyrim_characters.csv row before all the others, so it is the first one a lookup finds"""
    row = pd.DataFrame([{column: values.get(column, float('nan')) for column in skyrim.character_df.columns}])
    skyrim._Gameable__character_df = pd.concat([row, skyrim.character_df], ignore_index=True)


def test_find_best_voice_model_skips_a_row_without_a_voice_model(skyrim: Skyrim):
    """Like Dremora Merchant: the first row with the NPC's voice folder has no voice model. The next row's is used, not an empty one"""
    _put_first_row(skyrim, name="Test Merchant", skyrim_voice_folder="DLC2MaleDarkElfCynical")
    _put_first_row(skyrim, name="Test Elder", voice_model="MaleDarkElfCynical", skyrim_voice_folder="DLC2MaleDarkElfCynical")
    _put_first_row(skyrim, name="Test Merchant Again", skyrim_voice_folder="DLC2MaleDarkElfCynical")

    voice = skyrim.find_best_voice_model(actor_race="[Race <DarkElfRace (00013746)>]", actor_sex=0, ingame_voice_model="VoiceType <DLC2MaleDarkElfCynical (0201A3E3)>")

    assert voice == "MaleDarkElfCynical"


def test_find_best_voice_model_uses_the_race_when_no_row_has_a_voice_model(skyrim: Skyrim):
    _put_first_row(skyrim, name="Test Merchant", skyrim_voice_folder="TestOnlyVoiceFolder")

    voice = skyrim.find_best_voice_model(actor_race="[Race <DarkElfRace (00013746)>]", actor_sex=0, ingame_voice_model="VoiceType <TestOnlyVoiceFolder (0201A3E3)>")

    assert isinstance(voice, str) and voice # a voice from the race list, not an empty cell


def test_generic_npc_voice_folder_skips_a_row_without_one(skyrim: Skyrim):
    """Like Mephala: the first row with the voice model has no voice folder. The next row's is used, not an empty one"""
    _put_first_row(skyrim, name="Test Guard", voice_model="TestOnlyVoice", skyrim_voice_folder="TestOnlyFolder")
    _put_first_row(skyrim, name="Test Daedra", voice_model="TestOnlyVoice")

    info = skyrim.load_unnamed_npc("Test Guard", "DarkElf", 0, "VoiceType <TestOnlyFolder (0201A3E3)>")

    assert info["voice_model"] == "TestOnlyVoice"
    assert info["skyrim_voice_folder"] == "TestOnlyFolder"


def test_dictionary_match(skyrim: Skyrim):
    """Test the dictionary_match method for voice model mapping"""
    # Test with valid race/sex combinations
    assert skyrim.dictionary_match(
        female_voice_model_dictionary=skyrim.FEMALE_VOICE_MODELS_XVASYNTH, 
        male_voice_model_dictionary=skyrim.MALE_VOICE_MODELS_XVASYNTH, 
        actor_race="Imperial", 
        actor_sex=0
    ) == "Male Even Toned"
    
    assert skyrim.dictionary_match(
        female_voice_model_dictionary=skyrim.FEMALE_VOICE_MODELS_XVASYNTH, 
        male_voice_model_dictionary=skyrim.MALE_VOICE_MODELS_XVASYNTH, 
        actor_race="Khajiit", 
        actor_sex=1
    ) == "Female Khajiit"
    
    # Test with invalid race (should default to Nord)
    assert skyrim.dictionary_match(
        female_voice_model_dictionary=skyrim.FEMALE_VOICE_MODELS_XVASYNTH, 
        male_voice_model_dictionary=skyrim.MALE_VOICE_MODELS_XVASYNTH, 
        actor_race="Unknown", 
        actor_sex=1
    ) == "Female Nord"
    
    # Test with None values (should default to Nord)
    assert skyrim.dictionary_match(
        female_voice_model_dictionary=skyrim.FEMALE_VOICE_MODELS_XVASYNTH, 
        male_voice_model_dictionary=skyrim.MALE_VOICE_MODELS_XVASYNTH, 
        actor_race=None, 
        actor_sex=None
    ) == "Male Nord"


def test_get_weather_description(skyrim: Skyrim):
    """Test weather description retrieval"""
    # Test with valid weather ID
    weather_attrs = {skyrim.KEY_CONTEXT_WEATHER_ID: "1113848"}
    assert skyrim.get_weather_description(weather_attrs) == "In Sovngarde, the sky is choked with ominous clouds, swirling as if the heavens themselves are brooding, casting the land in a perpetual twilight."
    
    # Test with valid weather classification
    weather_attrs = {skyrim.KEY_CONTEXT_WEATHER_CLASSIFICATION: 0}
    assert skyrim.get_weather_description(weather_attrs) == skyrim.WEATHER_CLASSIFICATIONS[0]
    
    # Test with empty attributes
    weather_attrs = {}
    assert skyrim.get_weather_description(weather_attrs) == ""


def test_load_external_character_info(skyrim: Skyrim):
    """Test loading external character info"""
    # Test with a character that should be found in the CSV
    info = skyrim.load_external_character_info(
        base_id='0A2C8E', 
        name='Lydia', 
        race='[Race <NordRace (00013746)>]', 
        gender=1, 
        ingame_voice_model='[VoiceType <FemaleEvenToned (00013ADD)>]'
    )
    assert isinstance(info, external_character_info)
    assert info.name == "Lydia"
    assert info.is_generic_npc == False
    assert info.ingame_voice_model == "FemaleEvenToned"
    assert info.tts_voice_model == "FemaleEvenToned"
    assert info.csv_in_game_voice_model == "FemaleEvenToned"
    assert info.advanced_voice_model == "LydiaUniqueReworked"
    assert info.voice_accent == "en"
    assert "I am sworn to carry your burdens" in info.bio
    
    # Test with a character that should not be found in the CSV (generic NPC)
    info = skyrim.load_external_character_info(
        base_id='unknown', 
        name='Unknown Character', 
        race='[Race <NordRace (00013746)>]', 
        gender=0, 
        ingame_voice_model='[VoiceType <MaleNord (12345678)>]'
    )
    assert isinstance(info, external_character_info)
    assert info.name == "Unknown Character"
    assert info.is_generic_npc == True
    assert info.ingame_voice_model == "MaleNord"
    assert info.tts_voice_model == "MaleNord"
    assert info.csv_in_game_voice_model == "MaleNord"
    assert info.advanced_voice_model == ""
    assert info.voice_accent == "en"
    assert info.bio == "You are a male Nord Unknown Character."


def test_load_external_character_info_empty_voice_cells_are_text(skyrim: Skyrim):
    """Redoran Guard's rows leave voice_model, skyrim_voice_folder and advanced_voice_model empty. They come as '', not nan:
    nan is a number, and Pocket TTS failed on it with "'float' object has no attribute 'lower'" """
    info = skyrim.load_external_character_info(
        base_id='0195B5',
        name='Redoran Guard',
        race='[Race <DarkElfRace (00013742)>]',
        gender=0,
        ingame_voice_model='[VoiceType <DLC2MaleDarkElfCynical (04018469)>]'
    )
    assert info.is_generic_npc == False
    assert info.advanced_voice_model == ""
    assert info.csv_in_game_voice_model == ""
    assert info.tts_voice_model == "Male Dark Elf Cynical"
    assert all(isinstance(value, str) for value in (info.advanced_voice_model, info.csv_in_game_voice_model, info.tts_voice_model, info.voice_accent))



def test_prepare_sentence_for_game(tmp_path, skyrim: Skyrim, default_config: ConfigLoader, example_skyrim_npc_character: Character):
    """Test that prepare_sentence_for_game correctly processes audio files based on configuration"""
    default_config.save_audio_data_to_character_folder = False
    default_config.fast_response_mode = False

    # Create sentence content with the test NPC
    example_sentence_content = SentenceContent(
        speaker = example_skyrim_npc_character,
        text = 'Hello there.',
        sentence_type = SentenceTypeEnum.SPEECH,
    )

    # Copy an existing audio file for the sake of testing
    # Ignore the .lip file as it is can be skipped
    test_audio_path = os.path.join(tmp_path, "test_audio.wav")
    shutil.copyfile('data/mantella_ready.wav', test_audio_path)

    # Create sentence
    example_sentence = Sentence(
        content = example_sentence_content,
        voice_file = test_audio_path,
        voice_line_duration = 1.0,
        error_message = None,
    )

    # Configure mod path for testing
    default_config.mod_path = str(os.path.join(tmp_path, f"Sound/Voice/Mantella.esp"))
    mantella_voice_folder = os.path.join(tmp_path, f"Sound/Voice/Mantella.esp/{skyrim.MANTELLA_VOICE_FOLDER}")
    os.makedirs(mantella_voice_folder, exist_ok=True)

    # Test 1: Default behavior with topicID 1    
    skyrim.prepare_sentence_for_game(
        queue_output = example_sentence, 
        context_of_conversation = None, 
        config = default_config, 
        topicID = 1, 
        isFirstLine = False,
    )
    
    # Verify the correct file path and name for topicID 1
    expected_wav_path = os.path.join(mantella_voice_folder, f"{skyrim.DIALOGUELINE1_FILENAME}.wav")
    assert os.path.exists(expected_wav_path)
    
    # Clean up for next test
    os.remove(expected_wav_path)
    
    # Test 2: Different topicID
    skyrim.prepare_sentence_for_game(
        queue_output = example_sentence, 
        context_of_conversation = None, 
        config = default_config,
        topicID = 2,
        isFirstLine = False,
    )
    
    # Verify different filename for topicID 2
    expected_wav_path_topic2 = os.path.join(mantella_voice_folder, f"{skyrim.DIALOGUELINE2_FILENAME}.wav")
    assert os.path.exists(expected_wav_path_topic2)
    
    # Clean up
    os.remove(expected_wav_path_topic2)

    # Test 3: Fast response mode
    default_config.fast_response_mode = True
    default_config.fast_response_mode_volume = 0

    skyrim.prepare_sentence_for_game(
        queue_output = example_sentence, 
        context_of_conversation = None, 
        config = default_config,
        topicID = 1,
        isFirstLine = True, # isFirstLine determines whether a fast response should play
    )
    
    # Verify the correct file path and name
    expected_wav_path = os.path.join(mantella_voice_folder, f"{skyrim.DIALOGUELINE1_FILENAME}.wav")
    assert os.path.exists(expected_wav_path)
    
    # Clean up
    os.remove(expected_wav_path)
    
    # Note: save_audio_data_to_character_folder is not tested as it may be deprecated
