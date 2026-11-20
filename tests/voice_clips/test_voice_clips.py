"""Voice clips from the game: reading the game's sound files, the clip store, asking the game and taking in what it sends, and sharing picks"""
import json
import os
import shutil

import pytest
import soundfile as sf

from src.voice_clips import fuz
from src.voice_clips.clip_store import AUTOMATIC, CHOSEN, NPCS, OWN_LINES, VOICE_TYPES, ClipInfo, ClipStore, line_id, npc_key
from src.voice_clips.picks import KEEP_MINE, TAKE_THEIRS, export_picks, import_picks
from src.voice_clips.requests import INCOMING, REQUEST_FILE, RESULT_FILE, ClipRequests

GAME_LINE = os.path.join(os.path.dirname(__file__), "data", "dialoguesolitude__000dd60f_1.fuz") # a vanilla Female Even Toned line
GAME_PATH = "sound\\voice\\skyrim.esm\\femaleeventoned\\dialoguesolitude__000dd60f_1.fuz"


def other_path(n: int) -> str:
    return f"sound\\voice\\skyrim.esm\\femaleeventoned\\dialoguesolitude__000dd6{n:02x}_1.fuz"


@pytest.fixture
def store(tmp_path):
    return ClipStore(str(tmp_path / "voice_clips"))


@pytest.fixture
def requests(store):
    return ClipRequests(store)


def a_wav(tmp_path, name="clip.wav") -> str:
    path = str(tmp_path / name)
    fuz.to_wav(GAME_LINE, path)
    return path


def deliver(requests: ClipRequests, sent: str, clips: list[dict], status="ok", missing=None) -> None:
    """What MantellaVoiceClips.dll does with a request: the game files and result.json, last"""
    request = json.loads(sent)
    os.makedirs(request["dest"], exist_ok=True)
    for i, clip in enumerate(clips, start=1):
        clip.setdefault("file", f"{i}.fuz")
        shutil.copyfile(GAME_LINE, os.path.join(request["dest"], clip["file"]))
    with open(os.path.join(request["dest"], RESULT_FILE), "w", encoding="utf-8") as file:
        json.dump({"id": request["id"], "status": status, "clips": clips, "missing": missing or []}, file)


# ---- the game's sound files ----

def test_a_fuz_line_becomes_a_plain_sound_file(tmp_path):
    samples, rate = fuz.decode(open(GAME_LINE, "rb").read())
    assert rate == 44100 and abs(samples.size / rate - 4.644) < 0.01 # the length its xWMA header says (dpds), not data size / byte rate
    out = a_wav(tmp_path)
    info = sf.info(out)
    assert info.channels == 1 and info.subtype == "PCM_16" and 3.0 < info.duration <= 4.65


def test_the_sound_of_a_fuz_starts_after_its_lip_data():
    data = open(GAME_LINE, "rb").read()
    assert fuz.sound_bytes(data)[:4] == b"RIFF" and fuz.sound_bytes(data[12:])[:4] != b"RIFF"


def test_a_line_is_known_by_its_plugin_and_info():
    assert line_id(GAME_PATH) == "skyrim.esm|000dd60f"
    assert line_id("Sound/Voice/Dawnguard.esm/FemaleKhajiit/voicepowers_shout03_0000c6fc_1.fuz") == "dawnguard.esm|0000c6fc"
    assert npc_key("Skyrim.esm", "000A2C94") == "skyrimesm000a2c94"


# ---- the clip store ----

def test_picking_another_line_puts_the_old_one_in_the_list(store, tmp_path):
    store.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=GAME_PATH))
    store.add_candidate(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path, "b.wav"), ClipInfo(path=other_path(1), seconds=6.0))
    store.add_candidate(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path, "c.wav"), ClipInfo(path=other_path(2), seconds=7.0))
    candidates = store.candidates(VOICE_TYPES, "femaleeventoned")
    assert [line_id(c.path) for _, c in candidates] == ["skyrim.esm|000dd601", "skyrim.esm|000dd602"]
    store.choose_candidate(VOICE_TYPES, "femaleeventoned", candidates[0][0])
    info = store.info(VOICE_TYPES, "femaleeventoned")
    assert info.path == other_path(1) and info.how == CHOSEN and info.turned_down == [] and info.name == "FemaleEvenToned"
    assert store.voice_type_clip("Female Even Toned").endswith("femaleeventoned.wav")
    # The old clip is back in the list, so the player can switch back to it
    candidates = store.candidates(VOICE_TYPES, "femaleeventoned")
    assert sorted(line_id(c.path) for _, c in candidates) == ["skyrim.esm|000dd602", "skyrim.esm|000dd60f"]
    old = next(path for path, c in candidates if c.line == "skyrim.esm|000dd60f")
    store.choose_candidate(VOICE_TYPES, "femaleeventoned", old)
    assert store.info(VOICE_TYPES, "femaleeventoned").path == GAME_PATH
    assert sorted(line_id(c.path) for _, c in store.candidates(VOICE_TYPES, "femaleeventoned")) == ["skyrim.esm|000dd601", "skyrim.esm|000dd602"]


