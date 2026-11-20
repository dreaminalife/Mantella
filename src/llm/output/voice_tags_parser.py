from typing import Callable

from src.character_manager import Character
from src.llm.output.output_parser import output_parser, sentence_generation_settings
from src.llm.sentence_content import SentenceContent, SentenceTypeEnum
from src.tts.voice_tags import NO_TAGS, VoiceTags, split


class voice_tags_parser(output_parser):
    """Takes the sounds and directions in square brackets ([laugh], [say angrily]) out of each sentence's text, so subtitles and
    memory never show them. A spoken sentence of a speaker whose voice service takes them keeps them in its voice_text.
    Only in the chain while an NPC of the reply speaks with such a service"""
    def __init__(self, tags_of: Callable[[Character], VoiceTags]) -> None:
        super().__init__()
        self.__tags_of = tags_of

    def cut_sentence(self, output: str, current_settings: sentence_generation_settings) -> tuple[SentenceContent | None, str]:
        return None, output

    def get_cut_indicators(self) -> list[str]:
        return []

    def modify_sentence_content(self, cut_content: SentenceContent, last_content: SentenceContent | None, settings: sentence_generation_settings) -> tuple[SentenceContent | None, SentenceContent | None]:
        if cut_content.sentence_type == SentenceTypeEnum.THOUGHT:
            return cut_content, last_content
        name = cut_content.speaker.name
        tags = self.__tags_of(cut_content.speaker) if cut_content.sentence_type == SentenceTypeEnum.SPEECH else NO_TAGS
        held = settings.held_voice_tags.pop(name, "") if tags.any else ""
        if "[" not in cut_content.text and not held:
            return cut_content, last_content
        clean, spoken = split(f"{held} {cut_content.text}" if held else cut_content.text, tags)
        if not clean.strip():
            # Only tags, like the [sigh] after "Fine.": they go in front of this speaker's next sentence
            if tags.any and spoken.strip():
                settings.held_voice_tags[name] = spoken.strip()
            return None, last_content
        cut_content.text = clean
        if tags.any:
            cut_content.voice_text = spoken
        return cut_content, last_content
