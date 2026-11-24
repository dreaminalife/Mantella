"""Lists of words to say differently, for the new voice services.

The lists live in the pronunciation folder next to config.ini:
- mantella_words.csv ships with Mantella and is replaced on every start, so updates bring the newest version
- my_words.csv is the player's own list; it is created empty and never overwritten
- any other .csv put there (someone's list for a mod's names) is read too
Load order: mantella_words.csv, the others alphabetically, my_words.csv last. For the same word, service and language a later entry wins.
Only what is spoken changes: never the subtitles, the transcript or what NPCs remember.
"""
from __future__ import annotations

import csv
import io
import locale
import os
import re
import shutil
import threading
from dataclasses import dataclass
from typing import Callable

from src import utils
from src.tts.registry import normalize_service_name
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.VOICES)

MANTELLA_LIST = "mantella_words.csv"
MY_LIST = "my_words.csv"
COLUMNS = ["word", "say_as", "kind", "service", "language"]
SPELLING = "spelling"
IPA = "ipa"


@dataclass(frozen=True)
class WordEntry:
    word: str
    say_as: str
    kind: str # SPELLING or IPA
    service: str # blank: every service
    language: str # blank: every language
    file_name: str
    order: int # later entries win

    @property
    def specificity(self) -> int:
        return (2 if self.service else 0) + (1 if self.language else 0)


def shipped_list_path() -> str:
    return os.path.join(utils.resolve_path(), "data", "pronunciation", MANTELLA_LIST)


def ensure_files(folder: str, shipped_list: str | None = None) -> None:
    """Copies Mantella's list in (replacing the old one) and makes the player's own list if it is missing"""
    os.makedirs(folder, exist_ok=True)
    shipped_list = shipped_list or shipped_list_path()
    if os.path.exists(shipped_list):
        try:
            shutil.copyfile(shipped_list, os.path.join(folder, MANTELLA_LIST))
        except OSError as e:
            log.warning(f"Could not copy Mantella's word list into {folder}: {e}")
    own_list = os.path.join(folder, MY_LIST)
    if not os.path.exists(own_list):
        with open(own_list, "w", encoding="utf-8", newline="") as f:
            f.write(",".join(COLUMNS) + "\n")


def _read_list_text(path: str) -> tuple[str, bool]:
    """The file's text and whether it was saved as UTF-8.
    Excel and Notepad often save a .csv in the computer's own text format instead of UTF-8 (a name with an accent then
    breaks a UTF-8 read), so that format is tried next, and cp1252 with unreadable letters replaced as the last resort"""
    with open(path, "rb") as f:
        data = f.read()
    try:
        return data.decode("utf-8-sig"), True
    except UnicodeDecodeError:
        pass
    for encoding in (locale.getpreferredencoding(False), "cp1252"):
        try:
            return data.decode(encoding), False
        except (UnicodeDecodeError, LookupError):
            pass
    return data.decode("cp1252", errors="replace"), False


def _read_rows(path: str) -> tuple[list[dict[str, str]], bool]:
    text, is_utf8 = _read_list_text(path)
    return list(csv.DictReader(io.StringIO(text, newline=""))), is_utf8


def _file_order(file_names: list[str]) -> list[str]:
    others = sorted((n for n in file_names if n.lower() not in (MANTELLA_LIST, MY_LIST)), key=str.lower)
    first = [n for n in file_names if n.lower() == MANTELLA_LIST]
    last = [n for n in file_names if n.lower() == MY_LIST]
    return first + others + last