def test_an_npcs_own_voice_can_become_their_voice_types(store, tmp_path):
    lydia = npc_key("Skyrim.esm", "000A2C94")
    store.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=GAME_PATH, turned_down=["skyrim.esm|000dd609"]))
    store.set_clip(NPCS, lydia, a_wav(tmp_path, "b.wav"), ClipInfo(name="Lydia", path=other_path(1), voice_type="FemaleEvenToned",
                                                                   npc_plugin="Skyrim.esm", npc_local_id="000A2C94"))
    store.add_candidate(NPCS, lydia, a_wav(tmp_path, "c.wav"), ClipInfo(path=other_path(2), order=1))
    store.add_candidate(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path, "d.wav"), ClipInfo(path=other_path(3), order=1))
    assert store.use_npc_clip_for_voice_type(lydia) == "femaleeventoned"
    info = store.info(VOICE_TYPES, "femaleeventoned")
    assert info.path == other_path(1) and info.how == CHOSEN and info.name == "FemaleEvenToned" and info.turned_down == ["skyrim.esm|000dd609"]
    assert not info.npc_plugin and not info.voice_type
    assert store.npc_clip("Skyrim.esm", "000A2C94") is None # Lydia shares her voice type's voice again
    # Her chosen clips came with her voice; the voice type's old clips are other clips, so the player can switch back
    assert [line_id(c.path) for _, c in store.voice_lines(VOICE_TYPES, "femaleeventoned")] == ["skyrim.esm|000dd601", "skyrim.esm|000dd602"]
    assert sorted(line_id(c.path) for _, c in store.other_lines(VOICE_TYPES, "femaleeventoned")) == ["skyrim.esm|000dd603", "skyrim.esm|000dd60f"]


def test_an_npc_without_a_voice_of_their_own_cant_give_it_to_their_voice_type(store):
    with pytest.raises(ValueError):
        store.use_npc_clip_for_voice_type(npc_key("Skyrim.esm", "000A2C94"))


def test_removed_lines_can_be_found_again(store, tmp_path):
    store.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=GAME_PATH,
                                                                                  turned_down=["skyrim.esm|000dd601", "skyrim.esm|000dd602"]))
    assert store.bring_back_turned_down(VOICE_TYPES, "femaleeventoned") == 2
    assert store.info(VOICE_TYPES, "femaleeventoned").turned_down == []
    assert store.turn_down_all(VOICE_TYPES, "femaleeventoned") == ["skyrim.esm|000dd60f"] # asking for more leaves out only the clip in use
    assert store.bring_back_turned_down(VOICE_TYPES, "femaleeventoned") == 0


def test_removing_an_npcs_own_clip(store, tmp_path):
    store.set_clip(NPCS, npc_key("Skyrim.esm", "000A2C94"), a_wav(tmp_path), ClipInfo(name="Lydia"))
    assert store.npc_clip("Skyrim.esm", "000A2C94")
    store.remove(NPCS, npc_key("Skyrim.esm", "000A2C94"))
    assert store.npc_clip("Skyrim.esm", "000A2C94") is None


# ---- asking the game ----

def test_a_new_voice_type_is_asked_for_once_in_five_minutes(requests):
    requests.ask_for("FemaleEvenToned", "Skyrim.esm", "000A2C94", "Lydia")
    sent = requests.requests_for_reply()
    assert len(sent) == 1
    request = json.loads(sent[0])
    assert request["voice_type"] == "FemaleEvenToned" and request["voice_type_lines"] and request["npc_lines"] == "if_different"
    assert request["npc_plugin"] == "Skyrim.esm" and request["npc_local_id"] == 0xA2C94 and request["skip_words"] == ["bardsongs"]
    assert os.path.exists(os.path.join(request["dest"], REQUEST_FILE))
    requests.ask_for("FemaleEvenToned", "Skyrim.esm", "000A2C94", "Lydia")
    assert requests.requests_for_reply() == [] # nothing came back yet: not again so soon


def test_a_damaged_request_is_left_out_and_the_others_still_go(requests):
    requests.ask_for_lines("import", [{"path": GAME_PATH, "for": "npc"}], "FemaleEvenToned", "Skyrim.esm", "not hex", "Lydia")
    requests.ask_for("MaleNord")
    [sent] = requests.requests_for_reply()
    assert json.loads(sent)["voice_type"] == "MaleNord"
    assert len(os.listdir(requests.incoming_folder)) == 1 # no folder left waiting for the damaged one


