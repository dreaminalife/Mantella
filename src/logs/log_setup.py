import logging
from abc import ABC, abstractmethod
from logging.handlers import RotatingFileHandler

from src.logs.log_formatters import FileFormatter, WindowFormatter
from src.logs.log_topic import LogChoices, LogTopic
from src.logs.topic_logger import get_prompt_logger, get_topic_logger

MAX_FILE_BYTES = 10 * 1024 * 1024 # Then the log is moved to logging.log.1 (replacing the previous one) and a new one is started
FILE_BACKUPS = 1
OTHER_PROGRAMS_LEVEL = logging.WARNING # The AI and web libraries Mantella uses only show their warnings and errors


def describe_choices(choices: LogChoices) -> str:
    return ", ".join(f"{topic.label} {choices[topic].value}" for topic in LogTopic if topic in choices)


class EarlyProblems(logging.Handler):
    """Before the log starts (while the settings load, which say what it shows): warnings and errors show in the window right away,
    and are kept, so logging.log gets them too once it starts"""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[logging.LogRecord] = []
        self.__window = logging.StreamHandler()
        self.__window.setFormatter(WindowFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        self.__window.handle(record)


class ILogSetup(ABC):
    """Mantella.exe's window and logging.log, and how much of each topic they show"""

    @abstractmethod
    def catch_early_problems(self) -> None:
        """Until start: warnings and errors show in the window, and reach logging.log when it starts"""

    @abstractmethod
    def start(self, file_path: str, version: str, choices: LogChoices) -> None:
        """Starts the window and file log, with the player's choices"""

    @abstractmethod
    def apply(self, choices: LogChoices) -> None:
        """The player changed what the log shows: applies it right away"""


class MantellaLogSetup(ILogSetup):
    def __init__(self, max_file_bytes: int = MAX_FILE_BYTES, file_backups: int = FILE_BACKUPS, other_programs_level: int = OTHER_PROGRAMS_LEVEL) -> None:
        self.__max_file_bytes = max_file_bytes
        self.__file_backups = file_backups
        self.__other_programs_level = other_programs_level
        self.__handlers: list[logging.Handler] = []
        self.__file_handler: logging.Handler | None = None
        self.__choices: LogChoices = {}
        self.__early_problems: EarlyProblems | None = None

    def catch_early_problems(self) -> None:
        if self.__early_problems is None:
            self.__early_problems = EarlyProblems()
            logging.getLogger().addHandler(self.__early_problems)

    def start(self, file_path: str, version: str, choices: LogChoices) -> None:
        root = logging.getLogger()
        for handler in self.__handlers:
            root.removeHandler(handler)
            handler.close()
        early_records: list[logging.LogRecord] = []
        if self.__early_problems is not None:
            root.removeHandler(self.__early_problems)
            early_records = self.__early_problems.records
            self.__early_problems = None

        window_handler = logging.StreamHandler()
        window_handler.setFormatter(WindowFormatter())
        file_handler = RotatingFileHandler(file_path, maxBytes=self.__max_file_bytes, backupCount=self.__file_backups, encoding='utf-8')
        file_handler.setFormatter(FileFormatter())
        self.__handlers = [window_handler, file_handler]
        self.__file_handler = file_handler
        for handler in self.__handlers:
            root.addHandler(handler)
        # Python checks a line's level only on the logger it was written to, so the topics' lines still reach these handlers
        root.setLevel(self.__other_programs_level)

        self.__set_levels(choices)
        self.__write_to_file_only(f"Mantella v{version}. Log: {describe_choices(choices)}")
        for record in early_records: # already shown in the window
            file_handler.handle(record)

    def apply(self, choices: LogChoices) -> None:
        if choices == self.__choices:
            return
        self.__set_levels(choices)
        self.__write_to_file_only(f"Log changed: {describe_choices(choices)}")

    def __set_levels(self, choices: LogChoices) -> None:
        self.__choices = dict(choices)
        for topic, detail in choices.items():
            get_topic_logger(topic).setLevel(topic.level(detail))
            if topic.prompt_text:
                get_prompt_logger(topic).setLevel(logging.INFO if detail.shows_prompt else logging.WARNING)

    def __write_to_file_only(self, text: str) -> None:
        """So a log sent with a bug report says what it was set to show, whatever Startup is set to"""
        if not self.__file_handler:
            return
        record = logging.LogRecord(get_topic_logger(LogTopic.STARTUP).name, logging.INFO, __file__, 0, text, None, None)
        self.__file_handler.handle(record)
