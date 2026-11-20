"""Sounds and directions in square brackets ([laugh], [say angrily]): what each voice service takes, what the prompt unlocks,
and how a spoken sentence is split into the text everyone sees and the text the voice service gets"""
from types import SimpleNamespace

from src.tts import inworld
from src.tts.voice_tags import NO_TAGS, VoiceTags, conditions_for, moods_text, sounds_text, split, tags_for

SOUNDS = VoiceTags(("laugh", "sigh", "clear throat"), False)
DIRECTIONS = VoiceTags((), True)
BOTH = VoiceTags(("laugh", "sigh", "clear throat"), True)


def npc(name: str, service: str = "", player: bool = False):
    return SimpleNamespace(name=name, tts_service=service, is_player_character=player)


def settings(service: str = "Inworld", audio_tags: bool = True, directions: bool = True, model: str = "TTS-2"):
    return SimpleNamespace(tts_service_name=service, inworld_audio_tags=audio_tags, inworld_directions=directions, inworld_model=model)


def test_a_line_without_tags_is_unchanged():
    assert split("Hello there, friend.", BOTH) == ("Hello there, friend.", "Hello there, friend.")
    assert split("Hello (waves).", NO_TAGS) == ("Hello (waves).", "Hello (waves).")


def test_sounds_stay_where_they_are_for_the_voice_only():
    assert split("Ha! [laugh] You thought so?", SOUNDS) == ("Ha! You thought so?", "Ha! [laugh] You thought so?")


def test_sounds_match_whatever_the_case_and_spacing():
    assert split("[Clear_Throat] Listen.", SOUNDS)[1] == "[Clear_Throat] Listen."


def test_a_sound_the_service_doesnt_make_is_left_out_and_never_taken_for_a_direction():
    assert split("[burp] Excuse me.", BOTH) == ("Excuse me.", "Excuse me.")


def test_sounds_are_left_out_while_audio_tags_is_off():
    assert split("[sigh] Fine.", DIRECTIONS) == ("Fine.", "Fine.")


def test_a_direction_changes_only_its_own_sentence():
    assert split("[say coldly] Take it.", DIRECTIONS) == ("Take it.", "[say coldly] Take it.")
    assert split("Go.", DIRECTIONS) == ("Go.", "Go.")


def test_a_new_direction_changes_the_rest_of_the_sentence():
    assert split("Get out! [whisper] Please.", BOTH) == ("Get out! Please.", "Get out! [whisper] Please.")


def test_a_reset_the_ai_writes_anyway_is_passed_on_never_taken_for_a_direction():
    assert split("[shout] Get out! [Reset] Please.", BOTH) == ("Get out! Please.", "[shout] Get out! [reset] Please.")


def test_directions_are_left_out_while_directions_is_off():
    assert split("[say angrily] Go away.", SOUNDS) == ("Go away.", "Go away.")


def test_a_voice_that_takes_nothing_gets_the_clean_line():
    assert split("[sigh] [say angrily] Fine.", NO_TAGS) == ("Fine.", "Fine.")


def test_a_sentence_of_only_tags_has_no_words():
    assert split("[sigh]", BOTH) == ("", "[sigh]")
    assert split("[say coldly]", BOTH) == ("", "[say coldly]") # put in front of the next sentence


def test_inworld_takes_sounds_and_directions_by_its_settings():
    assert inworld.voice_tags(settings(audio_tags=True, directions=True)) == VoiceTags(inworld.SOUNDS, True)
    assert inworld.voice_tags(settings(audio_tags=False, directions=False)) == NO_TAGS
    # TTS-2 Flash ignores directions, but makes sounds
    assert inworld.voice_tags(settings(model="TTS-2 Flash")) == VoiceTags(inworld.SOUNDS, False)


def test_a_character_gets_their_own_service_or_the_tts_service():
    config = settings(service="Pocket TTS")
    assert tags_for(config, npc("Lydia")) == NO_TAGS
    assert tags_for(config, npc("Serana", "inworld")).any
    assert tags_for(settings(), npc("Lydia")).any
    assert tags_for(settings(service="Piper"), npc("Lydia")) == NO_TAGS


def test_the_prompt_unlocks_what_one_of_the_npcs_can_use():
    config = settings(service="Pocket TTS", directions=False)
    group = [npc("Player", "inworld", player=True), npc("Lydia"), npc("Serana", "inworld")]
    assert conditions_for(config, group) == {"audio_tags"}
    assert sounds_text(config, group) == ", ".join(f"[{sound}]" for sound in inworld.SOUNDS)
    assert conditions_for(config, group[:2]) == set() # the player's own voice doesn't count
    assert conditions_for(settings(), [npc("Lydia")]) == {"audio_tags", "directions"}
    assert sounds_text(settings(audio_tags=False), [npc("Lydia")]) == ""
    assert moods_text(settings(), [npc("Lydia")]) == "" # Inworld has no moods