def test_a_voice_type_with_no_lines_is_not_asked_for_again(requests, monkeypatch):
    import src.voice_clips.requests as clip_requests
    requests.ask_for("UnvoicedFollower")
    [sent] = requests.requests_for_reply()
    deliver(requests, sent, [], status="nothing_found")
    requests.collect()
    later = clip_requests.time.time() + clip_requests.ASK_AGAIN_SECONDS + 1
    monkeypatch.setattr(clip_requests.time, "time", lambda: later)
    requests.ask_for("UnvoicedFollower")
    assert requests.requests_for_reply() == []
    requests.ask_for("FemaleEvenToned") # other voice types are still asked for
    assert len(requests.requests_for_reply()) == 1


def test_another_file_of_a_line_is_not_a_line_of_its_own(requests, store):
    """The game splits a long line into numbered files: to Mantella they are one line, listed once and never next to the clip's own"""
    requests.ask_for("FemaleEvenToned")
    [sent] = requests.requests_for_reply()
    deliver(requests, sent, [{"for": "voice_type", "path": GAME_PATH}, {"for": "voice_type", "path": GAME_PATH.replace("_1.fuz", "_2.fuz")},
                             {"for": "voice_type", "path": other_path(1)}, {"for": "voice_type", "path": other_path(1).replace("_1.fuz", "_3.fuz")}])
    requests.collect()
    assert [line_id(c.path) for _, c in store.candidates(VOICE_TYPES, "femaleeventoned")] == ["skyrim.esm|000dd601"]


def test_a_line_to_hear_from_the_clips_own_line_is_left_out(store, tmp_path):
    """Lines to hear kept from before: another file of the clip's line, which picking would turn down along with the clip"""
    store.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=GAME_PATH.replace("_1.fuz", "_4.fuz")))
    store.add_candidate(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(path=GAME_PATH.replace("_1.fuz", "_3.fuz")))
    store.add_candidate(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(path=other_path(2)))
    assert [line_id(c.path) for _, c in store.candidates(VOICE_TYPES, "femaleeventoned")] == ["skyrim.esm|000dd602"]


def test_an_npc_whose_voice_type_has_no_lines_gets_the_next_voice_in_line(requests):
    requests.ask_for("MyFollowerVoice", "MyFollower.esp", "00000D62", "Auri", stand_ins=["NotAVoiceType", "FemaleYoungEager"], count=10)
    [sent] = requests.requests_for_reply()
    assert json.loads(sent)["voice_type"] == "MyFollowerVoice" # nothing else is asked for while their own voice type may have lines
    deliver(requests, sent, [], status="nothing_found")
    requests.collect()
    [sent] = requests.requests_for_reply() # goes with the reply to the delivery, before the NPC's next line
    assert json.loads(sent)["voice_type"] == "NotAVoiceType"
    deliver(requests, sent, [], status="nothing_found")
    requests.collect()
    [sent] = requests.requests_for_reply()
    assert json.loads(sent)["voice_type"] == "FemaleYoungEager"
    assert json.loads(sent)["count"] == 10 # as many lines as the service speaking takes, for a stand-in too
    deliver(requests, sent, [{"for": "voice_type", "path": other_path(1)}])
    requests.collect()
    assert requests.requests_for_reply() == []


def test_another_npc_of_a_voice_type_with_no_lines_gets_their_own_stand_in(requests):
    requests.ask_for("MyFollowerVoice")
    [sent] = requests.requests_for_reply()
    deliver(requests, sent, [], status="nothing_found")
    requests.collect()
    requests.ask_for("MyFollowerVoice", stand_ins=["MaleOrc"])
    [sent] = requests.requests_for_reply()
    assert json.loads(sent)["voice_type"] == "MaleOrc"


def test_a_voice_type_whose_request_failed_is_asked_for_again(requests, monkeypatch):
    import src.voice_clips.requests as clip_requests
    requests.ask_for("FemaleEvenToned")
    [sent] = requests.requests_for_reply()
    deliver(requests, sent, [], status="error")
    requests.collect()
    later = clip_requests.time.time() + clip_requests.ASK_AGAIN_SECONDS + 1
    monkeypatch.setattr(clip_requests.time, "time", lambda: later)
    requests.ask_for("FemaleEvenToned")
    assert len(requests.requests_for_reply()) == 1


