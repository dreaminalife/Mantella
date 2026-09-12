import logging
from src.llm.sentence_content import SentenceContent


class drop_last_sentences_buffer:
    """Holds the last N NPC sentences of a response so they can be discarded.

    Sentences are emitted in order once the buffer exceeds N. At the end of
    generation, remaining held sentences are dropped, except that at least one
    spoken sentence is always kept.
    """

    def __init__(self, drop_count: int) -> None:
        self.__drop_count = max(0, drop_count)
        self.__held: list[SentenceContent] = []
        self.__emitted_count = 0

    def push(self, content: SentenceContent) -> SentenceContent | None:
        """Hold content when dropping is enabled. Return a sentence ready to emit, or None."""
        if self.__drop_count <= 0 or content.is_system_generated_sentence:
            if not content.is_system_generated_sentence:
                self.__emitted_count += 1
            return content
        self.__held.append(content)
        if len(self.__held) > self.__drop_count:
            self.__emitted_count += 1
            return self.__held.pop(0)
        return None

    def flush(self) -> list[SentenceContent]:
        """Drop held sentences, keeping one if nothing has been emitted yet."""
        to_emit: list[SentenceContent] = []
        if self.__emitted_count == 0 and self.__held:
            to_emit.append(self.__held.pop(0))
            self.__emitted_count += 1
        if self.__held:
            for sentence in self.__held:
                speaker_name = sentence.speaker.name if sentence.speaker else "NPC"
                logging.log(28, f"Dropped last sentence from {speaker_name} (not spoken, not saved to history): {sentence.text.strip()}")
        self.__held.clear()
        return to_emit
