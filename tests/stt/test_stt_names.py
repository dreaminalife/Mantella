"""The names sent with every recording: the Speech-to-Text Names prompt, the hint Whisper can read whole, and the safety check"""
import numpy as np

from src.config.definitions.prompt_definitions import PromptDefinitions
from src.stt_backends import names
from src.stt_backends.names import build, count_tokens, fill, hint_for, is_hint_copy, split_names
from tests.stt.fake_stt_server import FakeSTTServer, key_folders # noqa: F401 (a fixture)

DEFAULT = PromptDefinitions.STT_NAMES_PROMPT


def test_the_default_list_most_likely_first():
    mic = build(DEFAULT, ["Balgruuf the Greater", "Proventus Avenicci"], ["Irileth", "Farengar Secret-Fire"], "Dragonsreach", "Dovahkiin")
    assert mic.names == ["Balgruuf the Greater", "Proventus Avenicci", "Irileth", "Farengar Secret-Fire", "Dragonsreach", "Dovahkiin"]
    assert mic.hint == "Names: Balgruuf the Greater, Proventus Avenicci, Irileth, Farengar Secret-Fire, Dragonsreach, Dovahkiin."


def test_catch_first_names_sends_the_single_words_after_the_full_names():
    mic = build(DEFAULT, ["Balgruuf the Greater", "Proventus Avenicci"], ["Farengar Secret-Fire"], "Dragonsreach", "Dovahkiin", first_names=True)
    assert mic.names == ["Balgruuf the Greater", "Proventus Avenicci", "Farengar Secret-Fire", "Dragonsreach", "Dovahkiin",
                         "Balgruuf", "Proventus", "Avenicci", "Farengar", "Secret-Fire"]


def test_first_names_are_listened_for_on_their_own():
    mic = build(DEFAULT, ["Faendal"], ["Camilla Valerius", "Whiterun Guard", "Njordunn the Fox"], "Whiterun", "Yuji", first_names=True)
    assert "Camilla" in mic.names and "Valerius" in mic.names and "Njordunn" in mic.names
    assert not {"Guard", "Fox", "the"} & set(mic.names) # everyday words would be written where the player said something else
    assert mic.hint == "Names: Faendal, Camilla Valerius, Whiterun Guard, Njordunn the Fox, Whiterun, Yuji." # Whisper keeps the full names


def test_single_words_are_the_first_left_out():
    many = [f"Firstname{i} Lastname{i}" for i in range(40)]
    mic = build("{names}", many, [], "", "", first_names=True)
    assert mic.names == many[:len(mic.names)] # only full names fit


def test_a_word_already_listed_is_not_sent_twice():
    mic = build(DEFAULT, ["Lydia"], ["Lydia Housecarl", "Whiterun Guard"], "Whiterun", "", first_names=True)
    assert mic.names == ["Lydia", "Lydia Housecarl", "Whiterun Guard", "Whiterun"]


def test_nobody_nearby_leaves_no_gaps():
    mic = build(DEFAULT, ["Lydia"], [], "Whiterun", "")
    assert mic.names == ["Lydia", "Whiterun"]
    assert mic.hint == "Names: Lydia, Whiterun."


def test_names_the_player_added_and_pieces_that_are_not_names():
    text = fill(DEFAULT + ", Ulfric, Lydia, this is a sentence someone typed by mistake", ["Lydia"], [], "Whiterun", "")
    assert split_names(text) == ["Lydia", "Whiterun", "Ulfric"] # each name once, a long piece left out


def test_an_empty_prompt_sends_no_names():
    mic = build("", ["Lydia"], ["Hulda"], "Whiterun", "Dovahkiin")
    assert mic.hint == "" and mic.names == []


def test_whisper_counts_the_hint_with_its_own_tokenizer():
    assert names._load_tokenizer() is not None # the tokenizer ships in data/
    hint = "Names: Balgruuf, Proventus, Dragonsreach."
    assert 5 < count_tokens(hint) < len(hint.encode("utf-8"))


def test_a_long_english_list_fits_whole():
    skyrim = ["Balgruuf the Greater", "Proventus Avenicci", "Irileth", "Farengar Secret-Fire", "Hrongar", "Nelkir", "Dagny", "Frothar",
              "Lydia", "Adrianne Avenicci", "Ulfberth War-Bear", "Belethor", "Carlotta Valentia", "Mikael", "Ysolda", "Heimskr", "Jon Battle-Born",
              "Olfina Gray-Mane", "Vilkas", "Farkas", "Aela the Huntress", "Kodlak Whitemane", "Eorlund Gray-Mane", "Danica Pure-Spring",
              "Arcadia", "Amren", "Saadia", "Hulda", "Dragonsreach", "Jorrvaskr", "Whiterun", "Dovahkiin"]
    hint = hint_for(skyrim)
    assert hint.endswith("Dovahkiin.") # about 170 tokens: nothing left out
    assert count_tokens(hint) <= names.MAX_HINT_TOKENS


