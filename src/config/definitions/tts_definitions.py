import os
from enum import Enum, auto
from src.config.types.config_value import ConfigValue, ConfigValueTag
from src.config.types.config_value_bool import ConfigValueBool
from src.config.types.config_value_float import ConfigValueFloat
from src.config.types.config_value_int import ConfigValueInt
from src.config.types.config_value_path import ConfigValuePath
from src.config.types.config_value_selection import ConfigValueSelection
from src.config.types.config_value_string import ConfigValueString
from src.config.config_value_constraint import ConfigValueConstraint, ConfigValueConstraintResult

class TTSEnum(Enum):
    PIPER = auto()
    XTTS = auto()
    XVASYNTH = auto()

    @property
    def display_name(self) -> str:
        return {
            self.PIPER: 'Piper',
            self.XTTS: 'XTTS',
            self.XVASYNTH: 'xVASynth',
        }[self]

TRY_A_VOICE_GROUP = "Try A Voice"
VOICE_SETUPS_GROUP = "Voice Setups"
WORD_LISTS_GROUP = "Fix Pronunciation"
VOICE_CLIPS_GROUP = "Voice Clips"
POCKET_TTS_GROUP = "Pocket TTS"
ELEVENLABS_GROUP = "ElevenLabs"
INWORLD_GROUP = "Inworld"
# Folded parts of the Text-to-Speech page for one service's settings (config.ini keeps them under [TTS])
XTTS_PART = "XTTS"
XVASYNTH_PART = "xVASynth"

