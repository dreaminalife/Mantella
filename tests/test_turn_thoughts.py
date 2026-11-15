from unittest.mock import MagicMock

from src.config.definitions.llm_definitions import NarrationIndicatorsEnum
from src.llm.message_thread import message_thread
from src.llm.messages import AssistantMessage, UserMessage
from src.llm.output.output_parser import sentence_generation_settings
from src.llm.output.sentence_end_parser import sentence_end_parser
from src.llm.output.sentence_length_parser import sentence_length_parser
from src.llm.output.thought_parser import thought_parser
from src.llm.output.max_count_sentences_parser import max_count_sentences_parser
from src.llm.sentence import Sentence
from src.llm.sentence_content import SentenceContent, SentenceTypeEnum


def npc(name: str, ref_id: str):
    character = MagicMock()
    character.name = name
    character.ref_id = ref_id
    character.is_player_character = False
    return character


def config():
    cfg = MagicMock()
    cfg.narration_indicators = NarrationIndicatorsEnum.PARANTHESES
    return cfg


def add_speech(message: AssistantMessage, speaker, text: str):
    message.add_sentence(Sentence(SentenceContent(speaker, text, SentenceTypeEnum.SPEECH), "", 0))


def add_thought(message: AssistantMessage, speaker, text: str):
    message.add_sentence(Sentence(SentenceContent(speaker, text, SentenceTypeEnum.THOUGHT), "", 0))


def test_thought_marker_is_removed_and_typed_as_thought():
    lydia = npc("Lydia", "lydia")
    parser = thought_parser([lydia])
    settings = sentence_generation_settings(lydia)
    end_parser = sentence_end_parser()

    speech, rest = parser.cut_sentence("I can spare a minute. <<thought Lydia>> He's worse than last time.", settings)
    assert speech is not None
    assert speech.sentence_type == SentenceTypeEnum.SPEECH
    assert "thought" not in speech.text

    _, rest = parser.cut_sentence(rest, settings)
    thought, _ = end_parser.cut_sentence(rest, settings)
    assert thought is not None
    assert thought.sentence_type == SentenceTypeEnum.THOUGHT
    assert thought.speaker is lydia
    assert thought.text.strip().startswith("He's worse")
    assert "<<" not in thought.text


def test_second_thought_for_the_same_npc_is_suppressed():
    lydia = npc("Lydia", "lydia")
    parser = thought_parser([lydia])
    settings = sentence_generation_settings(lydia)

    parser.cut_sentence("<<thought Lydia>> First.", settings)
    assert settings.suppress_thought is False
    parser.cut_sentence("<<thought Lydia>> Second.", settings)
    assert settings.suppress_thought is True


def test_formatted_reply_puts_one_named_thought_after_speech():
    lydia = npc("Lydia", "lydia")
    faendal = npc("Faendal", "faendal")
    message = AssistantMessage(config())
    message.is_multi_npc_message = True
    add_speech(message, lydia, "I'll watch the door.")
    add_speech(message, faendal, "We should leave.")
    add_speech(message, lydia, "Not yet.")
    add_thought(message, lydia, "He's hiding something.")
    add_thought(message, lydia, "I stay close.")
    add_thought(message, faendal, "She's right.")

    text = message.get_formatted_content()
    assert text.index("Not yet.") < text.index("<<thought Lydia>>")
    assert "<<thought Lydia>> He's hiding something. I stay close." in text
    assert "<<thought Faendal>> She's right." in text
    assert text.count("<<thought Lydia>>") == 1


def test_thought_for_npc_who_did_not_speak_is_omitted():
    lydia = npc("Lydia", "lydia")
    faendal = npc("Faendal", "faendal")
    message = AssistantMessage(config())
    message.is_multi_npc_message = True
    add_speech(message, lydia, "I'll watch the door.")
    add_thought(message, faendal, "I was never here.")

    assert "<<thought" not in message.get_formatted_content()


def _thread_with_three_replies(cfg, keep_count: int | None = None, live_chat: bool = False):
    if keep_count is not None:
        cfg.turn_thoughts_to_keep = keep_count
    lydia = npc("Lydia", "lydia")
    first = AssistantMessage(cfg)
    add_speech(first, lydia, "Hello.")
    add_thought(first, lydia, "First thought.")
    second = AssistantMessage(cfg)
    add_speech(second, lydia, "Still talking.")
    add_thought(second, lydia, "Second thought.")
    latest = AssistantMessage(cfg)
    add_speech(latest, lydia, "Still here.")
    add_thought(latest, lydia, "New thought.")
    thread = message_thread(cfg, "system", include_decensor_bypass=live_chat)
    thread.add_message(first)
    thread.add_message(UserMessage(cfg, "Go on.", "Prisoner"))
    thread.add_message(second)
    thread.add_message(UserMessage(cfg, "What now?", "Prisoner"))
    thread.add_message(latest)
    return thread


