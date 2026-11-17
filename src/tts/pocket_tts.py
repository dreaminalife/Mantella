"""Pocket TTS, Kyutai's small voice program that copies a voice from a clip and runs on the processor.

Mantella downloads Kyutai's Windows program (one fixed version, checked against its published checksum) into
voice_programs/pocket-tts/ next to config.ini and starts it itself, in the language of the Language setting.
The program downloads Kyutai's model itself. The model that copies voices is behind a Hugging Face sign-in:
the player's token from HUGGINGFACE_SECRET_KEY.txt is handed to the program. Without access it only has its built-in voices,
and Mantella says why (the TTS page, the log and once in game).
Each clip is turned into a saved voice once (export-voice) and handed to the program through Mantella's own web address,
because the program won't read a voice from a file on this PC.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from typing import Any

import requests

from src import utils
from src.config.config_loader import ConfigLoader
from src.tts.clip_service import ClipVoiceService
from src.tts.registry import VoiceServiceInfo
from src.tts.voice_notices import add_notice
from src.tts.voice_picker import BUILT_IN, TRIAL_PREFIX, VoicePick, VoiceSpeaker
from src.tts.voice_setups import read_key_file
from src.voice_clips.preparing import in_background

NAME = "Pocket TTS"
PROGRAM_VERSION = "v4.0.1" # Kyutai's release. Its program is 3.3.0, and pins the model of each language
ARCHIVE = "pocket-tts-3.3.0-x86_64-pc-windows-msvc.zip"
ARCHIVE_SHA256 = "98753041f2e65744b989776f87bccb93f1f574a08afe1890c5427ec94cd6309c" # from the release's SHA256SUMS
DOWNLOAD_URL = f"https://github.com/kyutai-labs/pocket-tts/releases/download/{PROGRAM_VERSION}/{ARCHIVE}"
EXE = "pocket-tts.exe"
PORT = 8030 # not 8000 (its own default) or 8020 (XTTS)
KEY_FILE = "HUGGINGFACE_SECRET_KEY.txt"
MODEL_PAGE = "https://huggingface.co/kyutai/pocket-tts"
TOKEN_PAGE = "https://huggingface.co/settings/tokens"

# Mantella's language code -> Kyutai's language, and where the model that copies voices is (pinned by the program version above)
LANGUAGES = {"en": "english", "fr": "french", "de": "german", "it": "italian", "pt": "portuguese", "es": "spanish", "nl": "dutch"}
_REVISION = "3e82814a68665eec246ff649b14c71331f955c06"
MODEL_REVISIONS = {"english": "db04f0e653473e5c7333c69686ac92b7460b0c1d", **{lang: _REVISION for lang in LANGUAGES.values() if lang != "english"}}
BUILT_IN_VOICES = {"alba", "marius", "javert", "jean", "fantine", "cosette", "eponine", "azelma", "charles", "george", "michael",
                   "bill_boerst", "peter_yearsley", "stuart_bell", "caro_davy", "giovanni", "lola", "juergen", "rafael", "daan", "estelle",
                   "anna", "eve", "jane", "mary", "paul", "vera"}
MALE_VOICE = {"italian": "giovanni", "german": "juergen", "portuguese": "rafael", "dutch": "daan"}
FEMALE_VOICE = {"spanish": "lola", "french": "estelle"}

# Whether the player's sign-in reaches the model that copies voices
ACCESS_OK = "ok"
ACCESS_NO_TOKEN = "no token"
ACCESS_BAD_TOKEN = "bad token"
ACCESS_NO_TERMS = "terms not accepted"
ACCESS_UNKNOWN = "unknown" # no internet, or Hugging Face didn't answer: tried anyway

ACCESS_MESSAGES = {
    ACCESS_NO_TOKEN: f"Pocket TTS can't copy voices: {KEY_FILE} is missing or empty, so every NPC uses a built-in voice. See 'Turn on voice cloning' under Pocket TTS on the Text-to-Speech page.",
    ACCESS_BAD_TOKEN: f"Pocket TTS can't copy voices: Hugging Face doesn't accept the token in {KEY_FILE}, so every NPC uses a built-in voice. Make a new read token at {TOKEN_PAGE}.",
    ACCESS_NO_TERMS: f"Pocket TTS can't copy voices: your Hugging Face account hasn't accepted Kyutai's terms, so every NPC uses a built-in voice. Accept them at {MODEL_PAGE}.",
}
CLONING_REFUSED = "could not download the weights for the model with voice cloning" # what Kyutai's program says without access


def kyutai_language(language: str | None) -> str:
    return LANGUAGES.get((language or "en").lower()[:2], "english")


def program_folder(save_folder: str) -> str:
    return os.path.join(save_folder, "voice_programs", "pocket-tts")


def voices_folder(save_folder: str, language: str) -> str:
    """Saved voices only work with the model they were made from, so they are kept per program version and language"""
    return os.path.join(program_folder(save_folder), "voices", PROGRAM_VERSION, language)


def has_avx2() -> bool:
    try:
        return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40)) # PF_AVX2_INSTRUCTIONS_AVAILABLE
    except Exception:
        return True


def check_access(token: str, language: str, timeout: float = 10.0) -> str:
    """Asks Hugging Face whether this token can download the model that copies voices"""
    if not token:
        return ACCESS_NO_TOKEN
    url = f"https://huggingface.co/kyutai/pocket-tts/resolve/{MODEL_REVISIONS.get(language, _REVISION)}/languages/{language}/model.safetensors"
    try:
        answer = requests.head(url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout, allow_redirects=False)
    except requests.RequestException as e:
        logging.debug(f"Pocket TTS: could not reach Hugging Face to check the sign-in: {e}")
        return ACCESS_UNKNOWN
    if answer.status_code in (200, 302, 307):
        return ACCESS_OK
    if answer.status_code == 401:
        return ACCESS_BAD_TOKEN
    if answer.status_code == 403:
        return ACCESS_NO_TERMS
    return ACCESS_UNKNOWN


def install(save_folder: str) -> str:
    """Downloads Kyutai's program the first time, checks it is complete, and returns the path to pocket-tts.exe"""
    folder = os.path.join(program_folder(save_folder), PROGRAM_VERSION)
    exe = _find_exe(folder)
    if exe:
        return exe
    os.makedirs(folder, exist_ok=True)
    archive = os.path.join(folder, ARCHIVE)
    logging.log(24, f"Pocket TTS: downloading Kyutai's program ({PROGRAM_VERSION}, about 8 MB)...")
    with requests.get(DOWNLOAD_URL, stream=True, timeout=60) as answer:
        answer.raise_for_status()
        digest = hashlib.sha256()
        with open(archive + ".part", "wb") as file:
            for chunk in answer.iter_content(1 << 16):
                file.write(chunk)
                digest.update(chunk)
    if digest.hexdigest() != ARCHIVE_SHA256:
        os.remove(archive + ".part")
        raise RuntimeError("The Pocket TTS download is damaged or not Kyutai's (its checksum doesn't match). Try again later.")
    os.replace(archive + ".part", archive)
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(folder)
    os.remove(archive)
    exe = _find_exe(folder)
    if not exe:
        raise RuntimeError(f"Kyutai's download has no {EXE} in it")
    logging.log(24, "Pocket TTS: program ready")
    return exe