def test_the_first_line_becomes_the_clip_and_the_rest_are_lines_to_hear(requests, store):
    requests.ask_for("FemaleEvenToned", "Skyrim.esm", "000A2C94", "Lydia")
    [sent] = requests.requests_for_reply()
    deliver(requests, sent, [{"for": "voice_type", "path": GAME_PATH, "size": 27047, "source": "Skyrim - Voices_en0.bsa", "seconds": 4.64},
                             {"for": "voice_type", "path": other_path(1), "size": 30000, "source": "Skyrim - Voices_en0.bsa", "seconds": 6.0}])
    assert requests.collect() == 1
    info = store.info(VOICE_TYPES, "femaleeventoned")
    assert info.path == GAME_PATH and info.how == AUTOMATIC and info.source == "Skyrim - Voices_en0.bsa"
    assert [line_id(c.path) for _, c in store.candidates(VOICE_TYPES, "femaleeventoned")] == ["skyrim.esm|000dd601"]
    assert store.npc_clip("Skyrim.esm", "000A2C94") is None # her own lines come from the same place
    assert not os.listdir(requests.incoming_folder)
    requests.ask_for("FemaleEvenToned", "Skyrim.esm", "000A2C94", "Lydia")
    assert requests.requests_for_reply() == [] # the clip is there, Lydia is checked, and its line was just copied


def test_an_npc_whose_lines_come_from_a_voice_mod_gets_their_own_clip(requests, store):
    requests.ask_for("FemaleEvenToned", "Skyrim.esm", "000A2C94", "Lydia")
    [sent] = requests.requests_for_reply()
    deliver(requests, sent, [{"for": "voice_type", "path": GAME_PATH, "size": 27047, "source": "Skyrim - Voices_en0.bsa"},
                             {"for": "npc", "path": other_path(3), "size": 1, "source": "loose"}])
    requests.collect()
    info = store.info(NPCS, npc_key("Skyrim.esm", "000A2C94"))
    assert info.how == OWN_LINES and info.name == "Lydia" and info.npc_local_id == "000A2C94" and info.voice_type == "FemaleEvenToned"


def test_a_changed_game_line_is_copied_again(requests, store, tmp_path):
    store.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=GAME_PATH, size=27047, source="Skyrim - Voices_en0.bsa"))
    requests.ask_for("FemaleEvenToned")
    [sent] = requests.requests_for_reply()
    request = json.loads(sent)
    assert request["lines"] == [{"path": GAME_PATH, "for": "voice_type"}]
    deliver(requests, sent, [{"for": "voice_type", "path": GAME_PATH, "size": 27047, "source": "Skyrim - Voices_en0.bsa"}])
    assert requests.collect() == 0 # the same file: nothing to do
    requests2 = ClipRequests(store) # the next run of Mantella, after a voice replacer was installed
    requests2.ask_for("FemaleEvenToned")
    [sent] = requests2.requests_for_reply()
    deliver(requests2, sent, [{"for": "voice_type", "path": GAME_PATH, "size": 31000, "source": "loose"}])
    assert requests2.collect() == 1
    assert store.info(VOICE_TYPES, "femaleeventoned").source == "loose"


def test_a_gone_game_line_is_replaced_by_another(requests, store, tmp_path):
    store.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=GAME_PATH, size=1, source="x"))
    requests.ask_for("FemaleEvenToned")
    [sent] = requests.requests_for_reply()
    deliver(requests, sent, [], missing=[GAME_PATH])
    requests.collect()
    [again] = requests.requests_for_reply()
    again = json.loads(again)
    assert again["voice_type_lines"] and again["exclude"] == ["skyrim.esm|000dd60f"] and not again["lines"]


def test_more_lines_only_become_lines_to_hear(requests, store, tmp_path):
    store.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=GAME_PATH, turned_down=["skyrim.esm|000dd601"]))
    requests.ask_for_more(VOICE_TYPES, "femaleeventoned")
    [sent] = requests.requests_for_reply()
    assert set(json.loads(sent)["exclude"]) == {"skyrim.esm|000dd601", "skyrim.esm|000dd60f"}
    deliver(requests, sent, [{"for": "voice_type", "path": other_path(4)}, {"for": "voice_type", "path": other_path(5)}])
    requests.collect()
    assert store.info(VOICE_TYPES, "femaleeventoned").path == GAME_PATH
    assert len(store.candidates(VOICE_TYPES, "femaleeventoned")) == 2


def test_asking_for_more_lines_says_when_the_game_has_none_left(requests, store, tmp_path):
    store.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=GAME_PATH))
    requests.ask_for_more(VOICE_TYPES, "femaleeventoned")
    assert requests.waiting_for_more(VOICE_TYPES, "femaleeventoned")
    [sent] = requests.requests_for_reply()
    deliver(requests, sent, [], status="nothing_found")
    requests.collect()
    assert not requests.waiting_for_more(VOICE_TYPES, "femaleeventoned")
    assert requests.no_more_lines(VOICE_TYPES, "femaleeventoned")
    requests.ask_for_more(VOICE_TYPES, "femaleeventoned") # asking again starts over: a mod with new lines may be installed since
    assert not requests.no_more_lines(VOICE_TYPES, "femaleeventoned")
    [sent] = requests.requests_for_reply()
    deliver(requests, sent, [{"for": "voice_type", "path": other_path(4)}])
    requests.collect()
    assert not requests.no_more_lines(VOICE_TYPES, "femaleeventoned") and not requests.waiting_for_more(VOICE_TYPES, "femaleeventoned")


