import logging
from enum import Enum

from src.logs.log_detail import LogDetail

# Window colours (ANSI)
_WHITE = "\x1b[97m"
_GREY = "\x1b[37m"
_BLUE = "\x1b[94m"
_GREEN = "\x1b[92m"
_CYAN = "\x1b[96m"
_YELLOW = "\x1b[93m"
_MAGENTA = "\x1b[95m"
_DIM = "\x1b[2m"
LINE_WHITE = _WHITE # For a line with its own colour, see topic_logger.in_colour


class LogTopic(Enum):
    """The parts of Mantella the log is split into. The player picks Off, Short or Full for each, on the Other tab's "Log" part.
    Members are in the order the settings page shows them, so related topics sit together"""

    # key, label, window colour, default, what Short shows, what Full adds, and for some what Prompt and replies adds
    CONVERSATION = ("conversation", "Conversation", _WHITE, LogDetail.SHORT,
                    "Conversations starting and ending, who joins or leaves, and every spoken line.",
                    "Whose turn it is, and when a reply limit is reached.")
    MICROPHONE = ("microphone", "Microphone", _BLUE, LogDetail.SHORT,
                  "The mic turning on and off, and what Mantella heard you say.",
                  "Your words as you speak, when you start and stop talking, and speech recognition loading.")
    VOICES = ("voices", "Voices", _GREEN + _DIM, LogDetail.SHORT, # dim, so the green spoken lines stand out
              "The voice service starting, and which voice each NPC uses.",
              "Each line being voiced, how long it plays, and the voice effects used.")
    VOICE_CLIPS = ("voice_clips", "Voice clips", _GREEN + _DIM, LogDetail.SHORT,
                   "Clips being gathered to clone a voice, and how many each voice has.",
                   "Each clip picked and what it says.")
    AI_REPLIES = ("ai_replies", "LLM requests and replies", _CYAN, LogDetail.WITH_PROMPT,
                  "Each NPC reply: the model and settings used, how long it took, retries, and switching to the fallback model.",
                  "The whole request every turn (the prompt and the conversation history), and the whole reply.",
                  "The NPCs' prompt, once when a conversation starts and again when someone joins, and each reply exactly as the LLM wrote it, with its token count.")
    MODELS_AND_PROFILES = ("models_and_profiles", "LLM models and profiles", _CYAN + _DIM, LogDetail.SHORT,
                           "Which LLM was picked from your model list, which model profile was used, and profiles you add, change or delete.",
                           "Every setting in each profile.")
    GAME_MASTER = ("game_master", "Game Master", _YELLOW, LogDetail.SHORT,
                   "What the Game Master decided (who speaks next, who walks up to you, who joins, which event starts) and how it went.",
                   "The whole question asked to the Game Master and its whole answer.")
    ACTIONS = ("actions", "Actions", _MAGENTA, LogDetail.SHORT,
               "Each action an NPC takes, and actions that were refused.",
               "Why an action was refused, and its full details.")
    MEMORIES = ("memories", "Memories", _MAGENTA + _DIM, LogDetail.SHORT,
                "Summaries, private thoughts and personal reflections being saved.",
                "Their whole text, and the prompt that wrote them.")
    QUEST_TAGS = ("quest_tags", "Quest tags", _YELLOW + _DIM, LogDetail.SHORT,
                  "A quest outcome changing (like who holds Whiterun), and whether NPCs were told.",
                  "Quest conditions the game couldn't check, and other details.")
    INGAME_EVENTS = ("ingame_events", "In-game events", _GREY, LogDetail.SHORT,
                     "What NPCs are told happened each turn, like combat starting, an item picked up or a new location.",
                     "Repeated events that were merged or left out.")
    VANILLA_DIALOGUES = ("vanilla_dialogues", "Vanilla Dialogues", _GREY, LogDetail.SHORT,
                         "How many of Skyrim's own lines NPCs heard, how many reached the conversation, and lines forgotten after loading an older save.",
                         "Each line heard, and lines that couldn't be read.")
    VISION = ("vision", "Vision", _BLUE + _DIM, LogDetail.SHORT,
              "Screenshots being taken, and what the LLM saw in them.",
              "Screen and picture size, and the whole vision prompt.")
    CHARACTERS = ("characters", "Bios and lorebook", _WHITE + _DIM, LogDetail.SHORT,
                  "Where each NPC's bio came from, and lorebook entries added.",
                  "Which bio sections were used, and the bio templates loaded.")
    GAME_CONNECTION = ("game_connection", "Game connection", _BLUE, LogDetail.OFF,
                       "A line for each message the game and Mantella send each other.",
                       "What each message says.")
    SETTINGS_PAGE = ("settings_page", "Settings page", _GREY, LogDetail.SHORT,
                     "Settings saved, buttons pressed (like testing a voice) and prompt profiles edited.",
                     "Every value that changed.")
    STARTUP = ("startup", "Startup", _WHITE, LogDetail.SHORT,
               "Mantella's version, its folders, the game, and the settings page link.",
               "All your settings.")
    TIMINGS = ("timings", "Timings", _GREY + _DIM, LogDetail.OFF,
               "How long each reply took: the LLM, the voice, and in total.",
               "How long every step took.")

    def __init__(self, key: str, label: str, colour: str, default: LogDetail, short_text: str, full_text: str, prompt_text: str = "") -> None:
        self.key = key
        self.label = label
        self.colour = colour
        self.default = default
        self.short_text = short_text
        self.full_text = full_text
        self.prompt_text = prompt_text

    @property
    def details(self) -> list[LogDetail]:
        """The options the settings page offers. Prompt and replies only for topics that have a prompt text"""
        return [detail for detail in LogDetail if detail != LogDetail.WITH_PROMPT or self.prompt_text]

    @property
    def hides_warnings_when_off(self) -> bool:
        """Quest tags at Off hide their warnings too, and only show errors"""
        return self is LogTopic.QUEST_TAGS

    def level(self, detail: LogDetail) -> int:
        """The level the topic's logger is set to for the player's choice"""
        if detail == LogDetail.OFF and self.hides_warnings_when_off:
            return logging.ERROR
        return detail.level

    @property
    def setting_id(self) -> str:
        return f"log_{self.key}"


LogChoices = dict[LogTopic, LogDetail]


def default_log_choices() -> LogChoices:
    return {topic: topic.default for topic in LogTopic}