def _find_exe(folder: str) -> str | None:
    if not os.path.isdir(folder):
        return None
    for root, _, files in os.walk(folder):
        if EXE in files:
            return os.path.join(root, EXE)
    return None


def _remove_other_versions(save_folder: str) -> None:
    """Programs and saved voices of older versions: their model is gone, so are they"""
    for parent in (program_folder(save_folder), os.path.join(program_folder(save_folder), "voices")):
        if not os.path.isdir(parent):
            continue
        for name in os.listdir(parent):
            if re.fullmatch(r"v\d[\d.]*", name) and name != PROGRAM_VERSION and os.path.isdir(os.path.join(parent, name)):
                shutil.rmtree(os.path.join(parent, name), ignore_errors=True)


def clip_hash(clip_path: str) -> str:
    """Names a clip's saved voice: the same sound gives the same name, whichever key it is saved under"""
    with open(clip_path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()[:16]


def _language_folders(save_folder: str) -> list[str]:
    parent = os.path.join(program_folder(save_folder), "voices", PROGRAM_VERSION)
    if not os.path.isdir(parent):
        return []
    return [os.path.join(parent, name) for name in os.listdir(parent) if os.path.isdir(os.path.join(parent, name))]


def use_trial_voice(save_folder: str, key: str, clip_path: str) -> None:
    """Clone from this clip, right after hearing it: the voice made to hear it becomes the voice NPCs use, so the clip isn't cloned twice.
    Only when it was made from this very sound; any other trial voice of this clip is removed"""
    try:
        wanted = f"{TRIAL_PREFIX}{key}-{clip_hash(clip_path)}.safetensors"
    except OSError:
        wanted = ""
    for folder in _language_folders(save_folder):
        try:
            if wanted and os.path.exists(os.path.join(folder, wanted)):
                for old in os.listdir(folder):
                    if old.startswith(key + "-"):
                        os.remove(os.path.join(folder, old))
                os.replace(os.path.join(folder, wanted), os.path.join(folder, wanted[len(TRIAL_PREFIX):]))
        except OSError as e:
            logging.warning(f"Could not keep the voice made to hear this clip, so it will be cloned again: {e}")
    remove_trial_voices(save_folder, only=key)


def move_saved_voice(save_folder: str, from_key: str, to_key: str, clip_path: str) -> None:
    """A clip moved from one voice to another (an NPC's own voice used for their whole voice type): its saved voice moves with it,
    so the same clip isn't cloned again. Only a voice made from this very sound moves"""
    try:
        name = f"-{clip_hash(clip_path)}.safetensors"
    except OSError:
        return
    for folder in _language_folders(save_folder):
        source = os.path.join(folder, from_key + name)
        if not os.path.exists(source):
            continue
        try:
            for old in os.listdir(folder):
                if old.startswith(to_key + "-"):
                    os.remove(os.path.join(folder, old))
            os.replace(source, os.path.join(folder, to_key + name))
        except OSError as e:
            logging.warning(f"Could not reuse the saved voice, so it will be cloned again: {e}")


def remove_saved_voices(save_folder: str, key: str) -> None:
    """A voice that is gone (an NPC's own voice removed): its saved voice and trial voice (about 4 MB each) go too"""
    for folder in _language_folders(save_folder):
        for name in os.listdir(folder):
            if name.startswith(key + "-") or name.startswith(f"{TRIAL_PREFIX}{key}-"):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass


def remove_trial_voices(save_folder: str, only: str | None = None, keep: str | None = None) -> None:
    """The voices made to hear a clip from the list (about 4 MB each): all of them, or *only* one clip's, or all but *keep*'s"""
    for folder in _language_folders(save_folder):
        for name in os.listdir(folder):
            if not name.startswith(TRIAL_PREFIX):
                continue
            key = name[len(TRIAL_PREFIX):].rsplit("-", 1)[0]
            if (only is not None and key != only) or (keep is not None and key == keep):
                continue
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def _clean_download_cache(days: int = 30) -> None:
    """Kyutai's program keeps every voice it fetched from a web address in ~/.cache/pocket_tts and never removes them.
    Each swapped clip has a new address, so old ones pile up"""
    folder = os.path.join(os.path.expanduser("~"), ".cache", "pocket_tts")
    if not os.path.isdir(folder):
        return
    cutoff = time.time() - days * 86400
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if name.endswith(".safetensors") and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def _answers() -> bool:
    try:
        return requests.get(f"http://127.0.0.1:{PORT}/health", timeout=1).status_code == 200
    except requests.RequestException:
        return False


class _Program:
    """Kyutai's program, one for all of Mantella. Every settings change makes a new Pocket TTS service; it keeps using the running program,
    since starting it again reloads the model and the backup voice would speak meanwhile. It stops when no service uses it any more"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen | None = None
        self.external = False # another copy of the program already answered on the port: it is used as it is
        self.settings: tuple[str, str, str] | None = None # (program, language, token) it was started with
        self.users = 0
        self.job = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def add_user(self) -> None:
        with self.lock:
            self.users += 1

    def remove_user(self) -> None:
        with self.lock:
            self.users = max(0, self.users - 1)
            if self.users == 0:
                self.stop()

    def stop(self) -> None:
        with self.lock:
            process, self.process, self.external, self.settings = self.process, None, False, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()

    def launch(self, exe: str, language: str, token: str, log_path: str, environment: dict[str, str]) -> None:
        """Starts the program, unless it already runs with the same language and sign-in"""
        with self.lock:
            settings = (exe, language, token)
            if self.is_running():
                if self.settings == settings:
                    return
                logging.info("Pocket TTS: the language or the sign-in changed, so the program starts again")
                self.stop()
            elif _answers():
                logging.info(f"Pocket TTS: a program already answers on port {PORT}, using it")
                self.external, self.settings = True, settings
                return
            command = [exe, "serve", "--host", "127.0.0.1", "--port", str(PORT), "--language", language]
            logging.log(24, f"Pocket TTS: starting ({language})")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "ab") as log:
                self.process = subprocess.Popen(command, cwd=os.path.dirname(exe), env=environment, stdout=log, stderr=subprocess.STDOUT,
                                                creationflags=subprocess.CREATE_NO_WINDOW)
            self.external, self.settings = False, settings
            self.__tie_to_mantella(self.process)

    def __tie_to_mantella(self, process: subprocess.Popen) -> None:
        """The program closes when Mantella closes, however Mantella ends"""
        try:
            import win32api
            import win32con
            import win32job
            if self.job is None:
                self.job = win32job.CreateJobObject(None, "")
                limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
                limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
            handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid)
            win32job.AssignProcessToJobObject(self.job, handle)
        except Exception as e:
            logging.debug(f"Pocket TTS: could not tie the program to Mantella ({e}); it is stopped when the voice service changes")


_program = _Program()


class PocketTTS(ClipVoiceService):
    request_timeout = 60.0 # a long line on a slow processor
    SETUP_WAIT_SECONDS = 5.0 # for the first line, while the setup checks the sign-in. Longer, and the backup service speaks
    START_WAIT_SECONDS = 20.0 # for the program to answer after it is started again (its model is downloaded by then)

    def __init__(self, config: ConfigLoader, info: VoiceServiceInfo) -> None:
        self.__hashes: dict[tuple, str] = {} # set first: the shared part may ask whether a voice is ready as soon as it is set up
        super().__init__(config, info)
        self.__save_folder = config.save_folder
        self.__mantella_port = int(config.port)
        self.__kyutai_language = kyutai_language(config.language)
        self.__exe: str | None = None
        self.__stopped = False
        self.__setup_error: str | None = None
        self.__access = ACCESS_UNKNOWN
        self.__token = ""
        self.__ready = threading.Event() # the program is installed and the sign-in checked
        _program.add_user() # now, not in the setup: the service this one replaces may be stopped before the setup is done
        threading.Thread(target=self.__set_up, name="Pocket TTS setup", daemon=True).start()

    # ---- what the shared part asks ----

    def built_in_voice(self, gender: int | None) -> str:
        if gender == 1:
            return FEMALE_VOICE.get(self.__kyutai_language, "alba")
        return MALE_VOICE.get(self.__kyutai_language, "jean")

    def has_voice(self, name: str) -> bool:
        return name.lower() in BUILT_IN_VOICES

    @property
    def can_copy_voices(self) -> bool:
        return self.__access in (ACCESS_OK, ACCESS_UNKNOWN)

    @property
    def access(self) -> str:
        return self.__access

    def pick_voice(self, speaker: VoiceSpeaker | None, named_voice: str | None = None) -> VoicePick:
        pick = super().pick_voice(speaker, named_voice)
        if pick.clip_path and not self.can_copy_voices:
            return VoicePick(BUILT_IN, self.built_in_voice(speaker.gender if speaker else None), None)
        return pick

    def is_ready(self) -> bool:
        return (_program.external or _program.is_running()) and self.__answers()

    def start(self) -> None:
        if not self.__ready.wait(self.SETUP_WAIT_SECONDS):
            raise RuntimeError("Pocket TTS is still being set up")
        if self.__setup_error:
            raise RuntimeError(self.__setup_error)
        with _program.lock:
            if self.is_ready():
                return
            if _program.is_running() and _program.settings == self.__settings():
                # Started and not answering yet: the first start downloads Kyutai's model. Not waited for, so the backup speaks
                raise RuntimeError("Pocket TTS is still getting ready (the first start downloads Kyutai's model, about 220 MB)")
            if _program.process is not None and not _program.is_running():
                logging.warning(f"Pocket TTS stopped (exit code {_program.process.returncode}), starting it again. See {self.__log_path()}")
            _program.external = False
            self.__launch()
            if _program.external or _program.process is None:
                return
            process = _program.process
        deadline = time.time() + self.START_WAIT_SECONDS
        while time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Pocket TTS stopped while starting. See {self.__log_path()}")
            if self.__answers():
                return
            time.sleep(0.25)
        raise RuntimeError("Pocket TTS is still starting (the first start downloads Kyutai's model, about 220 MB)")

    def stop(self) -> None:
        """This service is done. The program stops once no Pocket TTS service uses it"""
        super().stop()
        if not self.__stopped:
            self.__stopped = True
            _program.remove_user()

    def prepare_voice(self, pick: VoicePick) -> Any:
        """A clip becomes a saved voice once, handed to the program through Mantella's own web address"""
        if not pick.clip_path:
            return pick.key
        if not self.is_ready():
            # Making a voice loads the model, which the program downloads on its first start: only once that is done
            raise RuntimeError(self.__setup_error or "Pocket TTS is still getting ready")
        clip_hash = self.__clip_hash(pick)
        folder = voices_folder(self.__save_folder, self.__kyutai_language)
        os.makedirs(folder, exist_ok=True)
        file_name = f"{pick.key}-{clip_hash}.safetensors"
        saved = os.path.join(folder, file_name)
        if not os.path.exists(saved):
            for old in os.listdir(folder): # the voice from a clip that was swapped
                if old.startswith(pick.key + "-"):
                    os.remove(os.path.join(folder, old))
            self.__export_voice(pick.clip_path, saved)
        return f"http://127.0.0.1:{self.__mantella_port}/voices/{self.__kyutai_language}/{file_name}?v={clip_hash}"

    def voice_ready(self, pick: VoicePick) -> bool:
        """A saved voice on disk, from an earlier run too"""
        if not pick.clip_path:
            return True
        try:
            clip_hash = self.__clip_hash(pick)
        except OSError:
            return False
        return os.path.exists(os.path.join(voices_folder(self.__save_folder, self.__kyutai_language), f"{pick.key}-{clip_hash}.safetensors"))

    def __clip_hash(self, pick: VoicePick) -> str:
        """The clip's hash names its saved voice. Kept while the clip file is unchanged, so checking a voice doesn't read the clip each time"""
        version = (pick.clip_path, self._clip_version(pick))
        found = self.__hashes.get(version)
        if found is None:
            found = clip_hash(str(pick.clip_path))
            self.__hashes[version] = found
        return found

    def speak(self, text: str, voice: Any, out_path: str) -> None:
        answer = requests.post(f"http://127.0.0.1:{PORT}/tts", data={"text": text, "voice_url": voice}, timeout=self.request_timeout)
        if answer.status_code != 200:
            raise RuntimeError(f"Pocket TTS answered {answer.status_code}: {answer.text[:300]}")
        if not answer.content:
            raise RuntimeError("Pocket TTS sent back no sound")
        with open(out_path, "wb") as file:
            file.write(answer.content)

    # ---- inside ----

    def __set_up(self) -> None:
        try:
            if not has_avx2():
                raise RuntimeError("Pocket TTS needs a processor with AVX2 (most from 2013 on). This one doesn't have it, so the backup voice service speaks.")
            _remove_other_versions(self.__save_folder)
            remove_trial_voices(self.__save_folder)
            _clean_download_cache()
            self.__token = read_key_file(KEY_FILE)
            self.__access = check_access(self.__token, self.__kyutai_language)
            if self.__access in ACCESS_MESSAGES:
                self.__say_no_copying(ACCESS_MESSAGES[self.__access])
            self.__exe = install(self.__save_folder)
            if not self.__stopped:
                self.__launch() # started now, so the model downloads while you play rather than when the first line is due
        except Exception as e:
            self.__setup_error = str(e)
            utils.play_error_sound()
            logging.error(f"Pocket TTS: {e}")
            add_notice(f"Pocket TTS: {e}")
        finally:
            self.__ready.set()

    def __say_no_copying(self, message: str) -> None:
        logging.warning(message)
        add_notice("Pocket TTS can't copy voices, so NPCs use built-in voices. See Mantella's Text-to-Speech page.")

    def __environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        if self.__token:
            environment["HF_TOKEN"] = self.__token # read by Kyutai's program for the gated download
        return environment

    def __log_path(self) -> str:
        return os.path.join(program_folder(self.__save_folder), "pocket-tts.log")

    def __settings(self) -> tuple[str, str, str]:
        return (self.__exe or "", self.__kyutai_language, self.__token)

    def __launch(self) -> None:
        _program.launch(self.__exe, self.__kyutai_language, self.__token, self.__log_path(), self.__environment())

    def __answers(self) -> bool:
        return _answers()

    def __export_voice(self, clip_path: str, saved: str) -> None:
        command = [self.__exe, "export-voice", clip_path, saved, "--language", self.__kyutai_language, "-q"]
        # A voice made ahead runs below normal priority, so the game and the lines being spoken come first
        priority = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) if in_background() else 0
        done = subprocess.run(command, cwd=os.path.dirname(self.__exe), env=self.__environment(), capture_output=True, text=True,
                              timeout=300, creationflags=subprocess.CREATE_NO_WINDOW | priority)
        if done.returncode != 0 or not os.path.exists(saved):
            output = (done.stderr or done.stdout or "").strip()
            if CLONING_REFUSED in output.lower():
                # Kyutai's program says this for any failed download, a network hiccup too: ask Hugging Face why
                access = check_access(self.__token, self.__kyutai_language)
                if access in ACCESS_MESSAGES:
                    self.__access = access
                    self.__say_no_copying(ACCESS_MESSAGES[access])
                else:
                    logging.warning("Pocket TTS could not download Kyutai's voice-copying model this time. "
                                    "Your sign-in is fine, so it tries again with the next line.")
            raise RuntimeError(f"Pocket TTS could not make a voice from {os.path.basename(clip_path)}: {output[-300:]}")

