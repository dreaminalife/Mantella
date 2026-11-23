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

# The Text-to-Speech page, top to bottom: Speed and lip sync (folded), TTS Service and Backup Voice Service, a list of every
# service (Settings for), and under it the picked one's Global settings for <service> (with Your key in them) and Model, or its program
# settings. Then folded: Hear it, Customization per voice type and per NPC, Voice Effects and Fix Pronunciation.
# Only the page changes: config.ini keeps every setting under [TTS]
SPEED_PART = "Speed and lip sync"
NPC_VOICES_PART = "Customization per voice type and per NPC"
HEAR_IT_PART = "Hear it"
WORD_LISTS_GROUP = "Fix Pronunciation"
VOICE_EFFECTS_GROUP = "Voice Effects"
POCKET_TTS_GROUP = "Pocket TTS"
NO_BACKUP = "None" # the Backup Voice Service's choice for no backup. Not a voice service: voice_services.find gives None for it
ELEVENLABS_GROUP = "ElevenLabs"
INWORLD_GROUP = "Inworld"
HEYGEN_GROUP = "HeyGen"
QWEN_GROUP = "Qwen"
BOSON_GROUP = "Boson AI"
IODINE_GROUP = "Iodine"
PIPER_PART = "Piper"
XTTS_PART = "XTTS"
XVASYNTH_PART = "xVASynth"
VOICE_SETUPS_PART = "Your voice setups"
# The online services' parts of the page: each has its own key panel
ONLINE_SERVICE_GROUPS = (ELEVENLABS_GROUP, INWORLD_GROUP, HEYGEN_GROUP, QWEN_GROUP, BOSON_GROUP, IODINE_GROUP)
# The choices under Settings for, in their order: one part of the page each, shown one at a time
SERVICE_PARTS = (POCKET_TTS_GROUP, *ONLINE_SERVICE_GROUPS, PIPER_PART, XTTS_PART, XVASYNTH_PART, VOICE_SETUPS_PART)
# Each newer service's Volume (config.ini keeps it under [TTS]); it shows in that service's part of the page
SERVICE_VOLUMES = {POCKET_TTS_GROUP: "pocket_tts_volume", ELEVENLABS_GROUP: "elevenlabs_volume", INWORLD_GROUP: "inworld_volume",
                   HEYGEN_GROUP: "heygen_volume", QWEN_GROUP: "qwen_volume", BOSON_GROUP: "boson_volume", IODINE_GROUP: "iodine_volume"}


def service_part_id(part: str) -> str:
    """The page element of a service's part (elem_id), e.g. tts-part-boson-ai"""
    return "tts-part-" + "-".join(part.lower().split())

def every_npc_part(part: str) -> str:
    """The fold with a service's settings for every NPC, in its part under Settings for"""
    return f"Global settings for {part}"


def sound_part_id(part: str) -> str:
    """The page element of a service's Global settings (elem_id), e.g. tts-part-boson-ai-sound"""
    return service_part_id(part) + "-sound"