def test_a_long_russian_list_drops_the_least_likely_names():
    russian = ["Балгруф Великий", "Провентус Авеницци", "Ирилет", "Фаренгар Тайный Огонь", "Хронгар", "Нелкир", "Дагни", "Фротар",
               "Лидия", "Адрианна Авеницци", "Ульфберт Боевой Медведь", "Белетор", "Карлотта Валенция", "Микаэль", "Исольда", "Хеймскр",
               "Йон Сын Битвы", "Олфина Серая Грива", "Вилкас", "Фаркас", "Эйла Охотница", "Кодлак Белая Грива", "Драконий Предел", "Вайтран"]
    russian += [name + " Младший" for name in russian] # about 390 tokens in all: twice what Whisper reads
    hint = hint_for(russian)
    assert count_tokens(hint) <= names.MAX_HINT_TOKENS # Whisper reads all of it
    assert hint.startswith("Names: Балгруф Великий, Провентус Авеницци") # the most likely stay
    assert "Вайтран Младший" not in hint # the least likely, at the end, go


def test_words_to_listen_for_keep_the_500_character_cap():
    many = [f"Name number {i}" for i in range(80)]
    mic = build("{names}", many, [], "", "")
    assert len(", ".join(mic.names)) <= 500
    assert mic.names == many[:len(mic.names)]


def test_the_hint_given_back_for_silence_is_thrown_away():
    hint = "Names: Lydia, Whiterun."
    assert is_hint_copy("Names: Lydia, Whiterun.", hint)
    assert is_hint_copy(" names lydia whiterun", hint)
    assert is_hint_copy("Lydia, Whiterun.", hint)
    assert not is_hint_copy("Lydia.", hint) # the player can say a single name
    assert not is_hint_copy("Lydia, follow me to Whiterun.", hint)
    assert not is_hint_copy("Names: Lydia, Whiterun.", "")


def test_part_of_the_hint_or_the_hint_twice_is_thrown_away():
    hint = "Names: Lydia, Whiterun, Dovahkiin."
    assert is_hint_copy("Names: Lydia.", hint)
    assert is_hint_copy("Names: Lydia, Whiterun, Dovahkiin. Names: Lydia, Whiterun, Dovahkiin.", hint)
    assert not is_hint_copy("Names: Lydia, follow me.", hint) # words that are not in the hint: something the player said
    assert not is_hint_copy("Lydia, Whiterun.", hint) # part of the list without "Names:" can be a real line


def test_a_single_name_is_kept_even_when_it_is_the_whole_list():
    assert not is_hint_copy("Lydia.", "Names: Lydia.")
    assert is_hint_copy("Names: Lydia.", "Names: Lydia.")


def test_the_transcriber_drops_a_hint_copy(key_folders):
    from src.stt import Transcriber
    from tests.stt.test_stt_formats import stt_config
    audio = np.zeros(16000, dtype=np.float32)
    with FakeSTTServer(text="Names: Lydia, Whiterun.") as server:
        transcriber = Transcriber(stt_config(stt_service="Custom Service", stt_custom_address=server.base + "/v1"), "STT_SECRET_KEY.txt", "GPT_SECRET_KEY.txt")
        transcriber.prompt = "Names: Lydia, Whiterun."
        transcriber.names = ["Lydia", "Whiterun"]
        assert transcriber._transcribe(audio) == ""
    assert server.requests[0]["fields"]["prompt"] == b"Names: Lydia, Whiterun."


def test_options_without_names_keep_a_line_that_matches_the_list(key_folders):
    # Moonshine and Parakeet are never sent the names, so they can't copy them back: a matching line is what the player said
    from src.stt import Transcriber
    from tests.stt.test_stt_formats import stt_config
    with FakeSTTServer(text="Lydia, Whiterun.") as server:
        transcriber = Transcriber(stt_config(stt_service="Custom Service", stt_custom_address=server.base + "/v1"), "STT_SECRET_KEY.txt", "GPT_SECRET_KEY.txt")
        transcriber._Transcriber__backend.takes_hint = False
        transcriber.prompt = "Names: Lydia, Whiterun."
        assert transcriber._transcribe(np.zeros(16000, dtype=np.float32)) == "Lydia, Whiterun."


def test_the_prompt_is_on_the_prompts_tab_with_its_variables(tmp_path):
    from src.config.config_loader import ConfigLoader
    config = ConfigLoader(str(tmp_path), prompt_profiles_path=str(tmp_path / "prompt_profiles.json"))
    assert config.stt_names_prompt == DEFAULT
    checker = config.definitions.get_config_value_definition("stt_names_prompt")
    assert checker.does_value_cause_error("{names}, {location}, Ulfric").is_success
    assert not checker.does_value_cause_error("{names}, {bios}").is_success
