import asyncio
import threading
from typing import Any, Hashable

from fastapi import FastAPI, Request
from src.llm.summary_client import SummaryLLMCLient
from src.config.config_loader import ConfigLoader
from src.games.fallout4 import Fallout4
from src.games.gameable import Gameable
from src.games.skyrim import Skyrim
from src.output_manager import ChatManager
from src.llm.llm_client import LLMClient
from src.game_manager import GameStateManager
from src.http.routes.routeable import routeable
from src.http.communication_constants import communication_constants as comm_consts
from src.tts.ttsable import TTSable
from src.tts.xvasynth import xVASynth
from src.tts.xtts import XTTS
from src.tts.piper import Piper
from src.tts.registry import create_voice_service
from src.tts.voice_folders import voice_clips_folder
from src.voice_clips.clip_store import shared_clip_store
from src.voice_clips.requests import clip_requests_for
from src.tts.voice_notices import take_notices
from src import utils
from src.config.definitions.game_definitions import GameEnum
from src.config.definitions.tts_definitions import TTSEnum
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.GAME_CONNECTION)
startup_log = get_topic_logger(LogTopic.STARTUP)
settings_log = get_topic_logger(LogTopic.SETTINGS_PAGE)
conversation_log = get_topic_logger(LogTopic.CONVERSATION)
voice_clips_log = get_topic_logger(LogTopic.VOICE_CLIPS)
vision_log = get_topic_logger(LogTopic.VISION)
game_master_log = get_topic_logger(LogTopic.GAME_MASTER)