class TTSDefinitions:
    VOICE_SETUPS_DESCRIPTION = """Connect any voice program or website, no code needed. Mantella speaks two request styles: OpenAI-style (llama.cpp, audio.cpp, LocalAI, many websites) and XTTS-style (XTTS servers and programs like them).
A saved setup shows up in the TTS Service list, can be the Backup Voice Service, and works in a character's tts_service column.
NPC voices are cloned from their voice clips (see Voice Clips). Until a clip arrives, the setup's male or female voice speaks.
Setups are saved in voice_setups.json next to config.ini, so you can copy them to another PC."""
    WORD_LISTS_DESCRIPTION = """Fix how a word is said, for example Dovahkiin as Doh-vah-keen. Only the speech changes, never the subtitles or what NPCs remember. Works from the next line.
The lists are in the pronunciation folder next to config.ini: mantella_words.csv (Mantella's, replaced by updates), my_words.csv (yours, never touched by updates, and it wins over Mantella's), and any other list you put there.
Pocket TTS and your voice setups use these lists. Piper, XTTS and xVASynth don't."""
    VOICE_CLIPS_DESCRIPTION = """Every NPC's voice is cloned from a few seconds of their real voice in your game. Hear it, swap it, or give an NPC a voice of their own."""
    POCKET_TTS_DESCRIPTION = """A small voice program by Kyutai. It clones each NPC's voice from a clip of their real lines and runs on the processor, leaving the graphics card to Skyrim. Pick it under TTS Service: Mantella downloads and starts it for you."""
    ELEVENLABS_DESCRIPTION = """Online voices that sound the best in listening tests, and cost the most. Each NPC's voice is cloned once on your ElevenLabs account. Pick it under TTS Service, or for one NPC in their tts_service column."""
    INWORLD_DESCRIPTION = """Online voices close to ElevenLabs' for less, with voice cloning on every plan. Each NPC's voice is cloned once on your Inworld account. Pick it under TTS Service, or for one NPC in their tts_service column."""

    class ResourceFolderExistsChecker(ConfigValueConstraint[str]):
        def __init__(self) -> None:
            super().__init__(f"Selected folder must contain subfolder '\\resources\\'!")

        def apply_constraint(self, value_to_apply_to: str) -> ConfigValueConstraintResult:
            if not os.path.exists(f"{value_to_apply_to}\\resources\\"):
                return ConfigValueConstraintResult(f'''
The selected folder for xVASynth is missing the expected subfolder '\\resources\\'. 
If you have trouble installing the xVASynth version from Nexus, try installing it from Steam.''')
            else:
                return ConfigValueConstraintResult()
        
    @staticmethod
    def voice_service_options() -> tuple[list[str], list]:
        """Every voice service and voice setup, with the old TTS Service value for Piper, XTTS and xVASynth (None for the others)"""
        from src.tts.registry import voice_services
        services = voice_services.all()
        return [s.name for s in services], [s.legacy_enum for s in services]

    @staticmethod
    def refresh_voice_service_options(selection: ConfigValueSelection) -> None:
        """After a voice setup is added or removed, the TTS Service and Backup Voice Service lists show it"""
        options, enums = TTSDefinitions.voice_service_options()
        selection.set_options(options, enums)

    @staticmethod
    def get_tts_service_config_value() -> ConfigValue:
        description = """The TTS service used by Mantella as default.

Support TTS service selection for each character. (for example, Xvasynth for Serana and Piper for the rest). Need to add a new column "tts_service" in the main/override CSV file. When leaving empty, use the default option (whichever is selected in the UI)

Example values in the 'tts_service' column: 'piper', 'xtts', 'xvasynth', or 'xva' (both can refer to the Xvasynth), 'pocket tts', 'elevenlabs', 'inworld', or the name of one of your voice setups.

Your own voice setups (Voice Setups, below) are in this list too."""
        options, enums = TTSDefinitions.voice_service_options()
        return ConfigValueSelection("tts_service", "TTS Service", description, TTSEnum.PIPER.display_name, options, enums)

    @staticmethod
    def get_tts_backup_service_config_value() -> ConfigValue:
        description = """Speaks a line when Pocket TTS or one of your voice setups fails, for example when your internet drops or the program stops.
The line is tried twice first. The NPC's voice changes for that one line instead of going silent.
Piper, XTTS and xVASynth work as before: when one of them fails for an NPC, the TTS Service speaks the line."""
        options, enums = TTSDefinitions.voice_service_options()
        return ConfigValueSelection("tts_backup_service", "Backup Voice Service", description, TTSEnum.PIPER.display_name, options, enums)

    @staticmethod
    def get_xvasynth_folder_config_value() -> ConfigValue:
        return ConfigValuePath("xvasynth_folder", "xVASynth Folder", "The folder you have xVASynth downloaded to (the folder that contains xVASynth.exe).", "C:\\Games\\Steam\\steamapps\\common\\xVASynth","xVASynth.exe",[TTSDefinitions.ResourceFolderExistsChecker()])
    
    @staticmethod
    def get_xtts_folder_config_value() -> ConfigValue:
        return ConfigValuePath("xtts_server_folder", "XTTS Folder", "The folder you have the XTTS server downloaded to (the folder that contains xtts-api-server-mantella.exe).", "C:\\Games\\Steam\\steamapps\\common\\XTTS","xtts-api-server-mantella.exe")
    
    @staticmethod
    def get_piper_folder_config_value(is_hidden: bool = False) -> ConfigValue:
        #Note(Leidtier): Because this is a Frankenparameter, I just set it to be a string. It SHOULD be a path, but this would require a different handling of the default empty state
        return ConfigValueString("piper_folder", "Piper Folder", "The folder where Piper is installed (where piper.exe exists).", "", is_hidden=is_hidden)

    @staticmethod
    def get_lipgen_folder_config_value(is_hidden: bool = False) -> ConfigValue:
        #Note(Leidtier): Because this is a Frankenparameter, I just set it to be a string. It SHOULD be a path, but this would require a different handling of the default empty state
        description = """The LipGenerator program is required by Bethesda games to accurately lip sync voicelines.
                        Please install the Creation Kit from Steam. Once installed, set this path to be the same path as your game folder (where the exectuable is located)."""
        return ConfigValueString("lipgen_folder", "LipGenerator Folder", description, "", is_hidden=is_hidden)
    
    @staticmethod
    def get_facefx_folder_config_value(is_hidden: bool = False) -> ConfigValue:
        #Note(Leidtier): Because this is a Frankenparameter, I just set it to be a string. It SHOULD be a path, but this would require a different handling of the default empty state
        facefx_discription = """The FaceFXWrapper is an alternative program to LipGenerator. Lip sync generation is required by Bethesda games to accurately lip sync voicelines.
                                This path does not need to be set if you have the LipGenerator folder configured."""
        return ConfigValueString("facefx_folder", "FaceFXWrapper Folder", facefx_discription, "", is_hidden=is_hidden)
    
    @staticmethod
    def get_number_words_tts_config_value() -> ConfigValue:
        description = """Minimum threshold of words in a sentence needed before sending to the TTS service.
                        TTS services often struggle with synthesizing short voicelines, so increasing this threshold can improve quality."""
        return ConfigValueInt("number_words_tts","Number of Words TTS",description, 3, 1, 999999)
    
    @staticmethod
    def get_lip_generation_config_value() -> ConfigValue:
        description = """Whether to generate lip sync files for spoken voicelines. Disable this setting to improve response times.
                        Set to 'Lazy' to skip lip syncing only for the first sentence spoken of every response."""
        return ConfigValueSelection("lip_generation","Lip File Generation",description,"Enabled",["Enabled","Lazy","Disabled"],tags=[ConfigValueTag.advanced])
    
    @staticmethod
    def get_fast_response_mode_config_value() -> ConfigValue:
        description = """Whether to play the first voiceline of every response directly from the Mantella exe instead of in-game (Skyrim only).
                        Enable this setting to improve response times.
                        Disable this setting to play all voicelines natively in-game."""
        return ConfigValueBool("fast_response_mode","Fast Response Mode", description, False, tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    @staticmethod
    def get_fast_response_mode_volume_config_value() -> ConfigValue:
        description = """Adjust the volume of the first delivered voiceline (from 1-100) if Fast Response Mode is enabled."""
        return ConfigValueInt("fast_response_mode_volume","Fast Response Mode Volume", description, 40, 1, 100, tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    # XTTS Section

    @staticmethod
    def get_xtts_url_config_value() -> ConfigValue:
        description = """The URL that your XTTS server is running on.
                        Examples:
                        http://127.0.0.1:8020 when running XTTS locally.
                        https://{POD_ID}-8020.proxy.runpod.net when running XTTS in a RunPod GPU pod."""
        return ConfigValueString("xtts_url","XTTS URL",description, "http://127.0.0.1:8020",tags=[ConfigValueTag.advanced])
    
    @staticmethod
    def get_xtts_default_model_config_value() -> ConfigValue:
        return ConfigValueSelection("xtts_default_model","XTTS Default Model","Official base XTTS-V2 model to use.", "main",["v2.0.0", "v2.0.1", "v2.0.2", "v2.0.3", "main"],tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])

    @staticmethod
    def get_xtts_device_config_value() -> ConfigValue:
        return ConfigValueSelection("xtts_device","XTTS Device","Set to cpu or cuda (NVIDIA GPUs only). You can also specify which NVIDIA GPU to use (cuda:0, cuda:1 etc)", "cpu" ,["cpu", "cuda", "cuda:0", "cuda:1", "cuda:2","cuda:3", "cuda:4", "cuda:5", "cuda:6", "cuda:7"],tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])

    @staticmethod
    def get_xtts_deepspeed_config_value() -> ConfigValue:
        return ConfigValueBool("xtts_deepspeed","XTTS DeepSpeed","Allows you to speed up processing by several times (highly recommended for NVIDIA GPU players!).\nOnly usable with an NVIDIA GPU that supports CUDA 11.8+.", False, tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])

    @staticmethod
    def get_xtts_lowvram_config_value() -> ConfigValue:
        description = """The mode in which the model will be stored in RAM and when the processing occurs it will move to VRAM. The difference in speed is small.
                    If you don't want to pre-generate the latents for every speaker, enable this setting or else it will generate the latents at every start."""
        return ConfigValueBool("xtts_lowvram","XTTS Low VRAM", description, True, tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    @staticmethod
    def get_xtts_data_config_value() -> ConfigValue:
        value = """{"temperature": 0.75,
                    "length_penalty": 1.0,
                    "repetition_penalty": 5.0,
                    "top_k": 50,
                    "top_p": 0.85,
                    "speed": 1,
                    "enable_text_splitting": true,
                    "stream_chunk_size": 100}"""
        return ConfigValueString("xtts_data","XTTS Data","Default settings passed to the XTTS API server.", value,tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    @staticmethod
    def get_xtts_accent_config_value() -> ConfigValue:
        description = """Note that this setting is only available for Skyrim.
        Changes the 'accent' of NPCs by sending the language value from data/Skyrim/skyrim_characters.csv's lang_override column to XTTS.\nThis helps give NPC's unique-sounding voices, even when they use the same base voice model."""
        return ConfigValueBool("xtts_accent", "XTTS Accent", description, False, tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    # xVASynth section
    @staticmethod
    def get_tts_process_device_config_value() -> ConfigValue:
        description = "Whether to run xVASynth server (unless already running) on your CPU or a NVIDIA GPU (with CUDA installed).\nThis setting has a minimal effect on response times."
        return ConfigValueSelection("tts_process_device","xVASynth Process Device", description, "cpu", ["cpu", "gpu"], tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])

    @staticmethod
    def get_pace_config_value() -> ConfigValue:
        description = """The default speed of talking. The effect can vary between voices.
                        0.5 = 2x faster; 2 = 2x slower"""
        return ConfigValueFloat("pace","xVASynth Pace", description, 1.0, 0.1, 2.0, tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    @staticmethod
    def get_use_cleanup_config_value() -> ConfigValue:
        description = """Whether to try to reduce noise and the robot-sounding nature of xVASynth generated speech.
                        Has a slight impact on processing speed for the CPU.
                        Not meant to be used on voices that have post-effects attached to them (echoes, reverbs, etc)."""
        return ConfigValueBool("use_cleanup","xVASynth Cleanup",description, False, tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    @staticmethod
    def get_use_sr_config_value() -> ConfigValue:
        description = """Whether to improve the quality of your audio through Super-resolution of 22050Hz audio into 48000Hz audio.
                        This is a fairly slow process on CPUs, but on some GPUs it can be relatively fast."""
        return ConfigValueBool("use_sr","xVASynth Super Resolution",description, False, tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    @staticmethod
    def get_tts_print_config_value() -> ConfigValue:
        return ConfigValueBool("tts_print","Print TTS Output","Print the output from the TTS service in the Mantella.exe window.", False, tags=[ConfigValueTag.advanced])

    






