"""Heard lines in each NPC's memory, next to their summaries"""
import os
from unittest.mock import MagicMock

import pytest

from src.character_manager import Character
from src.characters_manager import Characters
from src.conversation.context import build_bios_and_summaries_text
from src.games.equipment import Equipment
from src.heard_lines.heard_line import HandOver, HeardPerson
from src.heard_lines.heard_lines_store import JsonHeardLinesStore
from src.heard_lines.heard_lines_text import PlainHeardLinesText
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage, join_message
from src.remember.summaries import Summaries, build_merged_timeline
from tests.heard_lines.lines import CARLOTTA, MIKAEL, REF, WORLD, at, hand_over, menu_line, scene_line, HULDA, SAADIA

HEARD = "Your beauty outshines the sun."


def npc(name: str, ref_id: str | None = None) -> Character:
    return Character(base_id="0", ref_id=ref_id or REF[name], name=name, gender=0, race="[Race <NordRace (00013746)>]", is_player_character=False,
                     bio=f"{name} lives in Whiterun.", is_in_combat=False, is_enemy=False, relationship_rank=0, is_generic_npc=False,
                     ingame_voice_model="", tts_voice_model="", csv_in_game_voice_model="", advanced_voice_model="", voice_accent="en",
                     equipment=Equipment({}), custom_character_values={})


def write_summary(skyrim, character: Character, text: str, folder_name: str | None = None):
    folder = os.path.join(skyrim.conversation_folder_path, WORLD, folder_name or f"{character.name} - {character.ref_id}")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{character.name}_summary_1.txt"), "w", encoding="utf-8") as f:
        f.write(text)


@pytest.fixture
def store(skyrim, default_config) -> JsonHeardLinesStore:
    return JsonHeardLinesStore(skyrim, default_config)


@pytest.fixture
def summary_client() -> MagicMock:
    client = MagicMock()
    client.request_call.return_value = "A new summary."
    client.token_limit = 4096
    client.get_count_tokens.return_value = 1
    client.is_too_long.return_value = False
    return client


@pytest.fixture
def rememberer(skyrim, default_config, llm_client, store, summary_client, monkeypatch) -> Summaries:
    monkeypatch.setattr("src.remember.summaries.time.sleep", lambda *_args, **_kwargs: None)
    return Summaries(skyrim, default_config, llm_client, "English", summary_client=summary_client,
                     heard_lines_store=store, heard_lines_text=PlainHeardLinesText())


def keep(store, listener: str, *items):
    store.add(WORLD, HeardPerson(REF[listener], listener), list(HandOver.parse(hand_over(*items)).lines))


def characters(*people: Character) -> Characters:
    result = Characters()
    for person in people:
        result.add_or_update_character(person)
    return result


def test_heard_lines_sit_between_summaries_in_time_order(rememberer, store, skyrim):
    saadia = npc("Saadia")
    write_summary(skyrim, saadia, "ts=500\n- Older talk.\n\nts=1500\n- Newer talk.\n")
    keep(store, "Saadia", scene_line(HEARD, time=1000))
    memory = rememberer.get_character_summary(saadia, WORLD)
    assert memory.index("Older talk.") < memory.index(HEARD) < memory.index("Newer talk.")
    assert "New developments" not in memory


def test_heard_lines_never_go_under_new_developments():
    timeline = build_merged_timeline([], [(100, "- A summary.")], [(300, "Became thane")], exclude_events=[(300, "Became thane")],
                                     heard_entries=[(200, "Saadia overheard something.")])
    assert timeline == "- A summary.\nSaadia overheard something."


def test_older_summary_blocks_stay_first(rememberer, store, skyrim):
    saadia = npc("Saadia")
    write_summary(skyrim, saadia, "An old summary without a time.\n\nts=2000\n- Newer talk.\n")
    keep(store, "Saadia", scene_line(HEARD, time=1000))
    memory = rememberer.get_character_summary(saadia, WORLD)
    assert memory.index("An old summary without a time.") < memory.index(HEARD) < memory.index("Newer talk.")


def test_an_npc_with_only_heard_lines_has_a_memory(rememberer, store):
    keep(store, "Saadia", scene_line(HEARD))
    assert "Below is a summary of past events:" in rememberer.get_prompt_text(characters(npc("Saadia")), WORLD)


