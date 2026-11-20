from src.config.types.config_value import ConfigValue, ConfigValueTag
from src.config.types.config_value_bool import ConfigValueBool
from src.config.types.config_value_float import ConfigValueFloat
from src.config.types.config_value_int import ConfigValueInt
from src.config.types.config_value_selection import ConfigValueSelection
from src.config.types.config_value_string import ConfigValueString
from src.config.config_value_constraint import ConfigValueConstraint, ConfigValueConstraintResult
from src.stt_backends import presets

# The two parts of the Speech-to-Text page, in this order (settings_ui_constructor draws them)
TALKING_PART = "Talking"
SERVICE_PART = "Service"
PAGE_DESCRIPTION = "How Mantella hears you when you talk to NPCs."
TALKING_DESCRIPTION = "When Mantella listens to you, and how it reacts to your voice."
SERVICE_DESCRIPTION = "What turns what you say into text."
# Settings greyed out while they can't change anything: the setting, and the switch that must be on
GREYED_UNLESS_ON = {"ptt_hotkey": "ptt_enabled", "min_refresh_secs": "proactive_mic_mode"}
CUSTOM_SHOWN_WHEN = f"stt_service={presets.CUSTOM_SERVICE}"


class STTDefinitions:
    class WhisperProcessDeviceChecker(ConfigValueConstraint[str]):
        def __init__(self) -> None:
            super().__init__()
    
    @staticmethod
    def get_audio_threshold_config_value() -> ConfigValue:
        audio_threshold_description = """Controls how much background noise is filtered out (from 0-1).
                                        If the mic is not picking up speech, try lowering this value.
                                        If the mic is picking up too much background noise, try increasing this value."""
        return ConfigValueFloat("audio_threshold","Audio Threshold",audio_threshold_description, 0.4, 0, 1, tags=[ConfigValueTag.share_row])
    
    @staticmethod
    def get_allow_interruption_config_value() -> ConfigValue:
        description = """Sets whether the player can interrupt NPCs mid-response. 
                        Disable this setting if your microphone tends to pick up background noise or is picking up in-game speech. Alternatively, try increasing `Audio Threshold`."""
        return ConfigValueBool("allow_interruption", "Allow Interruption", description, True, tags=[ConfigValueTag.share_row])
    
    @staticmethod
    def get_save_mic_input_config_value() -> ConfigValue:
        description = """Whether to save captured mic input to Documents/My Games/Mantella/data/tmp/mic/.
                        Enable this setting to test your mic quality.
                        Disable this setting to improve performance."""
        return ConfigValueBool("save_mic_input", "Save Mic Input", description, False, tags=[ConfigValueTag.share_row])
    
    @staticmethod
    def get_stt_service_config_value() -> ConfigValue:
        description = """How Mantella turns what you say into text.
                        Moonshine (on your PC): free and fast on any processor, English only. Best for weak laptops.
                        Whisper (on your PC): free, 99 languages. The accurate models want an NVIDIA graphics card.
                        Parakeet (on your PC): free, fast and accurate on any processor, 25 European languages. Downloads about 670 MB the first time. Can't take names, so Skyrim names may come out misspelled.
                        Online Service: a company turns your speech into text, so nothing runs on your PC. Needs a key and costs about 1 to 4 US cents per hour of play.
                        Custom Service: a program on this PC or another one at home, or a service that isn't listed. For example whisper.cpp, which works with AMD and Intel graphics cards."""
        return ConfigValueSelection("stt_service", "Speech-to-Text Service", description, presets.MOONSHINE_CHOICE, presets.service_choices(), allows_free_edit=False,
                                    tags=[ConfigValueTag.share_row])

    @staticmethod
    def get_stt_online_service_config_value() -> ConfigValue:
        description = """The company that turns your speech into text when Speech-to-Text Service is Online Service. Paste its key into the Key box below.
                        OpenAI: most players already have a key.
                        Groq: very fast and the cheapest, about 1 cent per hour of play.
                        Mistral: strong at European languages.
                        Deepgram: the recommended one. Very fast and accurate, and new accounts get $200 of free credit, thousands of hours of play.
                        ElevenLabs: among the most accurate. Uses the same key as the ElevenLabs voices.
                        OpenRouter: uses your OpenRouter chat key when you chat through OpenRouter, so one key and one bill. It can't take names."""
        value = ConfigValueSelection("stt_online_service", "Online Service", description, "OpenAI", presets.online_choices(), allows_free_edit=False)
        value.shown_when = f"stt_service={presets.ONLINE_CHOICE}"
        return value

    @staticmethod
    def get_model_config_value(preset: presets.STTPreset) -> ConfigValue:
        """The Model setting of one choice. Each choice keeps its own, so trying another choice doesn't lose this one's model"""
        descriptions = {
            presets.MOONSHINE_CHOICE: "The Moonshine model, from smallest to largest. Larger models hear better but are slower.",
            presets.PARAKEET_CHOICE: """The Parakeet model. The recommended one, nemo-parakeet-tdt-0.6b-v3, understands 25 European languages and works out which one you speak.
                                     You can also type the name of any other model the onnx-asr program can run.""",
            presets.WHISPER_CHOICE: """The Whisper model. Larger models hear better but are slower and want an NVIDIA graphics card.
                                     large-v3-turbo is nearly as accurate as large-v3 and about 8 times faster, but Whisper STT Translate works poorly with it. The .en models only understand English.
                                     You can also type the name of any other Whisper model in faster-whisper's format (a Hugging Face name such as Systran/faster-whisper-large-v3).""",
        }
        update = f"Press Update to list the speech models {preset.name} offers. " if preset.model_list else ""
        description = descriptions.get(preset.name, f"""The {preset.name} model that turns your speech into text. The recommended one is {preset.default_model}.
                                     {update}You can also type a name.""")
        tags = [ConfigValueTag.share_row] if preset.name == presets.WHISPER_CHOICE else [] # beside Graphics Card or Processor
        value = ConfigValueSelection(preset.model_setting, "Model", description, preset.default_model, list(preset.suggestions), allows_free_edit=True, tags=tags)
        if preset.online:
            value.shown_when = (f"stt_service={presets.ONLINE_CHOICE}", f"stt_online_service={preset.name}")
        else:
            value.shown_when = f"stt_service={preset.name}"
        return value

    @staticmethod
    def get_pause_threshold_config_value() -> ConfigValue:
        description = """How long to wait (in seconds) before converting mic input to text.
                    If you feel like you are being cut off before you finish your response, increase this value.
                    If you feel like there is too much of a delay between you finishing your response and the text conversion, decrease this value.
                    Set this value to 0 for faster response times."""
        return ConfigValueFloat("pause_threshold","Pause Threshold", description, 0.25, 0, 999, tags=[ConfigValueTag.share_row])

    @staticmethod
    def get_ptt_enabled_config_value() -> ConfigValue:
        description = """Enable Push-To-Talk (PTT). When enabled, speech is only captured while the PTT hotkey is held.
                        On key release, the buffered speech is transcribed once and sent to the conversation.
                        When disabled, Mantella behaves as before."""
        return ConfigValueBool("ptt_enabled", "Enable PTT", description, False, tags=[ConfigValueTag.share_row])

    @staticmethod
    def get_ptt_hotkey_config_value() -> ConfigValue:
        description = """Push-To-Talk hotkey. Examples: V, SPACE, ENTER, F1..F12.
                        Letters and digits map directly, special keys include SPACE, TAB, ESC, ENTER, SHIFT, CTRL, ALT, and arrows.
                        Greyed out while Enable PTT is off."""
        return ConfigValueString("ptt_hotkey", "PTT Hotkey", description, "V", tags=[ConfigValueTag.share_row])
    
    @staticmethod
    def get_play_cough_sound_config_value() -> ConfigValue:
        description = """If enabled, a generic cough sound will play when the speech-to-text service fails to transcribe player input.
                        Enable this setting if you are playing in VR / full screen and need an audio cue to know when you haven't been heard.
                        Disable this setting if its annoying."""
        return ConfigValueBool("play_cough_sound", "Cough Error Sound", description, True, tags=[ConfigValueTag.share_row])

    @staticmethod
    def get_listen_timeout_config_value() -> ConfigValue:
        description = """How long to wait (in seconds) for the player to speak before retrying.
                    This needs to be set to ensure that Mantella can periodically check if the conversation has ended."""
        return ConfigValueInt("listen_timeout","Listen Timeout",  description, 30, 0, 999, tags=[ConfigValueTag.share_row])
    
    @staticmethod
    def get_proactive_mic_mode_config_value() -> ConfigValue:
        description = """If enabled, mic input will be transcribed continuously (every `Refresh Frequency` seconds) as long as speech is detected.
                        Enable this setting to improve response times.
                        Disable this setting to improve performance.
                        Always off for the Online Service choices, where every update is a separate paid request. Custom Service keeps it as set here: an online address is billed for every update."""
        return ConfigValueBool("proactive_mic_mode", "Proactive Mode", description, False, tags=[ConfigValueTag.share_row])
    
    @staticmethod
    def get_min_refresh_secs_config_value() -> ConfigValue:
        description = """Controls how frequently mic input is transcribed by Moonshine / Whisper.
                        Increase this value if the frequency is too intense for your hardware.
                        Decrease this value to improve response times.
                        Greyed out while Proactive Mode is off."""
        return ConfigValueFloat("min_refresh_secs", "Refresh Frequency", description, 0.3, 0.01, 999, tags=[ConfigValueTag.share_row])
    
    @staticmethod
    def get_stt_language_config_value() -> ConfigValue:
        description = """The language you speak. 'default' is the language set on the main settings. Every choice that understands more than English uses it."""
        return ConfigValueSelection("stt_language","Spoken Language",description,"default",["default","en", "ar", "cs", "da", "de", "el", "es", "fi", "fr", "hi", "hu", "it", "ja", "ko", "nl", "pl", "pt", "ro", "ru", "sv", "sw", "uk", "ha", "tr", "vi", "yo"], tags=[ConfigValueTag.share_row])

    @staticmethod
    def get_stt_translate_config_value() -> ConfigValue:
        description = """Turns what you say into English text. Only works with Whisper on your PC."""
        value = ConfigValueBool("stt_translate", "Whisper STT Translate",description, False)
        value.shown_when = f"stt_service={presets.WHISPER_CHOICE}"
        return value

    @staticmethod
    def get_stt_first_names_config_value() -> ConfigValue:
        description = """Catches names when you only say part of them: "Camilla" as well as "Camilla Valerius".
                        Works with Deepgram, ElevenLabs and Mistral, and is greyed out for the others. Everyday words in names, like "Guard" or "Fox", are left out.
                        Turn it off if ordinary words you say get written as names."""
        return ConfigValueBool("stt_first_names", "Catch First Names", description, False)

    @staticmethod
    def get_process_device_config_value() -> ConfigValue:
        description = """Whether Whisper runs on your processor (cpu) or your NVIDIA graphics card (cuda, which needs CUDA installed).
                        AMD and Intel graphics cards can't be used here: run whisper.cpp instead and pick Custom Service to connect to it."""
        value = ConfigValueSelection("process_device", "Graphics Card or Processor", description, "cpu", ["cpu", "cuda"], tags=[ConfigValueTag.share_row])
        value.shown_when = f"stt_service={presets.WHISPER_CHOICE}"
        return value

    @staticmethod
    def get_moonshine_folder_config_value(is_hidden: bool = False) -> ConfigValue:
        description = "The folder where Moonshine models are installed (where tiny/ and base/ folders exist)."
        value = ConfigValueString("moonshine_folder", "Moonshine Folder", description, "", is_hidden=is_hidden)
        value.shown_when = f"stt_service={presets.MOONSHINE_CHOICE}"
        return value


    # Custom Service: any program or service that accepts a known format, shown while Speech-to-Text Service is Custom Service
    @staticmethod
    def get_custom_format_config_value() -> ConfigValue:
        description = """The form the program or service accepts. OpenAI-style is the form most of them copy: whisper.cpp, Speaches, LocalAI, Together, Fireworks, OpenRouter and others.
                        Deepgram-style and ElevenLabs-style are for Deepgram's and ElevenLabs' own forms, for example at another of their addresses."""
        value = ConfigValueSelection("stt_custom_format", "Format", description, presets.OPENAI_STYLE_FORMAT, list(presets.FORMATS.keys()), allows_free_edit=False)
        value.shown_when = CUSTOM_SHOWN_WHEN
        return value

    @staticmethod
    def get_custom_address_config_value() -> ConfigValue:
        description = """Where to send your speech. The short form (http://127.0.0.1:8000/v1) or the full one (http://127.0.0.1:8000/v1/audio/transcriptions) both work.
                        whisper.cpp's server: http://127.0.0.1:8080/inference (or the other PC's address, such as http://192.168.1.20:8080/inference, when it runs there)."""
        value = ConfigValueString("stt_custom_address", "Address", description, "http://127.0.0.1:8080/inference")
        value.shown_when = CUSTOM_SHOWN_WHEN
        return value

    @staticmethod
    def get_custom_model_config_value() -> ConfigValue:
        description = """The model name the program or service expects, such as Systran/faster-whisper-large-v3 for Speaches. whisper.cpp ignores it, so it can stay empty.
                        The Key box below takes a key if the service needs one. Programs on your own PCs usually need none."""
        value = ConfigValueString("stt_custom_model", "Model", description, "", tags=[ConfigValueTag.share_row])
        value.shown_when = CUSTOM_SHOWN_WHEN
        return value

    @staticmethod
    def get_custom_timeout_config_value() -> ConfigValue:
        description = """How many seconds to wait for an answer, both tries together, before giving up and playing the cough sound.
                        8 suits online services. A PC at home running a large Whisper model may need longer."""
        value = ConfigValueFloat("stt_custom_timeout", "Time Limit", description, presets.DEFAULT_TIMEOUT_SECONDS, 1, 120, tags=[ConfigValueTag.share_row])
        value.shown_when = CUSTOM_SHOWN_WHEN
        return value