def test_a_result_mantella_never_asked_for_is_dropped(requests):
    folder = os.path.join(requests.incoming_folder, "stray")
    os.makedirs(folder)
    with open(os.path.join(folder, RESULT_FILE), "w") as file:
        json.dump({"id": "stray", "status": "ok", "clips": []}, file)
    assert requests.collect() == 0 and not os.path.exists(folder)


# ---- sharing picks ----

def test_picks_go_from_one_player_to_another(tmp_path):
    mine = ClipStore(str(tmp_path / "mine"))
    mine.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=GAME_PATH, size=5, source="loose", turned_down=["skyrim.esm|000dd601"]))
    mine.set_clip(NPCS, npc_key("Skyrim.esm", "000A2C94"), a_wav(tmp_path), ClipInfo(name="Lydia", path=other_path(3), npc_plugin="Skyrim.esm",
                                                                                      npc_local_id="000A2C94", voice_type="FemaleEvenToned"))
    picks = json.loads(json.dumps(export_picks(mine)))
    assert set(picks[VOICE_TYPES]) == {"femaleeventoned"}
    assert "size" not in picks[VOICE_TYPES]["femaleeventoned"] and "source" not in picks[VOICE_TYPES]["femaleeventoned"]

    theirs = ClipStore(str(tmp_path / "theirs"))
    theirs.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=other_path(9)))
    requests = ClipRequests(theirs)
    assert import_picks(theirs, requests, picks, KEEP_MINE) == (1, 1) # Lydia asked for, their own Female Even Toned kept
    [sent] = requests.requests_for_reply()
    request = json.loads(sent)
    assert request["lines"] == [{"path": other_path(3), "for": "npc"}] and request["npc_local_id"] == 0xA2C94
    assert import_picks(theirs, ClipRequests(theirs), picks, TAKE_THEIRS) == (2, 0)

    requests = ClipRequests(theirs)
    import_picks(theirs, requests, {"mantella_voice_picks": 1, VOICE_TYPES: {"femaleeventoned": picks[VOICE_TYPES]["femaleeventoned"]}}, TAKE_THEIRS)
    [sent] = requests.requests_for_reply()
    deliver(requests, sent, [{"for": "voice_type", "path": GAME_PATH, "size": 7, "source": "loose"}])
    requests.collect()
    info = theirs.info(VOICE_TYPES, "femaleeventoned")
    assert info.path == GAME_PATH and info.turned_down == ["skyrim.esm|000dd601"]


def test_a_file_that_is_not_picks_is_refused(store, requests):
    with pytest.raises(ValueError):
        import_picks(store, requests, {"something": 1}, KEEP_MINE)


# ---- fixes from the review ----

def test_an_npc_with_no_lines_of_their_own_gets_voice_type_lines_and_the_shared_clip_stays(requests, store, tmp_path):
    store.set_clip(VOICE_TYPES, "femaleyoungeager", a_wav(tmp_path), ClipInfo(name="FemaleYoungEager", path=GAME_PATH))
    requests.ask_for_own_clip("Skyrim.esm", "00013BAB", "Ysolda", "FemaleYoungEager")
    [sent] = requests.requests_for_reply()
    deliver(requests, sent, [{"for": "voice_type", "path": other_path(6)}, {"for": "voice_type", "path": other_path(7)}])
    requests.collect()
    assert store.info(VOICE_TYPES, "femaleyoungeager").path == GAME_PATH # everyone else keeps their voice
    own = store.info(NPCS, npc_key("Skyrim.esm", "00013BAB"))
    assert own.path == other_path(6) and own.name == "Ysolda"
    assert [line_id(c.path) for _, c in store.candidates(NPCS, npc_key("Skyrim.esm", "00013BAB"))] == ["skyrim.esm|000dd607"]


def test_checking_another_npc_doesnt_pile_up_lines_to_hear(requests, store, tmp_path):
    store.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=GAME_PATH, size=27047, source="x"))
    for local_id, lines in (("000A2C94", [GAME_PATH, other_path(1)]), ("000A2C95", [other_path(2), other_path(3)])):
        requests.ask_for("FemaleEvenToned", "Skyrim.esm", local_id, "someone")
        for sent in requests.requests_for_reply():
            request = json.loads(sent)
            clips = [{"for": "voice_type", "path": request["lines"][0]["path"], "size": 27047, "source": "x"}] if request["lines"] else \
                [{"for": "voice_type", "path": path} for path in lines]
            deliver(requests, sent, clips)
        requests.collect()
    # the line in use is not offered, and the second NPC's check added nothing
    assert [line_id(c.path) for _, c in store.candidates(VOICE_TYPES, "femaleeventoned")] == ["skyrim.esm|000dd601"]