class WordLists:
    def __init__(self, folder: str, shipped_list: str | None = None) -> None:
        self.__folder = folder
        self.__lock = threading.Lock()
        self.__state: tuple = ()
        self.__entries: list[WordEntry] = []
        self.__warned: set[tuple[str, int]] = set()
        ensure_files(folder, shipped_list)

    @property
    def folder(self) -> str:
        return self.__folder

    def entries(self) -> list[WordEntry]:
        """Every entry of every list, reloaded when a file changed"""
        with self.__lock:
            state = self.__current_state()
            if state != self.__state:
                self.__entries = self.__load(state)
                self.__state = state
            return list(self.__entries)

    def __current_state(self) -> tuple:
        try:
            names = [n for n in os.listdir(self.__folder) if n.lower().endswith(".csv")]
        except OSError:
            return ()
        state = []
        for name in _file_order(names):
            try:
                state.append((name, os.path.getmtime(os.path.join(self.__folder, name))))
            except OSError:
                pass
        return tuple(state)

    def __load(self, state: tuple) -> list[WordEntry]:
        entries: list[WordEntry] = []
        for name, _ in state:
            path = os.path.join(self.__folder, name)
            try:
                rows, is_utf8 = _read_rows(path)
            except (OSError, csv.Error) as e:
                log.warning(f"Could not read the word list {path}: {e}")
                continue
            if not is_utf8 and (name, 0) not in self.__warned:
                self.__warned.add((name, 0))
                log.warning(f"Word list {name} is not saved as UTF-8, so letters with accents may come out wrong. In Notepad or Excel, use Save As and pick UTF-8.")
            for row_number, row in enumerate(rows, start=2):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
                word, say_as = row.get("word", ""), row.get("say_as", "")
                kind = (row.get("kind", "") or SPELLING).lower()
                if not word or not say_as or kind not in (SPELLING, IPA):
                    if (name, row_number) not in self.__warned:
                        self.__warned.add((name, row_number))
                        log.warning(f"Word list {name}, row {row_number}: skipped, it needs a word, what to say instead, and a kind of 'spelling' or 'ipa'.")
                    continue
                entries.append(WordEntry(word, say_as, kind, row.get("service", ""), row.get("language", "").lower(), name, len(entries)))
        return entries

    def replacements(self, service_matches: Callable[[str], bool], language: str, accepts_ipa: bool,
                     format_ipa: Callable[[str, str], str] | None = None) -> dict[str, str]:
        """What to say instead, by word in lower case, for one service and language"""
        language = (language or "").lower()
        chosen: dict[tuple[str, str], WordEntry] = {}
        for entry in self.entries():
            if entry.service and not service_matches(entry.service):
                continue
            if entry.language and entry.language != language:
                continue
            key = (entry.word.lower(), entry.kind)
            current = chosen.get(key)
            if current is None or (entry.specificity, entry.order) >= (current.specificity, current.order):
                chosen[key] = entry
        result: dict[str, str] = {}
        for (word, kind), entry in chosen.items():
            if kind == SPELLING and word not in result:
                result[word] = entry.say_as
        if accepts_ipa:
            for (word, kind), entry in chosen.items():
                if kind == IPA:
                    result[word] = format_ipa(entry.word, entry.say_as) if format_ipa else entry.say_as
        return result

    def apply(self, text: str, service_matches: Callable[[str], bool], language: str, accepts_ipa: bool = False,
              format_ipa: Callable[[str, str], str] | None = None) -> str:
        return replace_words(text, self.replacements(service_matches, language, accepts_ipa, format_ipa))

    def add_to_my_list(self, word: str, say_as: str, kind: str = SPELLING, service: str = "", language: str = "") -> None:
        """Adds a row to my_words.csv, or changes the row for the same word, kind, service and language"""
        path = os.path.join(self.__folder, MY_LIST)
        rows: list[dict[str, str]] = []
        newline = "\n"
        if os.path.exists(path):
            with open(path, "rb") as f:
                newline = "\r\n" if b"\r\n" in f.read() else "\n"
            rows = [{k: (v or "") for k, v in row.items() if k is not None} for row in _read_rows(path)[0]] # saved back as UTF-8 below
        new_row = {"word": word.strip(), "say_as": say_as.strip(), "kind": kind, "service": service.strip(), "language": language.strip().lower()}
        same = lambda r: (r.get("word", "").lower(), (r.get("kind", "") or SPELLING).lower(), normalize_service_name(r.get("service", "")), r.get("language", "").lower()) == \
                         (new_row["word"].lower(), new_row["kind"], normalize_service_name(new_row["service"]), new_row["language"])
        for row in rows:
            if same(row):
                row.update(new_row)
                break
        else:
            rows.append(new_row)
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore", lineterminator=newline)
            writer.writeheader()
            writer.writerows(rows)


def replace_words(text: str, replacements: dict[str, str]) -> str:
    """Whole words, case does not matter, longest entry first (so 'Jarl Balgruuf' can beat 'Balgruuf'). A trailing 's stays attached"""
    if not replacements or not text:
        return text
    words = sorted(replacements.keys(), key=len, reverse=True)
    pattern = re.compile(r"(?<![\w])(" + "|".join(re.escape(w) for w in words) + r")(?![\w])", re.IGNORECASE)
    return pattern.sub(lambda m: replacements[m.group(1).lower()], text)


_shared: dict[str, WordLists] = {}
_shared_lock = threading.Lock()


def shared_word_lists(folder: str) -> WordLists:
    with _shared_lock:
        if folder not in _shared:
            _shared[folder] = WordLists(folder)
        return _shared[folder]