class mantella_route(routeable):
    """Main route for Mantella conversations

    Args:
        routeable (_type_): _description_
    """
    def __init__(self, config: ConfigLoader, stt_secret_key_file: str, image_secret_key_file: str, secret_key_file: str, language_info: dict[Hashable, str]) -> None:
        super().__init__(config)
        self.__language_info: dict[Hashable, str] = language_info
        self.__secret_key_file: str = secret_key_file
        self.__stt_secret_key_file = stt_secret_key_file
        self.__image_secret_key_file: str = image_secret_key_file
        self.__game: GameStateManager | None = None
        self.__request_lock: threading.Lock = threading.Lock()
        # The conversation requests the game is waiting for an answer to, oldest first, by request type. The interrupt hotkey and the end
        # of the conversation answer them right away, without waiting for them to finish. Only used in the server's event loop
        self.__waiting: list[tuple[str, asyncio.Future]] = []
        self.__background_tasks: set[asyncio.Future] = set() # Requests answered early, and ends, still running. See __keep_running

        # if not self._can_route_be_used():
        #     error_message = "MantellaSoftware settings faulty. Please check MantellaSoftware's window or log."
        #     log.error(error_message)

    def start_loading_in_background(self):
        """Loads the settings, TTS, LLM clients and character data right away instead of on the first request from the game"""
        threading.Thread(target=self.__load, daemon=True).start()

    def __load(self):
        try:
            with self.__request_lock: # Any request arriving meanwhile waits for this instead of loading again
                self._can_route_be_used()
        except Exception as e:
            startup_log.error(f"Loading on startup failed, it will be retried when a conversation starts: {e}")

    def _supports_hot_swap(self) -> bool:
        """Returns True since mantella_route supports hot-swapping settings"""
        return True

    @utils.time_it
    def _hot_swap_settings(self) -> bool:
        """Attempts to hot-swap settings without ending active conversations"""
        if not self.__game:
            return False
        
        try:
            # Get the current game instance from GameStateManager
            current_game = self.__game.game
            
            # Try to hot-swap the existing game instance first (this preserves character data)
            game_hot_swap_success = current_game.hot_swap_settings(self._config)
            
            # Create new TTS and LLM client components with updated config
            tts: TTSable
            if self._config.tts_service == TTSEnum.XVASYNTH:
                tts = xVASynth(self._config)
            elif self._config.tts_service == TTSEnum.XTTS:
                tts = XTTS(self._config, current_game)  # Use existing game instance
            elif self._config.tts_service == TTSEnum.PIPER:
                tts = Piper(self._config, current_game)  # Use existing game instance
            else: # the newer voice services and the player's voice setups
                tts = create_voice_service(self._config.tts_service_name, self._config, current_game)

            llm_client = LLMClient(self._config, self.__secret_key_file, self.__image_secret_key_file)
            
            chat_manager = ChatManager(self._config, tts, llm_client, None, self.__secret_key_file, current_game)
            chat_manager.start_backup_voice_service() # before the old one stops, so a backup both use keeps running

            # Try to hot-swap the GameStateManager components with the existing game instance
            success = self.__game.hot_swap_settings(
                game=current_game,  # Pass the existing game instance instead of creating new one
                chat_manager=chat_manager,
                config=self._config,
                llm_client=llm_client,
                secret_key_file=self.__secret_key_file,
                image_secret_key_file=self.__image_secret_key_file
            )
            
            if success and game_hot_swap_success:
                settings_log.info("Hot-swapped settings successfully without ending conversation or reloading character data")
                return True
            else:
                settings_log.warning("Hot-swap failed, falling back to full reinitialization")
                chat_manager.stop_voice_services(keep=tts) # the backup started above: the full reinitialization starts its own
                return False
                
        except Exception as e:
            settings_log.error(f"Error during hot-swap: {e}")
            return False

    @utils.time_it
    def _setup_route(self):
        if self.__game:
            self.__game.end_conversation({})
            self.__game.stop_voice_services()

        game: Gameable
        game_enum = self._config.game
        if game_enum.base_game == GameEnum.FALLOUT4:
            game = Fallout4(self._config)
        else:
            game = Skyrim(self._config)

        tts: TTSable
        if self._config.tts_service == TTSEnum.XVASYNTH:
            tts = xVASynth(self._config)
        elif self._config.tts_service == TTSEnum.XTTS:
            tts = XTTS(self._config, game)
        elif self._config.tts_service == TTSEnum.PIPER:
            tts = Piper(self._config, game)
        else: # the newer voice services and the player's voice setups
            tts = create_voice_service(self._config.tts_service_name, self._config, game)

        llm_client = LLMClient(self._config, self.__secret_key_file, self.__image_secret_key_file)
        summary_client = SummaryLLMCLient(self._config, self.__secret_key_file, self.__image_secret_key_file)
        
        chat_manager = ChatManager(self._config, tts, llm_client, None, self.__secret_key_file, game)
        chat_manager.start_backup_voice_service()
        self.__game = GameStateManager(game, chat_manager, self._config, self.__language_info, llm_client, summary_client, self.__stt_secret_key_file, self.__secret_key_file)

        # Set the global reference for UI access
        try:
            from src.ui.settings_ui_constructor import set_game_manager_reference
            settings_log.debug("Setting global game manager reference for character data reload...")
            set_game_manager_reference(self.__game)
            settings_log.debug("Global game manager reference has been set for character data reload.")
        except ImportError:
            # If the UI module is not available, just continue
            settings_log.warning("Could not import set_game_manager_reference. Character data reload button in UI will not work.")
            pass

    @utils.time_it
    def add_route_to_server(self, app: FastAPI):
        @app.post("/mantella")
        async def mantella(request: Request):
            received_json: dict[str, Any] | None = await request.json()
            if received_json and received_json.get(comm_consts.KEY_REQUESTTYPE) == comm_consts.KEY_REQUESTTYPE_INTERRUPT:
                # Handled right away. With the interrupt hotkey, the request the game is waiting for is answered with the player's turn
                # now. That request goes on until it has picked the interruption up, and its own answer is thrown away
                if self.__game and self.__game.interrupt_conversation(received_json):
                    self.__answer_waiting(self.INTERRUPTIBLE_REQUESTS, self.__game.player_turn_reply())
                return {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_INTERRUPTRECEIVED}
            if received_json and received_json.get(comm_consts.KEY_REQUESTTYPE) == comm_consts.KEY_REQUESTTYPE_ENDCONVERSATION:
                return await self.__end(received_json)
            if received_json and received_json.get(comm_consts.KEY_REQUESTTYPE) == comm_consts.KEY_REQUESTTYPE_SHOW:
                # Handled right away, so the picture is of the moment the player pressed Show, even while the NPCs are talking
                return await asyncio.to_thread(self.__process_show, received_json)
            if received_json and received_json.get(comm_consts.KEY_REQUESTTYPE) == comm_consts.KEY_REQUESTTYPE_NPCINITIATIVE:
                # Not queued with the other requests, so a slow check never delays a conversation the player starts
                return await asyncio.to_thread(self.__process_npc_initiative_check, received_json)
            if received_json and received_json.get(comm_consts.KEY_REQUESTTYPE) == comm_consts.KEY_REQUESTTYPE_NPCEVENT:
                # Not queued either, for the same reason
                return await asyncio.to_thread(self.__process_npc_event_check, received_json)
            if received_json and received_json.get(comm_consts.KEY_REQUESTTYPE) == comm_consts.KEY_REQUESTTYPE_VOICECLIP_DELIVERED:
                # Not queued either: the request a new voice's first line is made for may be waiting for these clips
                return await self.__with_extras(await asyncio.to_thread(self.__process_voice_clips_delivered, received_json))
            # Handled in a worker thread, so the server stays free to receive an interruption
            request_type = received_json.get(comm_consts.KEY_REQUESTTYPE) if received_json else None
            arrival = (self.__game, self.__game.ends_requested if self.__game else 0)
            if request_type not in self.ENDABLE_REQUESTS:
                return await self.__with_extras(await asyncio.to_thread(self.__process_request, received_json, arrival))
            worker = asyncio.ensure_future(asyncio.to_thread(self.__process_request, received_json, arrival))
            answered_early = asyncio.get_running_loop().create_future()
            self.__waiting.append((request_type, answered_early))
            try:
                await asyncio.wait({worker, answered_early}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                self.__waiting = [(t, f) for t, f in self.__waiting if f is not answered_early]
            if answered_early.done():
                self.__keep_running(worker) # Its own answer comes too late
                return await self.__with_extras(answered_early.result())
            return await self.__with_extras(worker.result())

    def __keep_running(self, task: asyncio.Future):
        """Keeps a task nobody waits for until it is done. Its error, if any, was logged where it happened"""
        self.__background_tasks.add(task)
        def done(t: asyncio.Future):
            self.__background_tasks.discard(t)
            if not t.cancelled():
                t.exception()
        task.add_done_callback(done)

    # The requests the game waits for while a conversation runs. The end of the conversation answers them right away
    ENDABLE_REQUESTS = {comm_consts.KEY_REQUESTTYPE_STARTCONVERSATION, comm_consts.KEY_REQUESTTYPE_CONTINUECONVERSATION,
                        comm_consts.KEY_REQUESTTYPE_PLAYERINPUT, comm_consts.KEY_REQUESTTYPE_CONVERSATIONEVENT}
    # The ones the interrupt hotkey answers right away, with the player's turn. The game sends it only while the NPCs have the turn.
    # Not the player's line: it is quick, and it may hold a goodbye, which must still end the conversation. The continue request after it
    # picks the interruption up
    INTERRUPTIBLE_REQUESTS = {comm_consts.KEY_REQUESTTYPE_CONTINUECONVERSATION}

    def __answer_waiting(self, request_types: set[str], reply: dict[str, Any]) -> bool:
        """Answers the oldest request of these types the game is waiting for with this reply, and any others with a reply the game
        ignores, so it acts on the reply only once. Called in the server's event loop

        Returns:
            bool: False if the game is waiting for none of them
        """
        answered = False
        for request_type, answered_early in self.__waiting:
            if request_type not in request_types or answered_early.done():
                continue
            answered_early.set_result(dict(reply) if not answered else {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_INTERRUPTRECEIVED})
            answered = True
        return answered

    async def __end(self, received_json: dict[str, Any]) -> dict[str, Any]:
        """The game ended the conversation. A request it is still waiting for is answered right away with the end, and the end request
        itself with a reply the game ignores. Otherwise the end request is answered with the end right away.
        The conversation ends here once the requests before are done, without the game waiting: what the NPCs remember of it is saved
        then. A conversation started meanwhile waits for that"""
        self._log_received(received_json)
        end_reply = {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_ENDCONVERSATION}
        game = self.__game
        if game:
            game.stop_conversation_now()
        answered_early = self.__answer_waiting(self.ENDABLE_REQUESTS, end_reply)
        self.__keep_running(asyncio.ensure_future(asyncio.to_thread(self.__end_after_requests_before)))
        reply = {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_INTERRUPTRECEIVED} if answered_early else end_reply
        return await self.__with_extras(reply)

    def __end_after_requests_before(self):
        with self.__request_lock:
            try:
                if not self._can_route_be_used() or not self.__game:
                    return
                self.__game.end_requested_conversation()
            except Exception:
                conversation_log.exception("Ending the conversation failed")

    async def __with_extras(self, reply: dict[str, Any]) -> dict[str, Any]:
        """Puts the voice clip requests and voice notices on the reply the game gets, whatever it is"""
        return await asyncio.to_thread(self.__add_extras, reply)

    def __add_extras(self, reply: dict[str, Any]) -> dict[str, Any]:
        if isinstance(reply, dict):
            try:
                voice_clip_requests = self.__voice_clip_requests().requests_for_reply()
                if voice_clip_requests:
                    reply[comm_consts.KEY_VOICECLIP_REQUESTS] = voice_clip_requests # The add-on hands these to MantellaVoiceClips.dll, whatever the reply is
            except Exception as e:
                voice_clips_log.warning(f"Voice clips: could not add the requests to the reply: {e}")
            notices = take_notices()
            if notices:
                reply[comm_consts.KEY_VOICE_NOTICES] = notices
        self._log_sent(reply)
        return reply

    def __process_show(self, received_json: dict[str, Any]) -> dict[str, Any]:
        """Show: never replies with an error, which would end the conversation"""
        self._log_received(received_json)
        game = self.__game
        reply = {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_SHOWRECEIVED, comm_consts.KEY_SHOW_TAKEN: False}
        if game:
            try:
                reply = game.show_picture()
            except Exception:
                # The game ends the conversation on an error reply. Here it only says the picture couldn't be taken
                vision_log.exception("Show failed")
        self._log_sent(reply)
        return reply

    def __process_npc_initiative_check(self, received_json: dict[str, Any]) -> dict[str, Any]:
        """Never replies with an error: the game would end a conversation the player started meanwhile"""
        with self.__request_lock: # Only held to check the settings, which this may reload
            game = self.__game if self._can_route_be_used() else None
        self._log_received(received_json)
        if not game:
            game_master_log.error("NPC Initiative check skipped: MantellaSoftware settings faulty. Please check MantellaSoftware's window or log.")
            reply = {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_NPCINITIATIVE, comm_consts.KEY_NPCINITIATIVE_CHOSEN_REFID: 0}
        else:
            reply = game.npc_initiative_check(received_json)
        self._log_sent(reply)
        return reply

    def __process_npc_event_check(self, received_json: dict[str, Any]) -> dict[str, Any]:
        """Never replies with an error: the game would end a conversation the player started meanwhile"""
        with self.__request_lock: # Only held to check the settings, which this may reload
            game = self.__game if self._can_route_be_used() else None
        self._log_received(received_json)
        if not game:
            game_master_log.error("NPC Event check skipped: MantellaSoftware settings faulty. Please check MantellaSoftware's window or log.")
            reply = {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_NPCEVENT, comm_consts.KEY_NPCEVENT_ID: 0, comm_consts.KEY_NPCEVENT_PARTICIPANT_REFIDS: []}
        else:
            reply = game.npc_event_check(received_json)
        self._log_sent(reply)
        return reply

    def __process_request(self, received_json: dict[str, Any] | None, arrival: tuple[GameStateManager | None, int] | None = None) -> dict[str, Any]:
        """
        Args:
            arrival (tuple[GameStateManager | None, int] | None): the game manager when the request arrived, and how often the game had
                ended a conversation by then (see GameStateManager.start_conversation)
        """
        with self.__request_lock: # Requests other than interruptions are still handled one at a time
            if not self._can_route_be_used():
                error_message = "MantellaSoftware settings faulty. Please check MantellaSoftware's window or log."
                log.error(error_message)
                return self.error_message(error_message)
            if not self.__game:
                error_message = "Game manager setup failed. There is most likely an issue with the config.ini."
                log.error(error_message)
                return self.error_message(error_message)
            reply = {}
            if received_json:
                self._log_received(received_json)
                request_type: str = received_json[comm_consts.KEY_REQUESTTYPE]
                try:
                    match request_type:
                        case comm_consts.KEY_REQUESTTYPE_INIT:
                            # nothing needs to be done for this request aside from self._can_route_be_used() being triggered
                            log.debug('Mantella settings initialized')
                            reply = {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTTYPE_INITCOMPLETED,
                                     comm_consts.KEY_PLAYERSPEAKSFIRST: self.__game.player_speaks_first}
                        case comm_consts.KEY_REQUESTTYPE_LISTENBEFORESTART:
                            ends_before = arrival[1] if arrival and arrival[0] is self.__game else None
                            reply = self.__game.listen_before_start(received_json, ends_before)
                        case comm_consts.KEY_REQUESTTYPE_STARTCONVERSATION:
                            # Not after the settings changed meanwhile: the new game manager counts the ends from 0
                            ends_before = arrival[1] if arrival and arrival[0] is self.__game else None
                            reply = self.__game.start_conversation(received_json, ends_before)
                        case comm_consts.KEY_REQUESTTYPE_CONTINUECONVERSATION:
                            reply = self.__game.continue_conversation(received_json)
                        case comm_consts.KEY_REQUESTTYPE_PLAYERINPUT:
                            reply = self.__game.player_input(received_json)
                        case comm_consts.KEY_REQUESTTYPE_CONVERSATIONEVENT:
                            reply = self.__game.conversation_event(received_json)
                        case comm_consts.KEY_REQUESTTYPE_ENDCONVERSATION:
                            reply = self.__game.end_conversation(received_json)
                        case comm_consts.KEY_REQUESTTYPE_QUESTCHECKLIST:
                            reply = self.__game.quest_check_list()
                        case comm_consts.KEY_REQUESTTYPE_VOICECLIP_SAVELOADED:
                            reply = {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_VOICECLIP_SAVELOADED}
                        case _:
                            reply = self.error_message(f"Request type '{request_type}' was not recognized")
                except Exception:
                    # Otherwise the failure only reaches the game, as an HTTP error that ends the conversation, and never this log
                    log.exception(f"Request '{request_type}' failed")
                    raise
            else:
                reply = self.error_message(f"Request did not contain properly formatted json!")
            return reply # The route adds the voice clip requests and notices, on the reply that reaches the game (see __with_extras)

    def __process_voice_clips_delivered(self, received_json: dict[str, Any]) -> dict[str, Any]:
        """The game copied the lines of a request: they are taken in"""
        self._log_received(received_json)
        try:
            changed = self.__voice_clip_requests().collect()
            voice_clips_log.debug(f"Voice clips: request {received_json.get(comm_consts.KEY_VOICECLIP_REQUESTID, '')} delivered, {changed} clip(s) changed")
        except Exception:
            voice_clips_log.exception("Voice clips: could not take in what the game delivered")
        return {comm_consts.KEY_REPLYTYPE: comm_consts.KEY_REPLYTYPE_VOICECLIP_DELIVERED}

    def __voice_clip_requests(self):
        return clip_requests_for(shared_clip_store(voice_clips_folder(self._config.save_folder)))