def test_chat_request_keeps_only_the_latest_turn_thoughts():
    lydia = npc("Lydia", "lydia")
    cfg = config()
    older = AssistantMessage(cfg)
    add_speech(older, lydia, "Hello.")
    add_thought(older, lydia, "Old thought.")
    latest = AssistantMessage(cfg)
    add_speech(latest, lydia, "Still here.")
    add_thought(latest, lydia, "New thought.")
    user = UserMessage(cfg, "What now?", "Prisoner")

    thread = message_thread(cfg, "system")
    thread.add_message(older)
    thread.add_message(user)
    thread.add_message(latest)

    chat = thread.get_openai_messages()
    assistant_contents = [m["content"] for m in chat if m["role"] == "assistant"]
    assert "Old thought" not in assistant_contents[0]
    assert "Hello." in assistant_contents[0]
    assert "<<thought Lydia>> New thought." in assistant_contents[1]

    full = message_thread.transform_to_openai_messages(thread.get_persistent_messages())
    full_assistant = [m["content"] for m in full if m["role"] == "assistant"]
    assert "<<thought Lydia>> Old thought." in full_assistant[0]
    assert "<<thought Lydia>> New thought." in full_assistant[1]


def test_live_chat_keeps_thoughts_on_the_last_n_assistant_replies():
    cfg = config()
    thread = _thread_with_three_replies(cfg, keep_count=2, live_chat=True)
    assistant_contents = [m["content"] for m in thread.get_openai_messages() if m["role"] == "assistant"]
    assert "First thought" not in assistant_contents[0]
    assert "Hello." in assistant_contents[0]
    assert "<<thought Lydia>> Second thought." in assistant_contents[1]
    assert "<<thought Lydia>> New thought." in assistant_contents[2]


def test_live_chat_sends_no_turn_thoughts_when_the_count_is_zero():
    cfg = config()
    thread = _thread_with_three_replies(cfg, keep_count=0, live_chat=True)
    assistant_contents = [m["content"] for m in thread.get_openai_messages() if m["role"] == "assistant"]
    assert all("thought" not in content.lower() for content in assistant_contents)
    assert "Hello." in assistant_contents[0]
    assert "Still here." in assistant_contents[2]


def test_live_chat_reads_a_new_turn_thought_count_on_the_next_request():
    cfg = config()
    thread = _thread_with_three_replies(cfg, keep_count=1, live_chat=True)
    first_pass = [m["content"] for m in thread.get_openai_messages() if m["role"] == "assistant"]
    assert "Second thought" not in first_pass[1]
    assert "<<thought Lydia>> New thought." in first_pass[2]

    cfg.turn_thoughts_to_keep = 3
    second_pass = [m["content"] for m in thread.get_openai_messages() if m["role"] == "assistant"]
    assert "<<thought Lydia>> First thought." in second_pass[0]
    assert "<<thought Lydia>> Second thought." in second_pass[1]
    assert "<<thought Lydia>> New thought." in second_pass[2]


def test_non_live_requests_keep_only_the_latest_turn_thought():
    cfg = config()
    thread = _thread_with_three_replies(cfg, keep_count=3, live_chat=False)
    assistant_contents = [m["content"] for m in thread.get_openai_messages() if m["role"] == "assistant"]
    assert "First thought" not in assistant_contents[0]
    assert "Second thought" not in assistant_contents[1]
    assert "<<thought Lydia>> New thought." in assistant_contents[2]


def test_first_name_matches_a_longer_npc_name():
    aela = npc("Aela the Huntress", "aela")
    parser = thought_parser([aela])
    settings = sentence_generation_settings(aela)
    _, rest = parser.cut_sentence("<<thought Aela>> Stay close.", settings)
    assert settings.suppress_thought is False
    assert settings.current_speaker is aela
    assert rest.strip().startswith("Stay close.")


def test_colon_inside_a_thought_does_not_switch_speaker():
    lydia = npc("Lydia", "lydia")
    faendal = npc("Faendal", "faendal")
    from src.llm.output.change_character_parser import change_character_parser
    characters = MagicMock()
    characters.get_all_characters.return_value = [lydia, faendal]
    speaker_parser = change_character_parser(characters)
    settings = sentence_generation_settings(lydia)
    settings.sentence_type = SentenceTypeEnum.THOUGHT
    sentence, rest = speaker_parser.cut_sentence("Don't trust Faendal: he is lying.", settings)
    assert sentence is None
    assert "Faendal:" in rest
    assert settings.sentence_type == SentenceTypeEnum.THOUGHT
    assert settings.current_speaker is lydia


def test_short_duplicate_thought_is_not_merged_into_the_first():
    lydia = npc("Lydia", "lydia")
    settings = sentence_generation_settings(lydia)
    settings.suppress_thought = True
    first = SentenceContent(lydia, "Hide.", SentenceTypeEnum.THOUGHT)
    duplicate = SentenceContent(lydia, "No.", SentenceTypeEnum.THOUGHT)
    ready, pending = sentence_length_parser(3).modify_sentence_content(duplicate, first, settings)
    assert ready is None
    assert pending is first
    assert pending.text == "Hide."


