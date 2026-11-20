"""Clean-up every speech-to-text option shares: throw away phrases Whisper makes up in silence, and end lines with a full stop"""
from __future__ import annotations

from src import utils

# Whisper models learned from YouTube videos and sometimes "hear" these in silence or noise
MADE_UP_PHRASES = {'', 'thank you', 'thank you for watching', 'thanks for watching', 'the transcript is from the', 'the', 'thank you very much',
                   "thank you for watching and i'll see you in the next video", "we'll see you in the next video", 'see you next time'}

SENTENCE_ENDINGS = {'.', '?', '!', ':', ';', '。'}
NAMES_CAP = 500 # characters, for the lists of words to listen for


def names_within(names: list[str], cap: int = NAMES_CAP) -> list[str]:
    """The names in order, each once, stopping before the list (joined by ", ") would pass *cap* characters. The most likely come first"""
    kept: list[str] = []
    seen: set[str] = set()
    length = 0
    for name in names or []:
        name = (name or "").strip()
        if not name or name.lower() in seen:
            continue
        added = len(name) + (2 if kept else 0)
        if length + added > cap:
            break
        kept.append(name)
        seen.add(name.lower())
        length += added
    return kept


def is_made_up(text: str) -> bool:
    return utils.clean_text(text or "") in MADE_UP_PHRASES


def clean(text: str) -> str:
    """The text as the player's line, or '' when it is a phrase made up in silence"""
    text = (text or "").strip()
    if is_made_up(text):
        return ""
    return text


def ensure_sentence_ending(text: str) -> str:
    '''Moonshine transcriptions tend to be missing sentence-ending characters, which can confuse LLMs'''
    if not text:
        return text
    if text[-1] == ',':
        return text[:-1] + '.'
    if text[-1] not in SENTENCE_ENDINGS:
        return text + '.'
    return text
