from unittest.mock import MagicMock
from src.llm.output.drop_last_sentences import drop_last_sentences_buffer
from src.llm.sentence_content import SentenceContent, SentenceTypeEnum


def make_sentence(text: str, is_system: bool = False) -> SentenceContent:
    return SentenceContent(MagicMock(), text, SentenceTypeEnum.SPEECH, is_system)


def test_drop_zero_emits_immediately():
    buffer = drop_last_sentences_buffer(0)
    sentence = make_sentence("One.")
    assert buffer.push(sentence) is sentence
    assert buffer.flush() == []


def test_drop_one_emits_all_but_last():
    buffer = drop_last_sentences_buffer(1)
    first = make_sentence("One.")
    second = make_sentence("Two.")
    third = make_sentence("Three.")
    assert buffer.push(first) is None
    assert buffer.push(second) is first
    assert buffer.push(third) is second
    assert buffer.flush() == []


def test_drop_one_keeps_single_sentence():
    buffer = drop_last_sentences_buffer(1)
    only = make_sentence("Only.")
    assert buffer.push(only) is None
    assert buffer.flush() == [only]


def test_drop_three_with_two_sentences_keeps_one():
    buffer = drop_last_sentences_buffer(3)
    first = make_sentence("One.")
    second = make_sentence("Two.")
    assert buffer.push(first) is None
    assert buffer.push(second) is None
    assert buffer.flush() == [first]


def test_system_sentences_bypass_the_buffer():
    buffer = drop_last_sentences_buffer(1)
    system_sentence = make_sentence("I can't find the right words at the moment.", True)
    spoken = make_sentence("Hello.")
    assert buffer.push(system_sentence) is system_sentence
    assert buffer.push(spoken) is None
    assert buffer.flush() == [spoken]