def test_sentence_cap_drops_later_speech_and_keeps_a_later_thought():
    lydia = npc("Lydia", "lydia")
    parser = max_count_sentences_parser(1, False, read_thoughts_after_cap=True)
    settings = sentence_generation_settings(lydia)
    speech = SentenceContent(lydia, "One.", SentenceTypeEnum.SPEECH)
    extra = SentenceContent(lydia, "Two.", SentenceTypeEnum.SPEECH)
    later = SentenceContent(lydia, "Three.", SentenceTypeEnum.SPEECH)
    thought = SentenceContent(lydia, "Secret.", SentenceTypeEnum.THOUGHT)

    kept, _ = parser.modify_sentence_content(speech, None, settings)
    assert kept is speech
    assert settings.speech_cap_reached is True
    assert settings.stop_generation is False

    assert parser.modify_sentence_content(extra, None, settings)[0] is None
    assert parser.modify_sentence_content(later, None, settings)[0] is None
    assert settings.stop_generation is False

    kept_thought, pending = parser.modify_sentence_content(thought, None, settings)
    assert kept_thought is thought
    assert pending is None
    assert settings.stop_generation is False


def test_sentence_cap_stops_the_stream_when_thoughts_are_not_exempt():
    lydia = npc("Lydia", "lydia")
    parser = max_count_sentences_parser(1, False, read_thoughts_after_cap=False)
    settings = sentence_generation_settings(lydia)
    speech = SentenceContent(lydia, "One.", SentenceTypeEnum.SPEECH)
    thought = SentenceContent(lydia, "Secret.", SentenceTypeEnum.THOUGHT)

    kept, _ = parser.modify_sentence_content(speech, None, settings)
    assert kept is speech
    assert settings.stop_generation is True

    settings.stop_generation = False
    thought_parser = max_count_sentences_parser(1, False, read_thoughts_after_cap=False)
    thought_settings = sentence_generation_settings(lydia)
    kept_thought, _ = thought_parser.modify_sentence_content(thought, None, thought_settings)
    assert kept_thought is thought
    assert thought_settings.stop_generation is True


def _game_master_round(sillne, sevinie):
    message = AssistantMessage(config())
    message.is_multi_npc_message = True
    message.thoughts_in_place = True
    add_speech(message, sillne, "Good morning, Yuji.")
    add_thought(message, sillne, "I must not let it show.")
    add_thought(message, sillne, "He must remember his place.")
    add_speech(message, sevinie, "Mmh, morning...")
    add_thought(message, sevinie, "He looks so crisp.")
    add_speech(message, sillne, "Do keep your hands to yourself.")
    add_thought(message, sillne, "That should do it.")
    return message


def test_game_master_round_puts_each_thought_after_its_own_turn():
    sillne, sevinie = npc("Sillne", "sillne"), npc("Sevinie", "sevinie")
    text = _game_master_round(sillne, sevinie).get_formatted_content()
    assert text == ("Sillne: Good morning, Yuji. <<thought Sillne>> I must not let it show. He must remember his place. "
                    "Sevinie: Mmh, morning... <<thought Sevinie>> He looks so crisp. "
                    "Sillne: Do keep your hands to yourself. <<thought Sillne>> That should do it.")


def test_game_master_round_keeps_only_the_requesting_npcs_thoughts():
    sillne, sevinie = npc("Sillne", "sillne"), npc("Sevinie", "sevinie")
    message = _game_master_round(sillne, sevinie)
    message.thoughts_only_of_ref_id = "sillne"
    text = message.get_formatted_content()
    assert "<<thought Sevinie>>" not in text
    assert text.index("Good morning, Yuji.") < text.index("<<thought Sillne>> I must not") < text.index("Sevinie: Mmh")
    assert "Sevinie: Mmh, morning... Sillne: Do keep" in text


def test_game_master_round_without_thoughts():
    sillne, sevinie = npc("Sillne", "sillne"), npc("Sevinie", "sevinie")
    text = _game_master_round(sillne, sevinie).get_formatted_content(include_thoughts=False)
    assert text == "Sillne: Good morning, Yuji. Sevinie: Mmh, morning... Sillne: Do keep your hands to yourself."


def test_thoughts_in_place_survives_save_and_load():
    from src.conversation.conversation_serializer import deserialize_persistent_messages, serialize_persistent_messages
    sillne, sevinie = npc("Sillne", "sillne"), npc("Sevinie", "sevinie")
    sillne.base_id, sevinie.base_id = "sillne", "sevinie"
    characters = {"sillne": sillne, "sevinie": sevinie}
    message = _game_master_round(sillne, sevinie)
    loaded = deserialize_persistent_messages(serialize_persistent_messages([message], config()), config(),
                                             lambda ref_id, name, base_id: characters.get(ref_id))
    assert loaded[0].thoughts_in_place is True
    assert loaded[0].get_formatted_content() == message.get_formatted_content()