def test_heygen_and_boson_unlock_moods_and_list_them_apart_from_the_sounds():
    from src.tts import heygen
    config = SimpleNamespace(tts_service_name="Pocket TTS", heygen_audio_tags=True, inworld_audio_tags=True, inworld_directions=True,
                             inworld_model="TTS-2", language="en")
    group = [npc("Lydia", "heygen"), npc("Serana", "inworld")]
    assert conditions_for(config, group) == {"audio_tags", "moods", "directions"}
    assert "[angry]" in moods_text(config, group) and "[angry]" not in sounds_text(config, group)
    assert moods_text(config, group) == ", ".join(f"[{mood}]" for mood in heygen.MOODS)
    config.heygen_audio_tags = False
    assert conditions_for(config, group[:1]) == set()


def test_the_speaking_prompts_show_the_rules_only_when_unlocked():
    from src.config.definitions.prompt_definitions import PromptDefinitions
    for prompt_type in ("skyrim_prompt", "skyrim_multi_npc_prompt", "skyrim_multi_npc_director_prompt", "skyrim_npc_initiative_prompt",
                        "skyrim_radiant_prompt", "skyrim_game_master_npc_prompt"):
        text = next(getattr(PromptDefinitions, name)() for name in dir(PromptDefinitions)
                    if name.startswith("get_") and name.endswith("_config_value") and getattr(PromptDefinitions, name)().identifier == prompt_type).default_value
        locked = PromptDefinitions.apply_prompt_conditions(prompt_type, text, set())
        assert "[laugh]" not in locked and "[say angrily]" not in locked, prompt_type
        sounds = PromptDefinitions.apply_prompt_conditions(prompt_type, text, {"audio_tags"})
        assert "{voice_sounds}" in sounds and "[say angrily]" not in sounds, prompt_type
        directions = PromptDefinitions.apply_prompt_conditions(prompt_type, text, {"directions"})
        assert "[say angrily]" in directions and "{voice_sounds}" not in directions and "{voice_moods}" not in directions, prompt_type
        moods = PromptDefinitions.apply_prompt_conditions(prompt_type, text, {"audio_tags", "moods"})
        assert "{voice_moods}" in moods and "{voice_sounds}" in moods and "[say angrily]" not in moods, prompt_type


def test_the_reply_example_only_shows_tags_the_voices_take():
    """The Game Master NPC and NPC Initiative prompts end with an example reply: it shows sounds, moods or directions only when they are on"""
    from src.config.definitions.prompt_definitions import PromptDefinitions
    import re
    for prompt_type, getter in (("skyrim_game_master_npc_prompt", PromptDefinitions.get_skyrim_game_master_npc_prompt_config_value),
                                ("skyrim_npc_initiative_prompt", PromptDefinitions.get_skyrim_npc_initiative_prompt_config_value)):
        text = getter().default_value
        def example(conditions):
            merged = PromptDefinitions.apply_prompt_conditions(prompt_type, text, conditions)
            lines = [line for line in merged.splitlines() if line.startswith("Ysolda: ")]
            assert len(lines) == 1, (prompt_type, conditions)
            return set(re.findall(r"\[([^\]]+)\]", lines[0]))
        assert example(set()) == set()
        assert example({"audio_tags"}) == {"sigh"}
        assert example({"audio_tags", "moods"}) == {"sigh", "scared", "whisper", "happy"}
        directions = example({"directions"})
        assert len(directions) == 3 and "sigh" not in directions # a different direction on each sentence with a feeling
        assert example({"audio_tags", "directions"}) == directions | {"sigh"}


def test_a_service_writing_sounds_its_own_way_gets_them_rewritten_and_nothing_else():
    from src.tts.voice_tags import rewrite_tags
    names = {"laugh": "[laughs]", "sigh": "[sighs]"}
    assert rewrite_tags("Hello there.", names) == ("Hello there.", "")
    assert rewrite_tags("[Laugh] Fine. [reset] Go.", names) == ("[laughs] Fine. Go.", "") # [reset] is no service's here
    assert rewrite_tags("[sigh] [laugh] Fine.", names) == ("[sighs] Fine.", "") # never two side by side
    assert rewrite_tags("[say coldly] Take it, [whisper] and go.", names) == ("Take it, and go.", "say coldly") # the first direction
    assert rewrite_tags("[cough] Hm.", names) == ("Hm.", "") # a sound it doesn't make is no direction