def test_the_npc_with_only_the_older_name_folder_keeps_summaries_and_heard_lines(rememberer, store, skyrim):
    saadia = npc("Saadia")
    write_summary(skyrim, saadia, "ts=500\n- Older talk.\n", folder_name="Saadia")
    keep(store, "Saadia", scene_line(HEARD, time=1000))
    memory = rememberer.get_character_summary(saadia, WORLD)
    assert "Older talk." in memory and HEARD in memory


def test_one_on_one_and_group_prompts_carry_each_npcs_own_lines(rememberer, store):
    keep(store, "Saadia", scene_line(HEARD))
    keep(store, "Hulda", menu_line("Any rumors?", "Not today.", heard_by=[HULDA]))
    one = rememberer.get_prompt_text(characters(npc("Saadia")), WORLD)
    assert "Saadia overheard Mikael say to Carlotta" in one
    group = rememberer.get_prompt_text(characters(npc("Saadia"), npc("Hulda")), WORLD)
    saadia_part = group.split("[This is the beginning of Hulda's memory]")[0]
    assert HEARD in saadia_part and "Not today." not in saadia_part # A Game Master turn builds the prompt for one NPC, the same way
    assert "Kit asked Hulda" in group.split("[This is the beginning of Hulda's memory]")[1]


def test_the_name_with_a_number_still_finds_the_lines(rememberer, store):
    keep(store, "Saadia", scene_line(HEARD))
    assert HEARD in rememberer.get_character_summary(npc("Saadia 2", REF["Saadia"]), WORLD)


def test_bios_and_summaries_carry_heard_lines_for_shout_initiative_and_npc_events(rememberer, store, default_config):
    keep(store, "Saadia", scene_line(HEARD), menu_line("Any rumors?", "Not today.", heard_by=[HULDA, SAADIA], time=2000))
    text = build_bios_and_summaries_text([npc("Saadia")], default_config, rememberer, WORLD, "Kit", contains_player=True, always_multi_npc=True)
    assert HEARD in text and "Saadia overheard Kit ask Hulda" in text and "Kit was there" in text
    hidden = build_bios_and_summaries_text([npc("Saadia")], default_config, rememberer, WORLD, "Kit", contains_player=True,
                                           always_multi_npc=True, hide_player=True)
    assert HEARD in hidden and "Kit" not in hidden and "Not today." not in hidden


def test_director_mode_prompt_leaves_out_menu_talk(rememberer, store):
    keep(store, "Hulda", menu_line("Any rumors?", "Not today.", heard_by=[HULDA]))
    assert "Not today." in rememberer.get_prompt_text(characters(npc("Hulda")), WORLD)
    assert "Not today." not in rememberer.get_prompt_text(characters(npc("Hulda")), WORLD, hide_player=True)


def test_summary_writing_leaves_heard_lines_out(rememberer, store, summary_client, default_config):
    saadia = npc("Saadia")
    keep(store, "Saadia", scene_line(HEARD))
    thread = message_thread(default_config, None)
    thread.add_message(join_message(saadia, default_config))
    for i in range(5):
        thread.add_message(UserMessage(default_config, f"Player line number {i} with extra words."))
    rememberer.save_conversation_state(thread, characters(saadia), WORLD, save_thoughts=False, save_reflections=False)
    prompts = ["\n".join(str(m["content"]) for m in call[0][0].get_openai_messages()) for call in summary_client.request_call.call_args_list]
    assert prompts and all(HEARD not in prompt for prompt in prompts)
    assert HEARD in rememberer.get_character_summary(saadia, WORLD) # The memory still has them


def test_summary_writing_calls_leave_heard_lines_out(rememberer):
    """The five calls summary writing makes (summary, thoughts and reflections) each ask without heard lines"""
    import inspect
    source = inspect.getsource(Summaries)
    calls = [line for line in source.splitlines() if "self.get_character_summary(" in line or "self.get_prompt_text(" in line]
    assert len(calls) == 5
    following = source.split("memory_conversation_summaries = self.get_prompt_text(")[1].split(")")[0]
    assert all("include_heard_lines=False" in line for line in calls if "get_character_summary" in line)
    assert "include_heard_lines=False" in following