def test_a_gone_game_line_gives_the_clip_a_new_line(requests, store, tmp_path):
    store.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=GAME_PATH, size=1, source="x"))
    requests.ask_for("FemaleEvenToned")
    [sent] = requests.requests_for_reply()
    deliver(requests, sent, [], missing=[GAME_PATH])
    requests.collect()
    [again] = requests.requests_for_reply()
    deliver(requests, again, [{"for": "voice_type", "path": other_path(8)}])
    requests.collect()
    assert store.info(VOICE_TYPES, "femaleeventoned").path == other_path(8)


def test_requests_that_never_come_back_are_cleared_after_an_hour(requests):
    requests.ask_for("FemaleEvenToned")
    [sent] = requests.requests_for_reply()
    dest = json.loads(sent)["dest"]
    requests.collect()
    assert os.path.isdir(dest)
    os.utime(dest, (0, 0))
    requests.collect()
    assert not os.path.exists(dest)


def test_a_line_turned_down_never_comes_back_when_the_clips_line_is_gone(requests, store, tmp_path):
    store.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=GAME_PATH, size=1, source="x",
                                                                              turned_down=["skyrim.esm|000dd601", "skyrim.esm|000dd602"]))
    requests.ask_for("FemaleEvenToned")
    [sent] = requests.requests_for_reply()
    deliver(requests, sent, [], missing=[GAME_PATH])
    requests.collect()
    [again] = requests.requests_for_reply()
    assert set(json.loads(again)["exclude"]) == {"skyrim.esm|000dd601", "skyrim.esm|000dd602", "skyrim.esm|000dd60f"}


def test_more_lines_for_an_npc_with_no_lines_of_their_own(requests, store, tmp_path):
    key = npc_key("Skyrim.esm", "00013BAB")
    store.set_clip(NPCS, key, a_wav(tmp_path), ClipInfo(name="Ysolda", path=other_path(6), npc_plugin="Skyrim.esm", npc_local_id="00013BAB",
                                                         voice_type="FemaleYoungEager"))
    store.set_clip(VOICE_TYPES, "femaleyoungeager", a_wav(tmp_path), ClipInfo(name="FemaleYoungEager", path=GAME_PATH))
    requests.ask_for_more(NPCS, key)
    [sent] = requests.requests_for_reply()
    request = json.loads(sent)
    assert request["voice_type_lines"] and request["npc_lines"] == "always" and "skyrim.esm|000dd606" in request["exclude"]
    deliver(requests, sent, [{"for": "voice_type", "path": other_path(7)}, {"for": "voice_type", "path": other_path(8)}]) # she has none of her own
    requests.collect()
    assert [line_id(c.path) for _, c in store.candidates(NPCS, key)] == ["skyrim.esm|000dd607", "skyrim.esm|000dd608"]
    assert store.info(NPCS, key).path == other_path(6) # more lines to hear, the clip itself stays
    assert store.candidates(VOICE_TYPES, "femaleyoungeager") == [] # and they are hers, not her voice type's


def test_an_npcs_own_clip_never_starts_as_their_voice_types_line(requests, store, tmp_path):
    store.set_clip(VOICE_TYPES, "femaleyoungeager", a_wav(tmp_path), ClipInfo(name="FemaleYoungEager", path=GAME_PATH))
    requests.ask_for_own_clip("Skyrim.esm", "00013BAB", "Ysolda", "FemaleYoungEager")
    [sent] = requests.requests_for_reply()
    assert json.loads(sent)["exclude"] == ["skyrim.esm|000dd60f"]
    requests.ask_for_own_clip("Skyrim.esm", "00013BAC", "Someone", "MaleNord", count=10) # no clip for the voice type yet: nothing to leave out
    [sent] = requests.requests_for_reply()
    assert json.loads(sent)["exclude"] == [] and json.loads(sent)["count"] == 10


# ---- several clips per voice ----

def chosen(store, kind=VOICE_TYPES, key="femaleeventoned") -> list[str]:
    return [line_id(c.path) for _, c in store.voice_lines(kind, key)]


def a_voice_with_lines(store, tmp_path, chosen_count: int, others: int = 0) -> None:
    store.set_clip(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path), ClipInfo(name="FemaleEvenToned", path=GAME_PATH))
    for n in range(1, chosen_count + others + 1):
        store.add_candidate(VOICE_TYPES, "femaleeventoned", a_wav(tmp_path, f"{n}.wav"),
                            ClipInfo(path=other_path(n), order=n if n <= chosen_count else 0))


