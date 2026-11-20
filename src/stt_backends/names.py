"""The names the player is likely to say, sent with every recording so speech-to-text spells them right.
One editable prompt (Speech-to-Text Names) makes a plain list with commas between the names. Options that take a hint get it as one line
after "Names:"; options that take words to listen for get each name on its own"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from src import utils
from src.stt_backends.cleanup import names_within

HINT_START = "Names: "
MAX_HINT_TOKENS = 200 # Whisper (on the PC, and online at OpenAI and Groq) only reads the last ~224 tokens of a hint and silently drops the start
MAX_WORDS_PER_NAME = 5 # a longer piece is a sentence someone typed, not a name
MIN_PART_LETTERS = 3 # a single word from a longer name shorter than this is not worth listening for
# Words found in Skyrim names and titles that players also say as plain words. Not listened for on their own (see name_parts)
EVERYDAY_WORDS = {
    # people and titles
    "guard", "guards", "soldier", "captain", "jarl", "steward", "housecarl", "court", "wizard", "mage", "priest", "priestess",
    "bandit", "chief", "chieftain", "hunter", "huntress", "merchant", "trader", "farmer", "miner", "smith", "blacksmith", "courier",
    "beggar", "bard", "innkeeper", "thug", "vampire", "dragon", "legate", "general", "commander", "officer", "prisoner", "citizen",
    "child", "boy", "girl", "man", "woman", "lady", "lord", "king", "queen", "prince", "princess", "brother", "sister", "mother",
    "father", "son", "daughter", "elder", "master", "apprentice", "servant", "maid", "cook", "fisherman", "lumberjack", "assassin",
    "thief", "warrior", "knight", "sergeant", "scout", "sentry", "archer", "necromancer", "conjurer", "witch", "imperial", "orc",
    "nord", "elf", # short, so easily heard in "or", "nor", "self". Lore words like Stormcloak or Khajiit are kept: nobody says them by chance
    # words that describe someone
    "old", "young", "big", "little", "great", "greater", "kind", "cruel", "wise", "fair", "bold", "brave", "mad", "wild", "quiet",
    "red", "black", "white", "gray", "grey", "green", "blue", "brown", "golden", "silver", "iron", "stone", "steel",
    # animals
    "fox", "wolf", "bear", "hawk", "crow", "raven", "horse", "mare", "snake", "spider", "rat", "cat", "dog",
    # places
    "house", "hall", "inn", "store", "shop", "tower", "temple", "keep", "castle", "mine", "mill", "farm", "camp", "fort", "hold",
    "city", "town", "village", "cave", "ruins", "sanctuary", "college", "palace", "market", "district", "square", "gate", "bridge",
    "road", "docks", "shack", "lodge", "barracks", "jail", "prison", "tomb", "barrow", "home", "manor", "east", "west",
    "north", "south", "upper", "lower", "new", "outside", "inside", "the", "and",
}
TOKENIZER_FILE =os.path.join("data", "stt_whisper_tokenizer.json") # Whisper's own (multilingual) tokenizer, to count the hint as Whisper does

_tokenizer = None
_tokenizer_failed = False


@dataclass
class MicNames:
    """The names for one recording: the hint line and the same names one by one"""
    hint: str = ""
    names: list[str] = field(default_factory=list)


def fill(prompt: str, names: list[str], nearby_names: list[str], location: str, player_name: str) -> str:
    """The Speech-to-Text Names prompt with its variables filled in: each variable becomes names with commas between them"""
    values = {
        "{names}": ", ".join(names),
        "{nearby_names}": ", ".join(nearby_names),
        "{location}": location or "",
        "{player_name}": player_name or "",
    }
    text = prompt or ""
    for variable, value in values.items():
        text = text.replace(variable, value)
    return text


def split_names(text: str) -> list[str]:
    """The names in a filled prompt, in order, each once: the pieces between commas (or lines), leaving out empty ones and pieces too long to be a name"""
    found: list[str] = []
    seen: set[str] = set()
    for piece in (text or "").replace("\n", ",").replace(";", ",").split(","):
        name = piece.strip().strip(".").strip()
        if not name or len(name.split()) > MAX_WORDS_PER_NAME or name.lower() in seen:
            continue
        found.append(name)
        seen.add(name.lower())
    return found


def _load_tokenizer():
    global _tokenizer, _tokenizer_failed
    if _tokenizer is None and not _tokenizer_failed:
        try:
            from tokenizers import Tokenizer
            _tokenizer = Tokenizer.from_file(os.path.join(utils.resolve_path(), TOKENIZER_FILE))
        except Exception as e:
            _tokenizer_failed = True
            logging.warning(f"Could not load Whisper's tokenizer ({e}). The speech-to-text names are counted by their size instead.")
    return _tokenizer


def count_tokens(text: str) -> int:
    """How many tokens Whisper counts in a hint. Without its tokenizer: the number of UTF-8 bytes, which is never fewer"""
    tokenizer = _load_tokenizer()
    if tokenizer is None:
        return len(text.encode("utf-8"))
    return len(tokenizer.encode(" " + text.strip(), add_special_tokens=False).ids) # faster-whisper adds the space too


def hint_for(names: list[str], max_tokens: int = MAX_HINT_TOKENS) -> str:
    """'Names: A, B, C.' with the least likely names (at the end) left out until Whisper can read all of it"""
    kept = list(names)
    while kept:
        hint = HINT_START + ", ".join(kept) + "."
        if count_tokens(hint) <= max_tokens:
            return hint
        kept.pop()
    return ""


def name_parts(names: list[str]) -> list[str]:
    """The single words of the longer names, in the same order: "Camilla Valerius" also gives "Camilla" and "Valerius".
    Players mostly say first names, and a service listening for "Camilla Valerius" does not catch a lone "Camilla".
    Leaves out words that start with a small letter ("the") and everyday words ("Guard", "Fox"): a service listening for those
    would write them where the player said something else"""
    parts: list[str] = []
    for name in names:
        words = name.split()
        if len(words) < 2:
            continue
        for word in words:
            word = word.strip(".,'\"()")
            if len(word) >= MIN_PART_LETTERS and not word[0].islower() and word.lower() not in EVERYDAY_WORDS:
                parts.append(word)
    return parts


def build(prompt: str, names: list[str], nearby_names: list[str], location: str, player_name: str, first_names: bool = False) -> MicNames:
    """The hint and the names to listen for, from the Speech-to-Text Names prompt.
    *first_names* (Catch First Names): the names to listen for also get the single words of the longer names, after all the full names,
    so they are the first left out when the list is too long. The hint keeps only the full names: Whisper reads it as a sentence"""
    listed = split_names(fill(prompt, names, nearby_names, location, player_name))
    return MicNames(hint_for(listed), names_within(listed + name_parts(listed) if first_names else listed))


def _plain(text: str) -> str:
    return utils.clean_text(text or "") # lower case, no punctuation, single spaces


def is_hint_copy(text: str, hint: str) -> bool:
    """Whisper sometimes answers silence with the hint itself, e.g. "Names: Lydia, Whiterun." That is not something the player said.
    Also part of it ("Names: Lydia.") or the hint twice, and the whole list without "Names:" when it has more than one name"""
    if not text or not hint:
        return False
    plain_text = _plain(text)
    plain_hint = _plain(hint)
    if not plain_text:
        return False
    if plain_text == plain_hint:
        return True
    if not hint.startswith(HINT_START):
        return False
    words = plain_text.split()
    if words[0] == _plain(HINT_START) and set(words) <= set(plain_hint.split()): # players don't start a line with "Names"
        return True
    listed = [name for name in hint[len(HINT_START):].rstrip(".").split(", ") if name]
    return len(listed) > 1 and plain_text == _plain(", ".join(listed)) # a single name is something the player may really say