def test_the_newer_services_make_sounds_only_while_their_switch_is_on():
    from src.tts import boson, heygen, qwen
    for module, switch in ((heygen, "heygen_audio_tags"), (qwen, "qwen_audio_tags"), (boson, "boson_audio_tags")):
        assert module.voice_tags(SimpleNamespace(**{switch: True}, language="en")) == VoiceTags(module.SOUNDS, False, getattr(module, "MOODS", ()))
        assert module.voice_tags(SimpleNamespace(**{switch: False}, language="en")) == NO_TAGS
        assert set(module.SOUND_NAMES) <= set(inworld.SOUNDS) # Mantella's own words for them, as the prompt lists them


def test_heygen_and_boson_also_take_moods_with_audio_tags():
    from src.tts import boson, heygen
    from src.tts.voice_tags import SOUND_WORDS, rewrite_tags
    for module in (heygen, boson):
        assert {"angry", "sad", "whisper", "shout"} <= set(module.MOODS) and not set(module.MOODS) & SOUND_WORDS
        assert not set(module.MOODS) & set(module.SOUNDS)
        heard = VoiceTags(module.SOUNDS, False, module.MOODS)
        assert split("[angry] Get out. [sigh] Fine.", heard) == ("Get out. Fine.", "[angry] Get out. [sigh] Fine.")
        assert split("[say angrily] Go.", heard)[1] == "Go." # a free direction isn't one of their moods
    assert rewrite_tags("[angry] Get out! [whisper] Please.", heygen.TAG_NAMES)[0] == "[angry] Get out! [whispers] Please."
    # A mood reaches a service with Directions on as a direction, and is left out for one with neither
    assert split("[angry] Go.", DIRECTIONS)[1] == "[angry] Go."
    assert split("[angry] Go.", SOUNDS)[1] == "Go."


def test_boson_keeps_the_voices_expressiveness_after_each_mood():
    from src.tts.boson import boson_text
    assert boson_text("Hello.", "Normal") == "Hello."
    assert boson_text("Hello.", "Lively") == "<|prosody:expressive_high|>Hello."
    # A mood starts a new group of tags, which replaces the one before: the expressiveness goes with it
    assert boson_text("[sad] I miss her. [laugh] Ha.", "Flat") == "<|emotion:sadness|><|prosody:expressive_low|> I miss her. <|sfx:laughter|> Ha."
    assert boson_text("Fine. [shout] Out!", "Lively") == "<|prosody:expressive_high|>Fine. <|style:shouting|><|prosody:expressive_high|> Out!"


def test_the_sounds_are_filled_into_the_prompt_sent_to_the_ai(default_context, monkeypatch):
    from src.config.definitions.prompt_definitions import PromptDefinitions
    from src.conversation import context as context_module
    monkeypatch.setattr(context_module.voice_tags, "conditions_for", lambda config, speakers: {"audio_tags"})
    monkeypatch.setattr(context_module.voice_tags, "sounds_text", lambda config, speakers: "[laugh], [sigh]")
    prompt = PromptDefinitions.get_skyrim_prompt_config_value().default_value
    text = default_context.generate_system_message(prompt, [], prompt_type="skyrim_prompt")
    assert "The ones these voices know: [laugh], [sigh]." in text
    assert "{voice_sounds}" not in text


def test_the_log_says_how_each_voice_service_speaks():
    from src.tts.registry import voice_services
    inworld_service, heygen_service = voice_services.find("Inworld"), voice_services.find("HeyGen")
    assert inworld_service.mode_text(settings()) == "TTS-2, audio tags, directions"
    # TTS-2 Flash makes no directions, so the log doesn't claim it does
    assert inworld_service.mode_text(settings(model="TTS-2 Flash")) == "TTS-2 Flash, audio tags"
    assert inworld_service.mode_text(settings(audio_tags=False, directions=False)) == "TTS-2"
    assert heygen_service.mode_text(SimpleNamespace(heygen_model="Turbo Free", heygen_audio_tags=True, language="en")) == "Turbo Free, audio tags"
    qwen_off = SimpleNamespace(qwen_audio_tags=False, qwen_directions=False, language="en")
    assert voice_services.find("Qwen").mode_text(qwen_off) == "Qwen Audio 3.1 TTS Plus" # every online service names its model
    assert voice_services.find("Qwen").mode_text(SimpleNamespace(qwen_model="qwen-audio-9-tts", language="en")) == "qwen-audio-9-tts" # typed in
    assert voice_services.find("Pocket TTS").mode_text(settings()) == "" # no model choice and nothing on: the log line stays as it was