def test_a_new_voice_gets_all_its_lines_chosen_in_order(requests, store):
    requests.ask_for("FemaleEvenToned", count=10) # ElevenLabs speaks first
    [sent] = requests.requests_for_reply()
    assert json.loads(sent)["count"] == 10
    deliver(requests, sent, [{"for": "voice_type", "path": GAME_PATH},
                             {"for": "voice_type", "path": other_path(2)}, {"for": "voice_type", "path": other_path(1)}])
    requests.collect()
    assert chosen(store) == ["skyrim.esm|000dd60f", "skyrim.esm|000dd602", "skyrim.esm|000dd601"]
    assert not store.is_hand_picked(VOICE_TYPES, "femaleeventoned")


def test_add_and_take_out(store, tmp_path):
    a_voice_with_lines(store, tmp_path, chosen_count=2, others=2)
    other = next(p for p, c in store.other_lines(VOICE_TYPES, "femaleeventoned") if c.line == "skyrim.esm|000dd604")
    store.add_line(VOICE_TYPES, "femaleeventoned", other, 2) # right below the clips the service uses
    assert chosen(store) == ["skyrim.esm|000dd60f", "skyrim.esm|000dd601", "skyrim.esm|000dd604", "skyrim.esm|000dd602"]
    assert store.is_hand_picked(VOICE_TYPES, "femaleeventoned") # the player chose: Mantella changes none of their clips by itself
    second = store.voice_lines(VOICE_TYPES, "femaleeventoned")[1][0]
    store.take_out(VOICE_TYPES, "femaleeventoned", second)
    assert chosen(store) == ["skyrim.esm|000dd60f", "skyrim.esm|000dd604", "skyrim.esm|000dd602"]
    assert "skyrim.esm|000dd601" in [c.line for _, c in store.other_lines(VOICE_TYPES, "femaleeventoned")] # there to add again
    # Taking out the top clip puts the next one on top
    store.take_out(VOICE_TYPES, "femaleeventoned", store.clip(VOICE_TYPES, "femaleeventoned"))
    assert chosen(store) == ["skyrim.esm|000dd604", "skyrim.esm|000dd602"]
    assert store.info(VOICE_TYPES, "femaleeventoned").name == "FemaleEvenToned" and store.is_hand_picked(VOICE_TYPES, "femaleeventoned")
    store.take_out(VOICE_TYPES, "femaleeventoned", store.voice_lines(VOICE_TYPES, "femaleeventoned")[1][0])
    with pytest.raises(ValueError): # a voice always keeps one clip
        store.take_out(VOICE_TYPES, "femaleeventoned", store.clip(VOICE_TYPES, "femaleeventoned"))


def test_use_this_one_swaps_two_clips_and_nothing_else(store, tmp_path):
    a_voice_with_lines(store, tmp_path, chosen_count=2, others=1)
    other = next(p for p, c in store.other_lines(VOICE_TYPES, "femaleeventoned"))
    store.choose_candidate(VOICE_TYPES, "femaleeventoned", other)
    assert chosen(store) == ["skyrim.esm|000dd603", "skyrim.esm|000dd601", "skyrim.esm|000dd602"]
    assert [c.line for _, c in store.other_lines(VOICE_TYPES, "femaleeventoned")] == ["skyrim.esm|000dd60f"]
    second = store.voice_lines(VOICE_TYPES, "femaleeventoned")[2][0]
    store.choose_candidate(VOICE_TYPES, "femaleeventoned", second) # a chosen one: the top clip takes its place
    assert chosen(store) == ["skyrim.esm|000dd602", "skyrim.esm|000dd601", "skyrim.esm|000dd603"]


def test_each_service_takes_what_fits_from_the_top():
    from src.voice_clips.clip_store import lines_within
    assert lines_within([7.0, 8.0, 9.0, 6.0], 0) == 1 # Pocket TTS and setups: the top clip
    assert lines_within([7.0, 8.0, 9.0, 6.0], 29.5) == 3 # Inworld: 7 + 8 + 9 and the gaps; a fourth would pass 30 s
    assert lines_within([7.0, 8.0, 6.0, 6.0], 29.5) == 4 # 27 s and 1.5 s of gaps
    assert lines_within([10.0] * 20, 120.0) == 11 # ElevenLabs: 2 minutes
    assert lines_within([5.0] * 20, 120.0) == 16 # and 16 clips at most
    assert lines_within([], 30.0) == 0