class TTSDefinitions:
    WORD_LISTS_DESCRIPTION = """Fix how a word is said, for example Dovahkiin as Doh-vah-keen."""
    VOICE_CLIPS_DESCRIPTION = """Every NPC's voice is cloned from a few seconds of their real voice in your game. Change any one voice: hear it, swap it, or give an NPC a voice of their own."""
    VOICE_EFFECTS_DESCRIPTION = """Give creatures and ghosts a voice that fits. Every effect is off until you tick it."""
    SPEED_DESCRIPTION = """For every service: how fast NPCs start talking, and how their mouths move."""
    NO_CLONING_NOTE = """{name} doesn't clone NPCs' voices from your game, so there are no voices to change here."""

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
    def voice_service_options(with_no_backup: bool = False) -> tuple[list[str], list]:
        """Every voice service and voice setup, with the old TTS Service value for Piper, XTTS and xVASynth (None for the others).
        *with_no_backup*: the Backup Voice Service's list, which starts with None"""
        from src.tts.registry import voice_services
        services = voice_services.all()
        names, enums = [s.name for s in services], [s.legacy_enum for s in services]
        if with_no_backup:
            return [NO_BACKUP, *names], [None, *enums]
        return names, enums

    @staticmethod
    def refresh_voice_service_options(selection: ConfigValueSelection) -> None:
        """After a voice setup is added or removed, the TTS Service and Backup Voice Service lists show it"""
        options, enums = TTSDefinitions.voice_service_options(selection.identifier == "tts_backup_service")
        selection.set_options(options, enums)

    @staticmethod
    def get_tts_service_config_value() -> ConfigValue:
        description = """The TTS service used by Mantella as default.

Support TTS service selection for each character. (for example, Xvasynth for Serana and Piper for the rest). Need to add a new column "tts_service" in the main/override CSV file. When leaving empty, use the default option (whichever is selected in the UI)

Example values in the 'tts_service' column: 'piper', 'xtts', 'xvasynth', or 'xva' (both can refer to the Xvasynth), 'pocket tts', 'elevenlabs', 'inworld', 'heygen', 'qwen', 'boson', 'iodine', or the name of one of your voice setups.

Your own voice setups are in this list too. The picked service's settings show below."""
        options, enums = TTSDefinitions.voice_service_options()
        return ConfigValueSelection("tts_service", "TTS Service", description, POCKET_TTS_GROUP, options, enums, tags=[ConfigValueTag.share_row])

    @staticmethod
    def get_tts_backup_service_config_value() -> ConfigValue:
        description = """Speaks a line when Pocket TTS or one of your voice setups fails, for example when your internet drops or the program stops.
The line is tried twice first. The NPC's voice changes for that one line instead of going silent.
Piper, XTTS and xVASynth work as before: when one of them fails for an NPC, the TTS Service speaks the line."""
        options, enums = TTSDefinitions.voice_service_options(with_no_backup=True)
        return ConfigValueSelection("tts_backup_service", "Backup Voice Service", description, NO_BACKUP, options, enums,
                                    tags=[ConfigValueTag.share_row],
                                    option_descriptions={NO_BACKUP: "No backup: a line that still fails after the two tries is skipped."})

    @staticmethod
    def get_service_volume_config_value(service: str) -> ConfigValue:
        """*service*: a group in SERVICE_VOLUMES (Pocket TTS, ElevenLabs, ...). Shown in that service's part of the page"""
        description = f"""How loud NPCs speak with {service}. 100% is as loud as Skyrim's own lines, 150% louder, 50% quieter.
Every line is first made as loud as the game's own lines, then this is applied. Very loud lines are kept from crackling."""
        return ConfigValueInt(SERVICE_VOLUMES[service], "Volume", description, 100, 10, 300).in_folded_part(service)

    @staticmethod
    def get_speed_config_value(group: str) -> ConfigValue:
        """Talking speed (src/tts/voice_speed.py): an online service's for each model, Pocket TTS's one. A voice setup keeps its own in the setup"""
        from src.tts.voice_style import SPEEDS
        return TTSDefinitions.get_style_config_value(SPEEDS[group], group,
            "How fast NPCs talk. 1 is the voice's own speed, 1.2 is a little faster, 0.8 a little slower. Their pitch stays the same.")

    @staticmethod
    def get_service_model_config_value(group: str, description: str) -> ConfigValue:
        """An online service's Model setting: the models on Mantella's list (src/tts/model_catalog.py), and any model typed in"""
        from src.tts.model_catalog import ServiceModels
        description += "\nMissing a model? Add it next to Settings for."
        return ServiceModels.of(group).config_value(description)

    @staticmethod
    def get_model_settings_config_value(group: str) -> ConfigValue:
        """Hidden: each model's own values of the service's global settings, kept while another model is picked (voice_style.py)"""
        from src.tts.model_catalog import ServiceModels
        models = ServiceModels.of(group)
        return ConfigValueString(models.stored_setting, f"{group}'s settings for each model", "Kept by Mantella.", "", is_hidden=True).in_folded_part(group)

    @staticmethod
    def get_model_list_config_value(group: str) -> ConfigValue:
        """Hidden: the models the player added to the service's list, and Mantella's models they removed (model_catalog.ListChanges)"""
        from src.tts.model_catalog import ServiceModels
        models = ServiceModels.of(group)
        return ConfigValueString(models.list_setting, f"{group}'s added and removed models", "Kept by Mantella.", "", is_hidden=True).in_folded_part(group)

    @staticmethod
    def get_audio_tags_config_value(module, group: str, description: str) -> ConfigValue:
        """The Audio tags switch of a service that makes sounds (src/tts/voice_tags.py): *module* has AUDIO_TAGS_SETTING"""
        description += "\nNeeds the default prompts: if you changed yours, reset them to default."
        return ConfigValueBool(module.AUDIO_TAGS_SETTING, "Audio tags", description, False).in_folded_part(group)

    @staticmethod
    def get_directions_config_value(module, group: str, description: str) -> ConfigValue:
        """The Directions switch of a service that takes directions: *module* has DIRECTIONS_SETTING"""
        description += "\nNeeds the default prompts: if you changed yours, reset them to default."
        return ConfigValueBool(module.DIRECTIONS_SETTING, "Directions", description, False).in_folded_part(group)

    class JsonObjectChecker(ConfigValueConstraint[str]):
        """Settings Mantella doesn't have yet: empty, or fields written as a JSON object"""
        def __init__(self) -> None:
            super().__init__(None)

        def apply_constraint(self, value_to_apply_to: str) -> ConfigValueConstraintResult:
            from src.tts.voice_style import parsed_object
            text = (value_to_apply_to or "").strip()
            if text and parsed_object(text) is None:
                return ConfigValueConstraintResult('Not readable: use {"name": value}, with double quotes.')
            return ConfigValueConstraintResult()

    @staticmethod
    def get_style_config_value(setting, group: str, description: str, option_descriptions: dict[str, str] | None = None) -> ConfigValue:
        """A voice style setting (src/tts/voice_style.py): how every NPC sounds with this service's picked model, unless a voice has
        its own under Customization per voice type and per NPC. Each model keeps its own value, and a model that skips it hides it"""
        if setting.json_object:
            value: ConfigValue = ConfigValueString(setting.name, setting.label, description, setting.default, [TTSDefinitions.JsonObjectChecker()])
        elif setting.choices:
            value = ConfigValueSelection(setting.name, setting.label, description, setting.default, list(setting.choices),
                                         option_descriptions=option_descriptions)
        else:
            value = ConfigValueFloat(setting.name, setting.label, description, setting.default, setting.low, setting.high, tags=[ConfigValueTag.slider])
        return value.in_folded_part(group)

    @staticmethod
    def get_extra_request_config_value(group: str) -> ConfigValue:
        """Settings Mantella doesn't have yet: fields the player writes, sent with every line on the picked model"""
        from src.tts.voice_style import EXTRA_REQUESTS
        return TTSDefinitions.get_style_config_value(EXTRA_REQUESTS[group], group,
            f'Fields sent with every line on the picked model, named the way {group}\'s own instructions name them, for example {{"seed": 42}}. '
            f"Leave it empty for none. If {group} refuses them, the backup voice speaks and this page says why.")

    @staticmethod
    def get_online_service_config_values() -> list[ConfigValue]:
        """The settings shown in the online services' parts of the page, above their Volume"""
        from src.tts import boson, heygen, inworld, iodine, qwen
        from src.tts.voice_style import BOSON_EXPRESSIVENESS, HEYGEN_EXPRESSIVENESS, HEYGEN_SIMILARITY, INWORLD_DELIVERY, IODINE_TEMPERATURE
        # Only on a model that takes directions (not TTS-2 Flash): the page hides it with another model
        inworld_directions = TTSDefinitions.get_directions_config_value(inworld, INWORLD_GROUP, "NPCs say lines the way they feel: angrily, in a whisper, trembling.")
        same_voices = "NPC voices already cloned stay the same."
        return [
            TTSDefinitions.get_service_model_config_value(ELEVENLABS_GROUP, f"The model ElevenLabs speaks with. {same_voices}"),
            TTSDefinitions.get_speed_config_value(ELEVENLABS_GROUP),
            TTSDefinitions.get_extra_request_config_value(ELEVENLABS_GROUP),
            TTSDefinitions.get_model_settings_config_value(ELEVENLABS_GROUP),
            TTSDefinitions.get_model_list_config_value(ELEVENLABS_GROUP),
            TTSDefinitions.get_service_model_config_value(INWORLD_GROUP, f"TTS-2 sounds the best. TTS-2 Flash answers faster and costs less. {same_voices}"),
            TTSDefinitions.get_style_config_value(INWORLD_DELIVERY, INWORLD_GROUP, "How NPCs deliver their lines.",
                                                  {"Stable": "Even, much the same every time.", "Balanced": "Inworld's usual.",
                                                   "Creative": "A wider range of feeling, less predictable."}),
            TTSDefinitions.get_audio_tags_config_value(inworld, INWORLD_GROUP, "NPCs laugh, sigh, gasp and groan while they talk, where it fits the line."),
            inworld_directions,
            TTSDefinitions.get_speed_config_value(INWORLD_GROUP),
            TTSDefinitions.get_extra_request_config_value(INWORLD_GROUP),
            TTSDefinitions.get_model_settings_config_value(INWORLD_GROUP),
            TTSDefinitions.get_model_list_config_value(INWORLD_GROUP),
            TTSDefinitions.get_service_model_config_value(HEYGEN_GROUP, f"The model HeyGen speaks with. {same_voices}"),
            TTSDefinitions.get_style_config_value(HEYGEN_SIMILARITY, HEYGEN_GROUP, "How closely each voice copies its clips. Each model keeps its own. Changing it, or picking a model with another Similarity, clones every voice again the next time it speaks: free HeyGen accounts can clone 20 voices a day.",
                                                  {"Medium": "The loosest copy.", "High": "A looser copy.", "Very high": "A close copy.",
                                                   "Highest": "The closest copy of the clips."}),
            TTSDefinitions.get_style_config_value(HEYGEN_EXPRESSIVENESS, HEYGEN_GROUP, "How much NPCs' voices rise and fall: 0 is the flattest, 1 the liveliest. HeyGen ignores it on a line with an audio tag."),
            TTSDefinitions.get_audio_tags_config_value(heygen, HEYGEN_GROUP, "NPCs laugh, sigh and gasp, and speak angrily, sadly or in a whisper, where it fits the line."),
            TTSDefinitions.get_speed_config_value(HEYGEN_GROUP),
            TTSDefinitions.get_extra_request_config_value(HEYGEN_GROUP),
            TTSDefinitions.get_model_settings_config_value(HEYGEN_GROUP),
            TTSDefinitions.get_model_list_config_value(HEYGEN_GROUP),
            TTSDefinitions.get_service_model_config_value(QWEN_GROUP, "The model Qwen speaks with. A Qwen voice only works with the model it was cloned for, so another model clones each voice once more the next time it speaks."),
            ConfigValueString(qwen.WORKSPACE_SETTING, "Workspace ID", "Your workspace in Alibaba Cloud's Model Studio, Singapore region: copy it from the console's Workspace page. Your key must come from the same workspace.", "").in_folded_part(QWEN_GROUP),
            TTSDefinitions.get_audio_tags_config_value(qwen, QWEN_GROUP, "NPCs laugh, sigh, gasp and cough while they talk, where it fits the line."),
            TTSDefinitions.get_directions_config_value(qwen, QWEN_GROUP, "NPCs say lines the way they feel: angrily, in a whisper, trembling. Only while Mantella's Language is English (en): Qwen wants it in the language of the line."),
            TTSDefinitions.get_speed_config_value(QWEN_GROUP),
            TTSDefinitions.get_extra_request_config_value(QWEN_GROUP),
            TTSDefinitions.get_model_settings_config_value(QWEN_GROUP),
            TTSDefinitions.get_model_list_config_value(QWEN_GROUP),
            TTSDefinitions.get_service_model_config_value(BOSON_GROUP, f"The model Boson AI speaks with. {same_voices}"),
            TTSDefinitions.get_style_config_value(BOSON_EXPRESSIVENESS, BOSON_GROUP, "How much NPCs' voices rise and fall.",
                                                  {"Flat": "Flatter and calmer.", "Normal": "Plain, everyday speech.", "Lively": "More feeling in each line."}),
            TTSDefinitions.get_audio_tags_config_value(boson, BOSON_GROUP, "NPCs laugh, sigh and sob, and speak angrily, sadly or in a whisper, where it fits the line."),
            TTSDefinitions.get_speed_config_value(BOSON_GROUP),
            TTSDefinitions.get_extra_request_config_value(BOSON_GROUP),
            TTSDefinitions.get_model_settings_config_value(BOSON_GROUP),
            TTSDefinitions.get_model_list_config_value(BOSON_GROUP),
            TTSDefinitions.get_service_model_config_value(IODINE_GROUP, f"The model Iodine speaks with. {same_voices}"),
            TTSDefinitions.get_style_config_value(IODINE_TEMPERATURE, IODINE_GROUP, "Lower sounds steadier and more alike each time, higher more varied. "
                                                  "Iodine suggests lowering it, for example to 0.7, if words sometimes come out misread, skipped or repeated."),
            TTSDefinitions.get_audio_tags_config_value(iodine, IODINE_GROUP, "NPCs laugh, sigh and gasp, speak nervously or sarcastically, and whisper or stress a few words, where it fits the line."),
            TTSDefinitions.get_directions_config_value(iodine, IODINE_GROUP, "NPCs say lines the way they feel, as a note to an actor: quietly, trying not to laugh."),
            TTSDefinitions.get_speed_config_value(IODINE_GROUP),
            TTSDefinitions.get_extra_request_config_value(IODINE_GROUP),
            TTSDefinitions.get_model_settings_config_value(IODINE_GROUP),
            TTSDefinitions.get_model_list_config_value(IODINE_GROUP),
        ]

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

    






