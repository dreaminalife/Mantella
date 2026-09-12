from src.conversation.action import Action
from src.config.types.config_value import ConfigValue, ConfigValueTag
from src.config.types.config_value_bool import ConfigValueBool
from src.config.types.config_value_int import ConfigValueInt
from src.config.types.config_value_string import ConfigValueString
from src.config.types.config_value_selection import ConfigValueSelection
from src.config.types.config_value_multi_selection import ConfigValueMultiSelection


class OtherDefinitions:
    @staticmethod
    def get_show_first_time_setup_config_value() -> ConfigValue:
        return ConfigValueBool("first_time_setup","","Show Setup Guide on Startup",True, [], True)
    
    @staticmethod
    def get_automatic_greeting_config_value() -> ConfigValue:
        automatic_greeting_description = """Should a conversation be started with an automatic greeting from the LLM / NPC.
                                        - If enabled: Conversations are always started by the LLM.
                                        - If disabled: The LLM will not respond until the player speaks first."""
        return ConfigValueBool("automatic_greeting","Automatic Greeting",automatic_greeting_description,True)
    
    #Conversation        
    @staticmethod
    def get_active_actions(actions: list[Action]) -> ConfigValue:
        description = "The actions Mantella will provide."
        default_value:list[str] = [a.name for a in actions]
        return ConfigValueMultiSelection("active_actions","Actions",description, default_value, default_value)
    
    @staticmethod
    def get_max_count_events_config_value() -> ConfigValue:
        max_count_events_description = """Maximum number of in-game events that are sent to the LLM per player message. 
                                    If the maximum number is reached, the oldest events will be dropped.
                                    Increasing this number will cost more prompt tokens and lead to the context limit being reached faster."""
        return ConfigValueInt("max_count_events","Max Count Events",max_count_events_description,5,0,999999,tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    @staticmethod
    def get_events_refresh_time_config_value() -> ConfigValue:
        max_count_events_description = """Determines how much time (in seconds) can pass between the last NPC's response and the player's input before in-game events need to be refreshed.
                                        Note that updating in-game events increases response times. If the player responds before this set number in seconds, response times will be reduced.
                                        Increase this value to allow more time for the player to respond before events need to be refreshed. Decrease this value to make in-game events more up to date."""
        return ConfigValueInt("events_refresh_time","Time to Wait before Updating Events",max_count_events_description,10,0,999999,tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    @staticmethod
    def get_hourly_time_config_value() -> ConfigValue:
        description = """If enabled, NPCs will be made aware of the time every in-game hour. Otherwise, time updates will be less granular (eg 'The conversation now takes place in the morning' / 'at night' etc).
                        To remove mentions of the hour entirely, prompts also need to be edited from 'The time is {time} {time_group}.' to 'The conversation takes place {time_group}.'"""
        return ConfigValueBool("hourly_time","Report In-Game Time Hourly",description,False,tags=[ConfigValueTag.advanced])
    
    #Player Character
    @staticmethod
    def get_player_character_description() -> ConfigValue:
        player_character_description_description = """A description of your player character in-game. This is sent to the LLM as part of the prompt using the '{player_description}' variable.
                                                    This is not meant to be a bio but rather a description how the NPC(s) perceive the player character when they speak to them.
                                                    e.g. 'A tall man with long red hair.'
                                                    If the in-game MCM offers to set this option the text sent from the game takes precendence over this."""
        return ConfigValueString("player_character_description","Player Character Description",player_character_description_description,"",tags=[ConfigValueTag.advanced])

    @staticmethod
    def get_voice_player_input() -> ConfigValue:
        voice_player_input_description = """Should the input of the player (both by text or voice) be spoken by the player character in-game?
                                            Can be used for immersion or to fill the initial gap between input and reply.
                                            Use the 'Player Voice Model' setting to select the voice model of the TTS for the player character."""
        return ConfigValueBool("voice_player_input","Voice Player Input",voice_player_input_description,False,tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    @staticmethod
    def get_player_voice_model() -> ConfigValue:
        player_voice_model_description = """The voice model for the player character to use if 'Voice player input' is activated."""
        return ConfigValueString("player_voice_model","Player Voice Model",player_voice_model_description,"",tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    #HTTP
    @staticmethod
    def get_port_config_value() -> ConfigValue:
        return ConfigValueInt("port","Port","The port for the Mantella HTTP server to use.",4999, 0, 65535, tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    @staticmethod
    def get_show_http_debug_messages_config_value() -> ConfigValue:
        return ConfigValueBool("show_http_debug_messages","Show HTTP Debug Messages","Display the JSON going in and out of the server in Mantella.exe's log.", False, tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
    
    @staticmethod
    def get_advanced_logs_config_value() -> ConfigValue:
        description = """Save advanced logs to Mantella's My Games/Mantella/logging.log file.
                        Useful for troubleshooting issues."""
        return ConfigValueBool("advanced_logs", "Advanced Logs", description, False, tags=[ConfigValueTag.advanced])
    
    #Debugging
    @staticmethod
    def get_debugging_config_value() -> ConfigValue:
        return ConfigValueBool("debugging","Activate Debugging","Whether debugging is enabled.\nIf this is enabled, the values of all other variables in this section are ignored.", False, tags=[ConfigValueTag.advanced])
    
    @staticmethod
    def get_play_audio_from_script_config_value() -> ConfigValue:
        return ConfigValueBool("play_audio_from_script","Play Audio From Script","Whether to play the generated voicelines directly from the exe.\nEnable this value if testing Mantella while Skyrim is not running.", True, tags=[ConfigValueTag.advanced])
    
    @staticmethod
    def get_debugging_npc_config_value() -> ConfigValue:
        return ConfigValueString("debugging_npc","Debugging NPC","Selects the NPC to test.\nSet this value to None if you would instead prefer to select an NPC via the mod's spell / gun.", "Hulda", tags=[ConfigValueTag.advanced])
    
    @staticmethod
    def get_use_default_player_response_config_value() -> ConfigValue:
        description = """Whether a default response is sent on the player's behalf (good for quickly testing if Mantella works without player input).
                        When this value is enabled, the sentence contained in default_player_response (see below) will be repeatedly sent to the LLM.
                        When this value is disabled, allows you to use mic / text input (depending on microphone_enabled setting)."""
        return ConfigValueBool("use_default_player_response","Use Default Player Response",description, False, tags=[ConfigValueTag.advanced])
    
    @staticmethod
    def get_default_player_response_config_value() -> ConfigValue:
        return ConfigValueString("default_player_response","Default Player Response","The default text sent to the LLM if 'Use Default Player Response' is enabled.", "Can you tell me something about yourself?", tags=[ConfigValueTag.advanced])
    
    @staticmethod
    def get_exit_on_first_exchange_config_value() -> ConfigValue:
        description = """Whether to end the conversation after the first back and forth exchange.
                        Enable this value if testing conversation saving on exit functionality."""
        return ConfigValueBool("exit_on_first_exchange","Exit on First Exchange",description, False, tags=[ConfigValueTag.advanced])
    
    @staticmethod
    def get_save_audio_data_to_character_folder_config_value() -> ConfigValue:
        description = """Whether to save audio data to an NPC's voice folder instead of MantellaVoice00.
                        Enable this value if voicelines are not being played in-game."""
        return ConfigValueBool("save_audio_data_to_character_folder", "Save Game Audio to Character Folder", description, False, tags=[ConfigValueTag.advanced])
    
    @staticmethod
    def get_hot_swap_enabled_config_value() -> ConfigValue:
        description = """Whether to enable hot-swapping settings during active conversations.
                        If enabled: Settings changes (LLM model settings, prompts) will be applied without ending conversations.
                        If disabled: Settings changes will end the current conversation and restart (classic behavior)."""
        return ConfigValueBool("hot_swap_enabled", "Enable Hot-Swap Settings", description, True, tags=[ConfigValueTag.advanced])
    
    @staticmethod
    def get_conversation_summary_enabled_config_value() -> ConfigValue:
        description = """Whether to generate and save conversation summaries when conversations end.
                        If enabled: Summaries will be generated and saved to help NPCs remember past conversations.
                        If disabled: No summaries will be generated, conversations will end without sending summary requests to the LLM."""
        return ConfigValueBool("conversation_summary_enabled", "Enable Conversation Summaries", description, True, tags=[ ConfigValueTag.share_row])

    @staticmethod
    def get_inner_monologue_enabled_config_value() -> ConfigValue:
        description = """Whether to generate and save a private inner monologue when a conversation summary is saved.
                        Requires Enable Conversation Summaries to be on. If summaries are off, thoughts are not saved even if this is on.
                        If enabled: after a summary is written, the NPC also records a first-person private thought.
                        If disabled: summaries still save (when enabled), but no new private thoughts are written.
                        Whether saved thoughts appear in the next conversation prompt is controlled separately by Private Thoughts in Prompt."""
        return ConfigValueBool("inner_monologue_enabled", "Enable Private Thoughts", description, False, tags=[ ConfigValueTag.share_row])

    @staticmethod
    def get_inner_monologue_load_mode_config_value() -> ConfigValue:
        description = """Which private thoughts to include in the next conversation prompt.
                        This does not change whether new thoughts are saved — that is controlled by Enable Private Thoughts.
                        - Latest: include only the most recent private thought (default).
                        - None: do not include private thoughts in the prompt, even if thought files already exist."""
        return ConfigValueSelection(
            "inner_monologue_load_mode",
            "Private Thoughts in Prompt",
            description,
            "Latest",
            ["Latest", "None"],
            tags=[ConfigValueTag.advanced],
        )

    @staticmethod
    def get_random_llm_one_on_one_enabled_config_value() -> ConfigValue:
        return ConfigValueBool(
            identifier="random_llm_one_on_one_enabled",
            name="Enable Per-Conversation Random LLM Selection (One-on-One)",
            description="Enable random LLM selection from the one-on-one pool for each new one-on-one conversation.",
            default_value=False,
            tags=["random_llm", "conversation", ConfigValueTag.share_row, ConfigValueTag.advanced]
        )

    @staticmethod
    def get_random_llm_multi_npc_enabled_config_value() -> ConfigValue:
        return ConfigValueBool(
            identifier="random_llm_multi_npc_enabled",
            name="Enable Per-Conversation Random LLM Selection (Multi-NPC)",
            description="Enable random LLM selection from the multi-NPC pool for each new multi-NPC conversation.",
            default_value=False,
            tags=["random_llm", "conversation", ConfigValueTag.share_row, ConfigValueTag.advanced]
        )

    @staticmethod
    def get_random_llm_one_on_one_per_request_enabled_config_value() -> ConfigValue:
        return ConfigValueBool(
            identifier="random_llm_one_on_one_per_request_enabled",
            name="Enable Per-Request Random LLM (One-on-One)",
            description=(
                "When enabled, every request in one-on-one conversations uses a randomly selected LLM "
                "from the one-on-one pool. This overrides per-character LLM overrides for that request."
            ),
            default_value=False,
            tags=["random_llm", "conversation", ConfigValueTag.share_row],
            row_group="random_llm_per_request_row",
        )

    @staticmethod
    def get_random_llm_multi_npc_per_request_enabled_config_value() -> ConfigValue:
        return ConfigValueBool(
            identifier="random_llm_multi_npc_per_request_enabled",
            name="Enable Per-Request Random LLM (Multi-NPC)",
            description=(
                "When enabled, every request in multi-NPC conversations uses a randomly selected LLM "
                "from the multi-NPC pool. This overrides the per-conversation multi-NPC model selection."
            ),
            default_value=False,
            tags=["random_llm", "conversation", ConfigValueTag.share_row],
            row_group="random_llm_per_request_row",
        )

    @staticmethod
    def get_llm_pool_one_on_one_config_value() -> ConfigValue:
        return ConfigValueString(
            identifier="llm_pool_one_on_one",
            name="LLM Pool (One-on-One)",
            description="JSON array of LLM models for random selection in one-on-one conversations. Edit this JSON directly to manage your pool.\n\nExample format:\n[\n  {\"service\": \"OpenRouter\", \"model\": \"deepseek/deepseek-chat\"},\n  {\"service\": \"OpenRouter\", \"model\": \"anthropic/claude-3-haiku\"},\n  {\"service\": \"OpenAI\", \"model\": \"gpt-4o-mini\"},\n  {\"service\": \"NanoGPT\", \"model\": \"gpt-4\"}\n]",
            default_value="[]",
            tags=["random_llm", "conversation", "pool", ConfigValueTag.share_row],
            row_group="llm_pool_row",
        )

    @staticmethod
    def get_llm_pool_multi_npc_config_value() -> ConfigValue:
        return ConfigValueString(
            identifier="llm_pool_multi_npc",
            name="LLM Pool (Multi-NPC)",
            description="JSON array of LLM models for random selection in multi-NPC conversations. Edit this JSON directly to manage your pool.\n\nExample format:\n[\n  {\"service\": \"OpenRouter\", \"model\": \"meta-llama/llama-3.1-8b-instruct\"},\n  {\"service\": \"OpenRouter\", \"model\": \"anthropic/claude-3-sonnet\"},\n  {\"service\": \"NanoGPT\", \"model\": \"gpt-4o\"}\n]",
            default_value="[]",
            tags=["random_llm", "conversation", "pool", ConfigValueTag.share_row],
            row_group="llm_pool_row",
        )

    @staticmethod
    def get_reload_character_data_config_value() -> ConfigValue:
        description = """Reload character CSV files and overrides from disk to pick up any changes.
                        This refreshes character data that is cached in memory.
                        Note: If there is an active conversation, it will be ended before reloading."""
        return ConfigValueString("reload_character_data", "Reload Character Data", description, "")

    @staticmethod
    def get_save_summary_now_config_value() -> ConfigValue:
        description = """Trigger saving a conversation summary (and log) without ending the active conversation.
                        Useful if the game crashes and Mantella doesn't receive the normal end-conversation event.
                        Note: When the conversation later ends normally, another summary may be generated again."""
        return ConfigValueString("save_summary_now", "Save Summary Now", description, "")

    @staticmethod
    def get_save_inner_thoughts_now_config_value() -> ConfigValue:
        description = """Trigger saving private inner thoughts without ending the active conversation, and without writing a new summary.
                        Useful if you want to capture the NPC's current unspoken take on the talk so far.
                        Note: When the conversation later ends normally, another thought may be generated again."""
        return ConfigValueString("save_inner_thoughts_now", "Save Inner Thoughts Now", description, "")

    @staticmethod
    def get_real_world_timestamp_config_value() -> ConfigValue:
        description = """Display the current real-world date and time. Click 'Get Timestamp' to update the display with the current Unix timestamp (seconds since epoch)."""
        return ConfigValueString("real_world_timestamp", "Real World Timestamp", description, "")