def test_find_more_clips_swaps_the_other_clips_and_keeps_the_chosen_ones(requests, store, tmp_path):
    a_voice_with_lines(store, tmp_path, chosen_count=1, others=2)
    requests.ask_for_more(VOICE_TYPES, "femaleeventoned")
    [sent] = requests.requests_for_reply()
    assert set(json.loads(sent)["exclude"]) == {"skyrim.esm|000dd60f", "skyrim.esm|000dd601", "skyrim.esm|000dd602", "skyrim.esm|000dd603"}
    deliver(requests, sent, [{"for": "voice_type", "path": other_path(7)}, {"for": "voice_type", "path": other_path(8)}])
    requests.collect()
    assert chosen(store) == ["skyrim.esm|000dd60f", "skyrim.esm|000dd601"]
    assert sorted(c.line for _, c in store.other_lines(VOICE_TYPES, "femaleeventoned")) == ["skyrim.esm|000dd607", "skyrim.esm|000dd608"]
    # The game has no new lines left: the ones swapped out come back, as many as the player asked for
    requests.ask_for_more(VOICE_TYPES, "femaleeventoned", 9)
    [sent] = requests.requests_for_reply()
    assert json.loads(sent)["count"] == 9
    deliver(requests, sent, [], status="nothing_found")
    requests.collect()
    assert requests.waiting_for_more(VOICE_TYPES, "femaleeventoned") and not requests.no_more_lines(VOICE_TYPES, "femaleeventoned")
    [again] = requests.requests_for_reply()
    assert "skyrim.esm|000dd602" not in json.loads(again)["exclude"] and json.loads(again)["count"] == 9
    deliver(requests, again, [], status="nothing_found")
    requests.collect()
    assert requests.no_more_lines(VOICE_TYPES, "femaleeventoned") and not requests.waiting_for_more(VOICE_TYPES, "femaleeventoned")


def test_a_short_voice_is_topped_up_once_with_chosen_lines(requests, store, tmp_path):
    a_voice_with_lines(store, tmp_path, chosen_count=3)
    assert requests.ask_to_top_up(VOICE_TYPES, "femaleeventoned", 6)
    assert not requests.ask_to_top_up(VOICE_TYPES, "femaleeventoned", 6) # once per run
    [sent] = requests.requests_for_reply()
    request = json.loads(sent)
    assert request["count"] == 6 and "skyrim.esm|000dd603" in request["exclude"]
    deliver(requests, sent, [{"for": "voice_type", "path": other_path(9)}])
    assert requests.collect() == 1
    assert chosen(store) == ["skyrim.esm|000dd60f", "skyrim.esm|000dd601", "skyrim.esm|000dd602", "skyrim.esm|000dd603", "skyrim.esm|000dd609"]


def test_every_chosen_line_is_copied_again_after_a_voice_mod_and_a_gone_one_goes(requests, store, tmp_path):
    a_voice_with_lines(store, tmp_path, chosen_count=2)
    requests.ask_for("FemaleEvenToned")
    [sent] = requests.requests_for_reply()
    assert [l["path"] for l in json.loads(sent)["lines"]] == [GAME_PATH, other_path(1), other_path(2)]
    deliver(requests, sent, [{"for": "voice_type", "path": GAME_PATH}, {"for": "voice_type", "path": other_path(2), "size": 99, "source": "loose"}],
            missing=[other_path(1)])
    requests.collect()
    lines = store.voice_lines(VOICE_TYPES, "femaleeventoned")
    assert [c.line for _, c in lines] == ["skyrim.esm|000dd60f", "skyrim.esm|000dd602"] and lines[1][1].source == "loose"


def test_a_voices_file_carries_the_chosen_clips_in_order(tmp_path):
    mine = ClipStore(str(tmp_path / "mine"))
    a_voice_with_lines(mine, tmp_path, chosen_count=2, others=1)
    picks = json.loads(json.dumps(export_picks(mine)))
    assert picks[VOICE_TYPES]["femaleeventoned"]["lines"] == [other_path(1), other_path(2)]
    theirs = ClipStore(str(tmp_path / "theirs"))
    requests = ClipRequests(theirs)
    import_picks(theirs, requests, picks, TAKE_THEIRS)
    [sent] = requests.requests_for_reply()
    assert [l["path"] for l in json.loads(sent)["lines"]] == [GAME_PATH, other_path(1), other_path(2)]
    deliver(requests, sent, [{"for": "voice_type", "path": GAME_PATH}, {"for": "voice_type", "path": other_path(1)},
                             {"for": "voice_type", "path": other_path(2)}])
    requests.collect()
    assert chosen(theirs) == ["skyrim.esm|000dd60f", "skyrim.esm|000dd601", "skyrim.esm|000dd602"]
    assert import_picks(mine, ClipRequests(mine), picks, TAKE_THEIRS) == (0, 0) # the same clips: nothing to ask for
