from enum import Enum
from threading import Thread, Lock
import time
from typing import Any
from src.llm.ai_client import AIClient
from src.llm.sentence_content import SentenceTypeEnum, SentenceContent, has_action
from src.characters_manager import Characters
from src.conversation.conversation_log import conversation_log
from src.conversation.conversation_serializer import serialize_persistent_messages, deserialize_persistent_messages
from src.conversation.action import Action
from src.actions.action_rules import LISTEN
from src.actions.function_manager import FunctionManager
from src.actions.tool_request import tool_request_for
from src.llm.sentence_queue import SentenceQueue
from src.llm.sentence import Sentence
from src.remember.remembering import Remembering
from src.remember.summaries import Summaries
from src.llm.client_base import ClientBase
from src.llm.image_client import VisionCapture
from src.llm.llm_client import LLMClient
from src.output_manager import ChatManager, log_raw_reply
from src.llm.messages import AssistantMessage, join_message, leave_message, nearby_join_message, overheard_message, SystemMessage, UserMessage
from src.conversation.context import add_or_update_result, context as Context
from src.llm.message_thread import message_thread
from src.conversation.conversation_type import conversation_type, multi_npc, multi_npc_game_master, npc_event, pc_to_npc, radiant, shout
from src.conversation.bystanders import NEWCOMERS_REMEMBER_EVERYTHING, NEWCOMERS_REMEMBER_LAST_LINE, EventPerson, ShoutState
from src.conversation.game_master import GameMaster, GameMasterTurns, game_master_history_lines, is_player_line
from src.conversation.ingame_event_tidy import tidy_events
from src.heard_lines.heard_line import HeardLine, HeardPerson
from src.heard_lines.heard_lines_store import HeardLinesStore
from src.heard_lines.heard_lines_text import HeardLinesText
from src.character_manager import Character
from src.http.communication_constants import communication_constants as comm_consts
from src.stt import Transcriber
from src.stt_backends import names as stt_names
import src.utils as utils
from src.config.config_loader import ConfigLoader
from src.config.definitions.prompt_definitions import PromptDefinitions
from src.prompt_profile_manager import PromptSectionError
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger, spoken_line

log = get_topic_logger(LogTopic.CONVERSATION)
game_master_log = get_topic_logger(LogTopic.GAME_MASTER)
ingame_events_log = get_topic_logger(LogTopic.INGAME_EVENTS)
vanilla_dialogues_log = get_topic_logger(LogTopic.VANILLA_DIALOGUES)
actions_log = get_topic_logger(LogTopic.ACTIONS)

class conversation_continue_type(Enum):
    NPC_TALK = 1
    PLAYER_TALK = 2
    END_CONVERSATION = 3

class Conversation:
    TOKEN_LIMIT_PERCENT: float = 0.9
    TOKEN_LIMIT_RELOAD_MESSAGES: float = 0.1
    DIRECTOR_CONTINUE_TEXT: str = "The scene continues."
    __shout_count: int = 0 # Each time Shout is switched on gets its own marker token
    __announce_private: bool = False # Shout was switched off: says who is left, once the game's updated list of people has arrived
    __event_count: int = 0 # Each Conversation, Nearby or Random Event gets its own marker, so someone who joins right after it is known to react to it
    EVENT_MARKER_ATTRIBUTE: str = "event_marker" # Set on the event's message. Kept when the thread is reloaded, which copies every message
    """Controls the flow of a conversation."""
    def __init__(self, context_for_conversation: Context, output_manager: ChatManager, rememberer: Remembering, llm_client: AIClient, stt: Transcriber | None, mic_input: bool, mic_ptt: bool,
                 heard_lines_text: HeardLinesText | None = None, heard_lines_store: HeardLinesStore | None = None) -> None:
        """
        Args:
            heard_lines_text (HeardLinesText | None): Vanilla Dialogues: writes the lines heard nearby into the next message
            heard_lines_store (HeardLinesStore | None): Vanilla Dialogues: keeps the lines heard during the conversation when it ends
        """
        self.__heard_lines_text: HeardLinesText | None = heard_lines_text
        self.__heard_lines_store: HeardLinesStore | None = heard_lines_store
        self.__heard_for_memory: list[HeardLine] = [] # Vanilla Dialogues: every line heard during the conversation, kept as heard lines when it ends

        self.__context: Context = context_for_conversation
        self.__mic_input: bool = mic_input
        self.__mic_ptt: bool = mic_ptt
        self.__allow_interruption: bool = context_for_conversation.config.allow_interruption # allow mic interruption
        self.__is_player_interrupting = False
        self.__is_interrupt_requested: bool = False # Set by the interrupt hotkey, from the thread of another request
        self.__awaiting_action_answer: bool = False # An action the NPC picked answers back (Advanced Actions): the game sends its answer next
        self.__action_asker: Character | None = None # The NPC whose action answers back
        self.__is_ending: bool = False
        self.__is_stopping: bool = False # The game ended the conversation, from the thread of another request. See stop_now
        self.__stt: Transcriber | None = stt
        self.__mic_names: stt_names.MicNames | None = None # built ahead of time, whenever the game sends new details (see __build_mic_names)
        self.__events_refresh_time: float = context_for_conversation.config.events_refresh_time  # Time in seconds before events are considered stale
        self.__transcribed_text: str | None = None
        if not self.__context.npcs_in_conversation.contains_player_character(): # TODO: fix this being set to a radiant conversation because of NPCs in conversation not yet being added
            self.__conversation_type: conversation_type = radiant(context_for_conversation.config)
        else:
            self.__conversation_type: conversation_type = pc_to_npc(context_for_conversation.config)
        self.__context.hides_player = lambda: self.hides_player() # A function, not a bound method: copying a message must not copy the conversation
        self.__was_directed: bool = False # The player directed the conversation at some point (Director Mode): its memory says they were not part of it
        self.__messages: message_thread = message_thread(self.__context.config, None, include_decensor_bypass=True)
        # When a new NPC joins mid-conversation, log the (updated) system prompt once.
        self.__log_prompt_on_next_regen: bool = False
        self.__output_manager: ChatManager = output_manager
        self.__rememberer: Remembering = rememberer
        self.__llm_client = llm_client
        self.__has_already_ended: bool = False
        self.__allow_mic_input: bool = True # this flag ensures mic input is disabled on conversation end
        self.__sentences: SentenceQueue = SentenceQueue()
        self.__generation_thread: Thread | None = None
        self.__generation_start_lock: Lock = Lock()
        # self.__actions: list[Action] = actions
        self.last_sentence_audio_length = 0
        self.last_sentence_start_time = time.time()
        # Memory requests ignore summaries/thoughts/reflections written since this time, so
        # regenerating one mid-conversation does not feed earlier attempts back in.
        self.__conversation_start_ts: int = int(time.time())
        self.__end_conversation_keywords = utils.parse_keywords(context_for_conversation.config.end_conversation_keyword)
        self.__game_master_turns: GameMasterTurns = GameMasterTurns(self.__context, self.__sentences)
        self.__is_waiting_for_room_answer: bool = False # Just saying something: the player said something to the room and nobody has answered yet
        self.__ended_unanswered: bool = False
        self.__is_waiting_for_speech: bool = False # A player input request waits for the player to speak into the mic
        self.__is_event_picked: bool = False # An event was picked on the Conversation Wheel and its request has not arrived yet. See stop_waiting_for_player
        self.__speech_wait_lock: Lock = Lock() # Keeps the two flags above together, as the event is picked from the thread of another request
        self.__shown_picture: VisionCapture | None = None # Show: taken when the player pressed Show, sent with their next line

    def update_summary_client(self, summary_client: ClientBase | None, fallback_client: LLMClient, thoughts_client: ClientBase | None, reflection_client: ClientBase | None) -> None:
        """Update the summary, thoughts and reflection LLM clients on the active rememberer, if it supports it."""
        if isinstance(self.__rememberer, Summaries):
            self.__rememberer.update_summary_client(summary_client, fallback_client, thoughts_client, reflection_client)

    @property
    def has_already_ended(self) -> bool:
        return self.__has_already_ended
    
    @property
    def is_stopping(self) -> bool:
        """The game ended the conversation (see stop_now). It ends here once the request still running is done"""
        return self.__is_stopping

    @property
    def start_ts(self) -> int:
        """When the conversation started (epoch seconds)"""
        return self.__conversation_start_ts

    @property
    def ended_unanswered(self) -> bool:
        """Just saying something: the player said something to the room, nobody answered, and the conversation ended"""
        return self.__ended_unanswered

    @property
    def context(self) -> Context:
        return self.__context
    
    @property
    def output_manager(self) -> ChatManager:
        return self.__output_manager
    
    @property
    def transcribed_text(self) -> str | None:
        return self.__transcribed_text
    
    @property
    def stt(self) -> Transcriber | None:
        return self.__stt

    def set_mic_setup(self, stt: Transcriber | None, mic_input: bool, mic_ptt: bool) -> None:
        """The microphone was switched on or off, or between push-to-talk and listening on its own, during the conversation.
        The player's next turn uses the new setup"""
        self.__stt = stt
        self.__mic_input = mic_input and stt is not None
        self.__mic_ptt = mic_ptt

    def npc_actions(self) -> list[Action]:
        """The actions the NPCs can use now, by the kind of conversation (Conversation page, Actions section)"""
        return self.__conversation_type.npc_actions(self.__context)

    def listen_longer(self):
        """Listen: the player gets more time to speak in their next turn. Only makes a difference with the microphone"""
        seconds = FunctionManager.get_action_pause_seconds(LISTEN)
        if self.__stt:
            self.__stt.set_temporary_pause(seconds)
        log.info(f"Listen: the player's next turn waits {seconds} seconds of silence before it ends")

    @utils.time_it
    def hot_swap_settings(self, config: ConfigLoader, llm_client: AIClient, chat_manager: ChatManager, rememberer: Remembering, stt: Transcriber | None = None) -> bool:
        """Attempts to hot-swap settings without ending the conversation.
        
        Args:
            config: Updated config loader instance
            llm_client: Updated LLM client instance
            chat_manager: Updated chat manager instance
            rememberer: Updated rememberer instance
            
        Returns:
            bool: True if hot-swap was successful, False otherwise
        """
        try:
            # A running Game Master turn reads the chat manager and conversation type again for each request,
            # so the reply generated ahead is still played and the requests after it use the new settings
            was_game_master = isinstance(self.__conversation_type, multi_npc_game_master)
            self.__game_master_turns.log_prompts_again() # The new settings can change the prompts

            # Update basic components
            self.__output_manager = chat_manager
            self.__rememberer = rememberer
            self.__llm_client = llm_client
            
            # Update config-derived values
            self.__allow_interruption = config.allow_interruption
            self.__events_refresh_time = config.events_refresh_time
            self.__end_conversation_keywords = utils.parse_keywords(config.end_conversation_keyword)
            # Optionally swap in a new STT transcriber (for heavy-change rebuilds)
            if stt is not None:
                self.__stt = stt
            
            # Update context with new config
            if self.__context:
                success = self.__context.hot_swap_settings(config, llm_client, rememberer)
                if not success:
                    return False
            
            # Update conversation type with new config
            self.__conversation_type = self.__select_conversation_type(config)
            self.__was_directed = self.__was_directed or self.is_directed()
            # Game Master Mode was turned off: the reply generated ahead would never be played. The player gets the turn after the reply being played
            if was_game_master and not isinstance(self.__conversation_type, multi_npc_game_master) and self.__game_master_turns.drop_lookahead():
                self.__stop_generation()

            # Regenerate system message with new prompt from updated conversation type
            if self.__messages and len(self.__messages) > 0:
                try:
                    new_prompt = self.__conversation_type.generate_prompt(self.__context)
                except PromptSectionError as e:
                    log.error(str(e))
                    if isinstance(self.__conversation_type, radiant):
                        self.__conversation_type.mark_prompt_unusable()
                else:
                    self.__conversation_type.adjust_existing_message_thread(new_prompt, self.__messages)
                
            # Update message thread with new config
            if self.__messages:
                self.__messages.hot_swap_settings(config)
            
            log.debug("Conversation hot-swap completed successfully")
            return True
            
        except Exception as e:
            log.error(f"Conversation hot-swap failed: {e}")
            return False

    @utils.time_it
    def add_or_update_character(self, new_character: list[Character]):
        """Adds or updates a character in the conversation.

        Args:
            new_character (Character): the character to add or update
        """
        update_result = self.__context.add_or_update_characters(new_character)
        if update_result.added_npcs:
            log.info(f"Joined the conversation: {', '.join(npc.name for npc in update_result.added_npcs)}")
        if update_result.removed_npcs:
            log.info(f"Left the conversation: {', '.join(npc.name for npc in update_result.removed_npcs)}")
        if self.__context.npc_event and any(npc.is_player_character for npc in update_result.added_npcs):
            self.__context.npc_event.on_player_joined()
        overheard_npcs: list[Character] = []
        shout_state = self.__context.shout
        if isinstance(shout_state, ShoutState):
            for npc in update_result.added_npcs:
                if not npc.is_player_character and shout_state.on_npc_joined(npc.ref_id):
                    overheard_npcs.append(npc) # Joined by shouting: heard everything since the player started shouting
        # If a non-player NPC joined, log the prompt the next time we regenerate it.
        # (Prompt regeneration itself happens in update_context() when have_actors_changed is true.)
        if any((not npc.is_player_character) for npc in update_result.added_npcs):
            self.__log_prompt_on_next_regen = True
        if len(update_result.removed_npcs) > 0:
            all_characters = self.__context.npcs_in_conversation.get_all_characters()
            all_characters.extend(update_result.removed_npcs)
            self.__save_conversation_log_for_characters(all_characters)
            
        # mark the joining / leaving of an npc in the message_thread
        for update_message in self.generate_add_or_remove_messages(update_result):
            if isinstance(update_message, join_message) and update_message.character.ref_id in [npc.ref_id for npc in overheard_npcs]:
                continue
            self.__messages.add_message(update_message)
        # Picked to react first to the event that was just added: they speak up about it, they don't walk over to the people talking
        reacts_to_event = (self.__event_count > 0 and len(self.__messages) > 0
                           and getattr(self.__messages.get_last_message(), self.EVENT_MARKER_ATTRIBUTE, None) == self.__event_count)
        for npc in overheard_npcs:
            self.__add_overheard_message(npc)
            # Where they actually join: who is already in the conversation, so they know they walked up to people already talking
            joined_together = [other.name for other in overheard_npcs]
            already_there = [name for name in self.__context.npcs_in_conversation.get_all_names() if name not in joined_together]
            self.__messages.add_message(nearby_join_message(npc, self.__context.config, already_there, reacts_to_event))

    def __add_overheard_message(self, npc: Character):
        """Shout: puts the join note of someone who joined by shouting where their memory of the conversation starts, as set by
        Newcomers Remember: where the player started shouting, the player's last line, or the start of the conversation.
        The prompts, the Game Master's history for each NPC and the summaries all go by where the join note is"""
        note = overheard_message(npc, self.__context.config)
        shout_state = self.__context.shout
        if not isinstance(shout_state, ShoutState):
            self.__messages.insert_overheard_message(note, None)
            return
        remember = shout_state.newcomers_remember
        token = None if remember == NEWCOMERS_REMEMBER_EVERYTHING else shout_state.start_marker
        from_last = is_player_line if remember == NEWCOMERS_REMEMBER_LAST_LINE else None
        self.__messages.insert_overheard_message(note, token, from_last)

    def start_shout(self, settings: dict[str, Any] | None):
        """Shout was switched on: anyone nearby can answer or join in. The conversation becomes a Shout conversation"""
        self.__shout_count += 1
        token = self.__shout_count
        shout_state = ShoutState(start_marker=token if self.__messages.mark_last_persistent_message(token) else None)
        shout_state.apply_settings(settings)
        self.__context.shout = shout_state
        self.__context.have_actors_changed = True # Selects the conversation type again
        game_master_log.info("Shout: the player is shouting. Anyone nearby can answer or join in")

    def __add_private_event(self):
        """Shout was switched off: an in-game event telling who is still in the now private conversation"""
        player = self.__context.npcs_in_conversation.get_player_character()
        npc_names = self.__context.get_character_names_as_text(False)
        if not player or not npc_names:
            return
        if self.__can_be_directed(self.__context.config):
            # Director Mode: the player is not part of the conversation, so they are not named
            self.__context.add_ingame_event("Nobody else joins the conversation now.")
            return
        self.__context.add_ingame_event(f"{player.name} is now speaking privately with {npc_names}.")

    def stop_shout(self):
        """Shout was switched off (Private). Nobody new joins. The game removes the people who joined by shouting itself, in the same request's actor update"""
        self.__context.shout = None
        self.__context.have_actors_changed = True
        self.__announce_private = True # Says who the conversation is with, after the actor update of the same request
        game_master_log.info("Shout: private again. Nobody new joins")

    def is_directed(self) -> bool:
        """The player directs the conversation (Director Mode) and is not part of the scene. Read from the current conversation type,
        so it follows the settings and the people in the conversation"""
        return self.__conversation_type.is_director_mode()

    def hides_player(self) -> bool:
        """Nothing about the player reaches the NPCs: the player directs the conversation, or watches an NPC Event with Director Mode on.
        While watching, the scene isn't directed (it runs on its own), but the player is still not part of it.
        Read from the type for the people in the conversation now: when a request adds or removes someone (a start, stepping into an NPC Event),
        its events are handled before the conversation switches to that type"""
        config = self.__context.config
        upcoming = self.__select_conversation_type(config)
        return upcoming.is_director_mode() or (isinstance(upcoming, npc_event) and multi_npc.is_director_enabled(config))

    def passes_ingame_events(self) -> bool:
        """Something that happens nearby reaches the NPCs with the next message. Not in a radiant conversation, or an NPC Event the player
        only watches: no message passes it on. Read from the type for the people in the conversation now, like hides_player"""
        upcoming = self.__select_conversation_type(self.__context.config)
        return not isinstance(upcoming, (radiant, npc_event))

    def add_heard_dialogue(self, lines: list[HeardLine]):
        """Vanilla Dialogues: Skyrim's own lines heard by people in the conversation. They go with the next message, and are kept as
        heard lines when the conversation ends"""
        self.__context.add_heard_dialogue(lines)
        self.__heard_for_memory.extend(lines)

    def event_history_lines(self) -> list[str]:
        """Conversation, Nearby and Random Events: the recent lines of the conversation for the event generator, the same ones the Game Master sees"""
        return game_master_history_lines(self.__messages.get_persistent_messages(), self.__context.config)

    def add_event(self, text: str, kind: str, shout_settings: dict[str, Any] | None, nearby: list[EventPerson], nearby_characters: dict[int, Character]):
        """Conversation, Nearby and Random Events: something happened. It is added as narration, so everyone in the conversation knows it
        and it ends up in their memories. Then the Game Master picks who reacts first, the player included.
        Called during the player's turn, so no NPC turn is running

        Args:
            kind (str): "conversation", "nearby" or "random". Nearby and Random switch Shout on, so the people nearby can join in
            shout_settings (dict[str, Any] | None): the Shout Mode settings, for switching Shout on
            nearby (list[EventPerson]): Nearby and Random: the people nearby who are not in the conversation, nearest first. Who the Game Master
                may bring in right away, if Shout was off until now (the game sends Shout's own list with its next request)
            nearby_characters (dict[int, Character]): Nearby and Random: by refid, the people nearby the game sent in full
        """
        with self.__generation_start_lock:
            self.__cancel_game_master_turn()
            self.__game_master_turns.on_player_input(kind) # Like a player line: the NPCs' turn starts again from the event, with the event's talk settings
            if isinstance(self.__context.shout, ShoutState):
                self.__context.shout.clear_pick() # Whoever still had to join or answer reacts to the event instead, if they are part of it
            self.__stop_generation()
            self.__sentences.clear()
            self.__is_interrupt_requested = False # Sent when the event was picked, to stop the NPCs before it. Not meant for the reaction
        if kind in ("nearby", "random"):
            if not isinstance(self.__context.shout, ShoutState):
                # Before the narration, so Newcomers Remember's Since Shout starts at the event
                self.start_shout(shout_settings)
                self.__context.shout.update_nearby(nearby)
            self.__context.shout.characters.update(nearby_characters)
        if isinstance(self.__context.shout, ShoutState):
            # The people a Nearby or Random Event is about react to it, even past Most People in Conversation. A Conversation Event is about the people in it
            self.__context.shout.event_ignores_max_people = kind in ("nearby", "random")
        event_message = self.__event_message(text)
        self.__event_count += 1
        setattr(event_message, self.EVENT_MARKER_ATTRIBUTE, self.__event_count)
        # Show: the replies to the event don't get the picture shown with the line before it. One shown since then still waits for the next line
        self.__messages.forget_shown_pictures()
        self.__messages.add_message(event_message)
        if self.__context.have_actors_changed: # Shout was switched on
            self.__update_conversation_type()
            self.__context.have_actors_changed = False
        if isinstance(self.__conversation_type, multi_npc_game_master):
            self.__start_generating_npc_sentences(allow_player_first=True)
        elif self.__does_npc_react_first():
            self.__start_generating_npc_sentences()

    def __event_message(self, text: str) -> UserMessage:
        """Something that happened, as an in-game event, like a custom game event. Its message has no text, as when the NPCs go on
        without the player saying anything. It comes after the in-game events since the last line, which would go with the player's next line"""
        message = UserMessage(self.__context.config, "", "", False)
        message.is_director_message = True # The player says nothing
        message.is_multi_npc_message = self.__context.npcs_in_conversation.contains_multiple_npcs()
        self.update_game_events(message)
        message.add_event([text])
        return message

    def __does_npc_react_first(self) -> bool:
        """A one-on-one conversation has no Game Master: the NPC always answers. After an event, the Game Master is asked once
        who reacts first, the NPC or the player. An NPC also reacts if there is no Game Master or no valid choice"""
        client = self.__output_manager.game_master_client
        if not isinstance(self.__conversation_type, pc_to_npc) or not client:
            return True
        # Only a one-on-one the player speaks in gets here (a directed one is never pc_to_npc), so the player may react first
        game_master = GameMaster(self.__context, multi_npc_game_master(self.__context.config, can_direct=False), client)
        game_master.choose_next_speaker(self.__messages, self.__messages.get_persistent_messages(), allow_player=True, after_event=True)
        return not game_master.picked_player

    @utils.time_it
    def generate_add_or_remove_messages(self, update_result: add_or_update_result) -> list[str]:
        add_or_remove_messages = []
        for npc in update_result.added_npcs:
            if not npc.is_player_character:
                add_or_remove_messages.append(join_message(npc, self.__context.config))
        for npc in update_result.removed_npcs:
            if not npc.is_player_character:
                add_or_remove_messages.append(leave_message(npc, self.__context.config))
        return add_or_remove_messages

    @utils.time_it
    def start_conversation(self) -> tuple[str, Sentence | None]:
        """Starts a new conversation.

        Returns:
            tuple[str, sentence | None]: Returns a tuple consisting of a reply type and an optional sentence
        """
        log.info(f"The conversation starts with: {', '.join(self.__context.npcs_in_conversation.get_all_names())}")
        if isinstance(self.__conversation_type, npc_event):
            # The scene starts as an in-game event, which the first speaker reacts to
            if self.__context.npc_event:
                self.__messages.add_message(self.__event_message(self.__context.npc_event.text))
            self.__start_generating_npc_sentences()
            return comm_consts.KEY_REPLYTYPE_NPCTALK, None
        greeting: UserMessage | None = self.__conversation_type.get_user_message(self.__context, self.__messages)
        if greeting:
            self.__messages.add_message(greeting)
            self.__start_generating_npc_sentences()
            return comm_consts.KEY_REPLYTYPE_NPCTALK, None
        else:
            return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None

    @utils.time_it
    def continue_conversation(self) -> tuple[str, Sentence | None]:
        """Main workhorse of the conversation. Decides what happens next based on the state of the conversation

        Returns:
            tuple[str, sentence | None]: Returns a tuple consisting of a reply type and an optional sentence
        """
        if self.has_already_ended or self.__is_stopping:
            return comm_consts.KEY_REPLYTYPE_ENDCONVERSATION, None
        if isinstance(self.__conversation_type, radiant) and self.__conversation_type.prompt_unusable:
            self.__has_already_ended = True
            return comm_consts.KEY_REPLYTYPE_ENDCONVERSATION, None        
        if self.__llm_client.is_too_long(self.__messages, self.TOKEN_LIMIT_PERCENT):
            # Check if conversation too long and if yes initiate intermittent reload
            self.__initiate_reload_conversation()

        # interrupt response if player has spoken
        if self.__stt and self.__stt.has_player_spoken:
            if len(self.__messages) == 1:
                # Nothing has been said yet: the microphone listened before the conversation started (listen_before_start), so this is
                # the player's first line, not an interruption. The player's input request picks it up
                return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None
            self.__cancel_game_master_turn()
            self.__stop_generation()
            self.__sentences.clear()
            self.__is_player_interrupting = True
            return comm_consts.KEY_REQUESTTYPE_TTS, None
        if self.__interrupt_for_player():
            return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None
        
        # restart mic listening as soon as NPC's first sentence is processed
        # Not while the player is not in the conversation (radiant, or a bystander of an NPC Event): what they say would interrupt a conversation they are not part of
        if self.__mic_input and self.__allow_interruption and not self.__mic_ptt and not self.__stt.is_listening and self.__allow_mic_input and not isinstance(self.__conversation_type, radiant) and self.__context.npcs_in_conversation.contains_player_character():
            mic_names = self.__get_mic_names()
            self.__stt.start_listening(mic_names.hint, mic_names.names)
        
        #Grab the next sentence from the queue. Private thoughts stay in history and are not spoken.
        #A spoken sentence is only added to the history once it is sent to the game: an interruption while waiting below throws it away unspoken
        next_sentence: Sentence | None = self.retrieve_sentence_from_queue(add_to_history=False)
        while next_sentence is not None and next_sentence.content.sentence_type == SentenceTypeEnum.THOUGHT:
            self.add_sentence_to_history(next_sentence)
            next_sentence = self.retrieve_sentence_from_queue(add_to_history=False)
        if self.__is_stopping: # The game ended the conversation while this waited
            return comm_consts.KEY_REPLYTYPE_ENDCONVERSATION, None
        # The hotkey stopped the wait before the LLM answered
        if next_sentence is None and self.__interrupt_for_player():
            return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None

        if next_sentence and has_action(next_sentence.actions, LISTEN): # Not sent to the game
            next_sentence.content.actions = [a for a in next_sentence.actions if a.get('identifier') != LISTEN]
            self.listen_longer()
        if next_sentence and not next_sentence.text.strip() and next_sentence.actions:
            # Actions without a line, e.g. the ones the AI model picked (Advanced Actions): sent to the game right away
            FunctionManager.record_shares(next_sentence.actions, self.__context.npcs_in_conversation) # Not before: an interrupted reply shares nothing
            if FunctionManager.any_action_requires_response(next_sentence.actions):
                self.__awaiting_action_answer = True # The game answers with its next request, then the NPC goes on (resume_after_action)
                self.__action_asker = next_sentence.speaker
                if isinstance(self.__conversation_type, multi_npc_game_master):
                    self.__game_master_turns.action_sent() # The reply waits for the answer from now on
            return comm_consts.KEY_REPLYTYPE_NPCACTION, next_sentence
        if next_sentence and len(next_sentence.text) > 0:
            if has_action(next_sentence.actions, comm_consts.ACTION_REMOVECHARACTER):
                self.__context.remove_character(next_sentence.speaker)
            #if there is a next sentence and it actually has content, return it as something for an NPC to say
            if self.last_sentence_audio_length > 0:
                log.debug(f'Waiting {round(self.last_sentence_audio_length, 1)} seconds for last voiceline to play')
            # before immediately sending the next voiceline, give the player the chance to interrupt
            while time.time() - self.last_sentence_start_time < self.last_sentence_audio_length:
                if self.__is_stopping:
                    return comm_consts.KEY_REPLYTYPE_ENDCONVERSATION, None
                if self.__stt and self.__stt.has_player_spoken:
                    self.__cancel_game_master_turn()
                    self.__stop_generation()
                    self.__sentences.clear()
                    self.__is_player_interrupting = True
                    return comm_consts.KEY_REQUESTTYPE_TTS, None
                if self.__interrupt_for_player():
                    return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None
                time.sleep(0.01)
            # The hotkey may have been pressed while waiting for this sentence to be generated
            if self.__interrupt_for_player():
                return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None
            self.last_sentence_audio_length = next_sentence.voice_line_duration + self.__context.config.wait_time_buffer
            self.last_sentence_start_time = time.time()
            self.add_sentence_to_history(next_sentence)
            return comm_consts.KEY_REPLYTYPE_NPCTALK, next_sentence
        else:
            #Ask the conversation type here, if we should end the conversation
            if self.__conversation_type.should_end(self.__context, self.__messages):
                self.initiate_end_sequence()
                return comm_consts.KEY_REPLYTYPE_NPCTALK, None
            elif isinstance(self.__conversation_type, multi_npc_game_master):
                # The next reply is generated while the last one is played, so it is usually ready by now
                has_player_interrupted = lambda: bool(self.__stt and self.__stt.has_player_spoken) or self.__is_interrupt_requested or self.__is_stopping
                has_next_reply = self.__game_master_turns.play_next_reply(self.__context.config.llm_response_timeout, has_player_interrupted)
                if self.__is_stopping:
                    return comm_consts.KEY_REPLYTYPE_ENDCONVERSATION, None
                if self.__interrupt_for_player(): # The interrupt hotkey: the NPCs stop now
                    return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None
                if has_player_interrupted():
                    # The next continue request handles the interruption (the player spoke into the mic)
                    return comm_consts.KEY_REPLYTYPE_NPCTALK, None
                if has_next_reply:
                    return comm_consts.KEY_REPLYTYPE_NPCTALK, None
                if isinstance(self.__conversation_type, npc_event):
                    return self.__continue_npc_event()
                if isinstance(self.__conversation_type, shout):
                    return self.__continue_shout()
                return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None
            else:
                #If not ended, ask the conversation type for an automatic user message. If there is None, signal the game that the player must provide it 
                new_user_message = self.__conversation_type.get_user_message(self.__context, self.__messages)
                if new_user_message:
                    self.__messages.add_message(new_user_message)
                    self.__start_generating_npc_sentences()
                    return comm_consts.KEY_REPLYTYPE_NPCTALK, None
                else:
                    return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None

    def __continue_npc_event(self) -> tuple[str, Sentence | None]:
        """NPC Events: what happens once the NPCs' turn is over. Without the player, the scene goes on until its line limit.
        The scene runs in Shout, so the people nearby the Game Master picks are added by Shout. Only the player is added by the event"""
        event = self.__context.npc_event
        if not event:
            return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None
        if event.pending_add:
            # The game adds them first, as only actors in the conversation can speak in game
            return comm_consts.KEY_REPLYTYPE_NPCEVENT_ADDACTOR, None
        shout_state = self.__context.shout
        if isinstance(shout_state, ShoutState) and shout_state.pending_add:
            return comm_consts.KEY_REPLYTYPE_SHOUT_ADDACTOR, None
        if isinstance(shout_state, ShoutState) and (shout_state.forced_speaker_ref_id or shout_state.answer_first_ref_id):
            self.__start_generating_npc_sentences()
            return comm_consts.KEY_REPLYTYPE_NPCTALK, None
        if event.player_joined:
            return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None
        if event.has_failed or event.has_reached_limit(self.__context.config):
            return comm_consts.KEY_REPLYTYPE_ENDCONVERSATION, None
        if self.__game_master_turns.nobody_left_to_speak(self.__conversation_type):
            game_master_log.info("NPC Event: everyone in the scene has reached Most Replies per Person. The scene ends.")
            return comm_consts.KEY_REPLYTYPE_ENDCONVERSATION, None
        # The Game Master gave no valid choice, or the turn was thrown away. The scene goes on
        self.__start_generating_npc_sentences()
        return comm_consts.KEY_REPLYTYPE_NPCTALK, None

    def __continue_shout(self) -> tuple[str, Sentence | None]:
        """Shout: what happens once the NPCs' turn is over"""
        shout_state = self.__context.shout
        if not isinstance(shout_state, ShoutState):
            return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None
        if shout_state.pending_add:
            # The game adds them first, as only actors in the conversation can speak in game
            return comm_consts.KEY_REPLYTYPE_SHOUT_ADDACTOR, None
        if shout_state.forced_speaker_ref_id or shout_state.answer_again or shout_state.answer_first_ref_id or self.__is_answer_due(shout_state):
            # The person who joined speaks next, or the person picked to answer the player could not be added and someone else answers,
            # or someone a line was aimed at has not answered it yet
            self.__start_generating_npc_sentences()
            return comm_consts.KEY_REPLYTYPE_NPCTALK, None
        if self.__is_waiting_for_room_answer and shout.is_said_to_room(self.__context):
            return self.__end_unanswered()
        self.__is_waiting_for_room_answer = False
        return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None

    def __is_answer_due(self, shout_state: ShoutState) -> bool:
        """Someone in the conversation a line was aimed at has not answered it yet, e.g. the turn ended to add someone who then could not join"""
        if not shout_state.still_to_answer:
            return False
        return any(npc.ref_id in shout_state.still_to_answer for npc in self.__context.get_characters_excluding_player().get_all_characters())

    def __end_unanswered(self) -> tuple[str, Sentence | None]:
        """Just saying something: nobody nearby answered what the player said to the room, so the conversation ends"""
        log.info("Nobody answers. The conversation ends")
        self.__has_already_ended = True
        self.__ended_unanswered = True
        if self.__stt:
            self.__stt.stop_listening()
            self.__allow_mic_input = False
        return comm_consts.KEY_REPLYTYPE_ENDCONVERSATION, None

    def stop_waiting_for_player(self):
        """An event was picked on the Conversation Wheel. A player input request that waits for the player to speak returns without a line,
        so the game can send the event. Called while that request may still be running, also just before it starts waiting:
        then it does not wait at all. Remembered until the wait or the event request (forget_event_picked)"""
        with self.__speech_wait_lock:
            self.__is_event_picked = True
            if self.__is_waiting_for_speech and self.__stt:
                self.__stt.cancel_wait()

    def forget_event_picked(self):
        """The event request arrived, so a pick no wait for speech used is forgotten (the NPCs were talking, or the player types)"""
        with self.__speech_wait_lock:
            self.__is_event_picked = False

    @utils.time_it
    def process_player_input(self, player_text: str, details_follow: bool = False) -> tuple[str | None, bool, Sentence|None]:
        """Submit the input of the player to the conversation

        Args:
            player_text (str): The input text / voice transcribe of what the player character is supposed to say. Can be empty if mic input has not yet been parsed
            details_follow (bool): the game sent no in-game events with the empty text, so the mic starts listening right away. Once the
                player has spoken, the events always need updating

        Returns:
            tuple[str | None, bool]: Returns a tuple consisting of updated player text (if using mic input) and whether or not in-game events need to be refreshed (depending on how much time has passed).
                The text is None if the player picked an event instead of speaking (see stop_waiting_for_player)
        """
        player_character = self.__context.npcs_in_conversation.get_player_character()
        if not player_character:
            return '', False, None # If there is no player in the conversation, exit here
        
        events_need_updating: bool = False

        with self.__generation_start_lock: #This lock makes sure no new generation by the LLM is started while we clear this
            self.__cancel_game_master_turn() # Before stopping, so a running Game Master turn sees it was discarded
            self.__game_master_turns.on_player_input()
            if isinstance(self.__conversation_type, shout) and isinstance(self.__context.shout, ShoutState):
                self.__context.shout.clear_pick() # A pick from the turn thrown away is not added
            self.__stop_generation() # Stop generation of additional sentences right now
            self.__sentences.clear() # Clear any remaining sentences from the list
            self.__context.npc_initiative = False # Once the player answers, the NPC Initiative Prompt gives way to the normal prompt

            # If the player's input does not already exist, parse mic input if mic is enabled
            if self.__mic_input and len(player_text) == 0:
                player_text = None
                if not self.__stt.is_listening and self.__allow_mic_input:
                    mic_names = self.__get_mic_names()
                    self.__stt.start_listening(mic_names.hint, mic_names.names)
                
                # Start tracking how long it has taken to receive a player response
                input_wait_start_time = time.time()
                with self.__speech_wait_lock:
                    # An event picked just before, or the conversation ended: no wait
                    self.__is_waiting_for_speech = not self.__is_event_picked and not self.__is_stopping
                    if self.__is_waiting_for_speech:
                        self.__stt.reset_cancel_wait()
                try:
                    # Empty only if stop_waiting_for_player stopped the wait
                    player_text = self.__stt.get_latest_transcription() if self.__is_waiting_for_speech else ""
                finally:
                    with self.__speech_wait_lock:
                        self.__is_waiting_for_speech = False
                        self.__is_event_picked = False
                if not player_text:
                    # The player picked an event instead of speaking. It is still their turn
                    self.__stt.stop_listening()
                    return None, False, None
                # Stop listening once input has been detected to give the NPC a chance to speak
                # This also needs to apply when interruptions are allowed,
                # otherwise the player could constantly speak over the NPC and never hear a response
                self.__stt.stop_listening()

                if details_follow or time.time() - input_wait_start_time >= self.__events_refresh_time:
                    # The game sent no events, or too much time has passed: the game sends the text again with its events
                    events_need_updating = True
                    log.debug('Updating game events...')
                    return player_text, events_need_updating, None
            
            self.__is_interrupt_requested = False # Pressing the interrupt hotkey during the player's turn has nothing to interrupt
            is_director = self.__conversation_type.is_director_mode()
            self.__was_directed = self.__was_directed or is_director # Also when only "." was sent: the player was still not part of it
            # Director mode and Game Master Mode: "." lets the NPCs keep talking without new player input
            is_continue = (is_director or isinstance(self.__conversation_type, multi_npc_game_master)) and player_text.strip() == "."
            new_message: UserMessage = UserMessage(self.__context.config, "" if is_continue else player_text, player_character.name, False)
            new_message.is_multi_npc_message = self.__context.npcs_in_conversation.contains_multiple_npcs()
            new_message.is_director_message = is_director or is_continue # A continue message only carries events, so no player name is shown
            new_message = self.update_game_events(new_message)
            self.__messages.forget_shown_pictures() # A picture shown with an earlier line isn't sent again
            picture, self.__shown_picture = self.__shown_picture, None
            if picture:
                new_message.shown_picture = picture
                # Stays in the conversation after the picture is gone, so later lines still make sense. In Director Mode the player's
                # character isn't part of the scene: it is the scene as it looks now
                new_message.add_event(["This is how the scene looks right now." if is_director
                                       else f"{player_character.name} shows you what they are looking at."])
            if not is_continue or new_message.count_ingame_events() > 0 or new_message.get_heard_dialogue():
                self.__messages.add_message(new_message)
            elif not isinstance(self.__conversation_type, multi_npc_game_master):
                # The LLM needs a new message to reply to. Flagged as system generated, so summaries leave it out.
                # Game Master Mode needs none: each NPC request adds its own
                new_message.text = self.DIRECTOR_CONTINUE_TEXT
                new_message.is_system_generated_message = True
                self.__messages.add_message(new_message)
            player_voiceline = None if is_director or is_continue else self.__get_player_voiceline(player_character, player_text)
            text = "" if is_continue else new_message.text
            if text.strip() and not is_director:
                spoken_line(log, player_character.name, text.strip(), by_player=True)
            is_said_to_room = isinstance(self.__conversation_type, shout) and shout.is_said_to_room(self.__context)

        if is_said_to_room:
            # Nobody to dismiss or say goodbye to. The Game Master decides whether someone nearby answers
            self.__is_waiting_for_room_answer = True
            self.__start_generating_npc_sentences()
            return player_text, events_need_updating, player_voiceline
        ejected_npc = self.__does_dismiss_npc_from_conversation(text)
        if ejected_npc:
            self.__prepare_eject_npc_from_conversation(ejected_npc)
        elif self.__has_conversation_ended(text):
            new_message.is_system_generated_message = True # Flag message containing goodbye as a system message to exclude from summary
            self.initiate_end_sequence()
        else:
            self.__start_generating_npc_sentences()

        return player_text, events_need_updating, player_voiceline

    def __get_mic_names(self) -> stt_names.MicNames:
        """The names sent with the player's recording. Built when the game sent its details; the microphone starts before this turn's details
        arrive, so these are the last turn's"""
        if self.__mic_names is None:
            self.__build_mic_names()
        return self.__mic_names or stt_names.MicNames()

    def __build_mic_names(self) -> None:
        """Fills the Speech-to-Text Names prompt: the NPCs in the conversation, the other NPCs nearby, the location and the player's name"""
        if not self.__mic_input or self.__stt is None:
            return
        try:
            characters = self.__context.npcs_in_conversation
            self.__mic_names = stt_names.build(
                getattr(self.__context.config, "stt_names_prompt", PromptDefinitions.STT_NAMES_PROMPT),
                self.__context.get_characters_excluding_player().get_all_names(),
                characters.get_nearby_npc_names(),
                self.__context.location or "",
                characters.get_player_name() or "",
                first_names=bool(getattr(self.__context.config, "stt_first_names", False)),
            )
        except Exception as e:
            log.warning(f"Could not make the speech-to-text names: {e}")
    
    @utils.time_it
    def __get_player_voiceline(self, player_character: Character | None, player_text: str) -> Sentence | None:
        """Synthesizes the player's input if player voice input is enabled, or else returns None
        """
        player_character_voiced_sentence: Sentence | None = None
        if self.__should_voice_player_input(player_character):
            player_character_voiced_sentence = self.__output_manager.generate_sentence(SentenceContent(player_character, player_text, SentenceTypeEnum.SPEECH, False))
            if player_character_voiced_sentence.error_message:
                player_message_content: SentenceContent = SentenceContent(player_character, player_text, SentenceTypeEnum.SPEECH, False)
                player_character_voiced_sentence = Sentence(player_message_content, "" , 2.0)

        return player_character_voiced_sentence

    @utils.time_it
    def update_context(self, location: str | None, time: int, custom_ingame_events: list[str], weather: str, custom_context_values: dict[str, Any]):
        """Updates the context with a new set of values

        Args:
            location (str): the location the characters are currently in
            time (int): the current ingame time
            custom_ingame_events (list[str]): a list of events that happend since the last update
            custom_context_values (dict[str, Any]): the current set of context values
        """
        # Snapshot prompt-relevant values so we can refresh the system prompt if they change.
        old_location = self.__context.location
        old_time = self.__context.ingame_time
        old_weather = self.__context.weather
        old_equipment = self.__context.get_npc_equipment_snapshot()
        old_player_equipment = self.__context.get_player_equipment_snapshot()

        self.__context.update_context(location, time, custom_ingame_events, weather, custom_context_values)
        self.__build_mic_names() # the game sends who is nearby and the location just before this
        if self.__announce_private:
            self.__announce_private = False
            self.__add_private_event()

        # Keep {location}/{time}/{weather}/{equipment}/{player_equipment} in the system prompt aligned with current game state.
        # Do NOT remove system-generated messages here; that is reserved for actor/type changes.
        try:
            new_equipment = self.__context.get_npc_equipment_snapshot()
            new_player_equipment = self.__context.get_player_equipment_snapshot()
            prompt_context_changed = (
                old_location != self.__context.location or
                old_time != self.__context.ingame_time or
                old_weather != self.__context.weather or
                old_equipment != new_equipment or
                old_player_equipment != new_player_equipment
            )
            if prompt_context_changed and self.__messages and len(self.__messages) > 0:
                new_prompt = self.__conversation_type.generate_prompt(self.__context)
                is_multi = self.__context.npcs_in_conversation.contains_multiple_npcs()
                self.__messages.modify_messages(
                    new_prompt,
                    multi_npc_conversation=is_multi,
                    remove_system_flagged_messages=False
                )
        except PromptSectionError as e:
            log.error(str(e))
            log.error("NPC reply was not generated. The conversation is waiting. Fix the active prompt profile, or end the conversation.")
        except Exception as e:
            log.warning(f"Failed to refresh system prompt after context update: {e}")

        if self.__context.have_actors_changed:
            self.__update_conversation_type()
            self.__context.have_actors_changed = False

    @utils.time_it
    def __update_conversation_type(self):
        """This changes between pc_to_npc, multi_npc and radiant conversation_types based on the current state of the context
        """
        # If the conversation can proceed for the first time, it starts and we add the system_message with the prompt
        if not self.__has_already_ended:
            self.__cancel_game_master_turn()
            self.__stop_generation()
            self.__sentences.clear()

            self.__conversation_type = self.__select_conversation_type(self.__context.config)
            self.__was_directed = self.__was_directed or self.is_directed()

            # Only log the prompt when creating the initial message thread (conversation start),
            # or when a new NPC has joined and we regenerated the prompt.
            # Note: NPC join/leave messages can be added before the initial system prompt is created,
            # so "len == 0" is not a reliable indicator. We log only when no SystemMessage exists yet.
            is_initial_prompt = not self.__messages.has_message_type(SystemMessage)
            should_log_prompt = is_initial_prompt or self.__log_prompt_on_next_regen
            try:
                new_prompt = self.__conversation_type.generate_prompt(self.__context, should_log=should_log_prompt)
            except PromptSectionError as e:
                log.error(str(e))
                self.__log_prompt_on_next_regen = False
                if isinstance(self.__conversation_type, radiant):
                    self.__conversation_type.mark_prompt_unusable()
                    log.error("Radiant conversation will end because the prompt could not be built.")
                else:
                    log.error("NPC reply was not generated. The conversation is waiting. Fix the active prompt profile, or end the conversation.")
                return
            # Reset after use so we only log once per join event.
            self.__log_prompt_on_next_regen = False
            if is_initial_prompt:
                self.__messages: message_thread = message_thread(self.__context.config, new_prompt, include_decensor_bypass=True)
            else:
                self.__conversation_type.adjust_existing_message_thread(new_prompt, self.__messages)
                self.reload_and_cull_thread(new_prompt)

    @utils.time_it
    def reload_and_cull_thread(self, new_prompt):
        """Reloads the message thread and removes old messages if getting close to context limits"""
        removed_messages = self.__messages.reload_message_thread(new_prompt, self.__llm_client.is_too_long, self.TOKEN_LIMIT_RELOAD_MESSAGES)
        if len(removed_messages) > 0:
            removed_messages_thread = message_thread(self.__context.config, None)
            for message in removed_messages:
                removed_messages_thread.add_message(message)
            characters = self.__context.npcs_in_conversation.get_all_characters()
            # Log the removed messages. The kept ones are logged when the conversation ends, so they are not logged here
            self.__save_conversation_log_for_characters(characters, removed_messages_thread)
            # Summarize the removed messages
            self.__save_summary_for_characters(removed_messages_thread, characters, True, save_log=False)
        

    @utils.time_it
    def update_game_events(self, message: UserMessage) -> UserMessage:
        """Add in-game events to player's response"""

        # Merged before the limit below, so the limit counts the tidied lines
        all_ingame_events = tidy_events(self.__context.get_context_ingame_events())
        if self.__is_player_interrupting:
            if not message.is_director_message: # Director mode or a continue: the player says nothing, so there is nobody interrupting
                all_ingame_events.append('Interrupting...')
            self.__is_player_interrupting = False
        max_events = min(len(all_ingame_events) ,self.__context.config.max_count_events)
        message.add_event(all_ingame_events[-max_events:])
        self.__context.clear_context_ingame_events()        

        if message.count_ingame_events() > 0:
            ingame_events_log.info(f'In-game events since previous exchange:\n{message.get_ingame_events_text()}')

        self.__add_heard_dialogue_to(message)
        return message

    def __add_heard_dialogue_to(self, message: UserMessage):
        """Vanilla Dialogues: the lines heard nearby since the previous exchange, as narration apart from the in-game events.
        At most Most Vanilla Dialogue Lines per NPC, the newest"""
        lines = self.__context.get_heard_dialogue()
        if not lines:
            return
        self.__context.clear_heard_dialogue()
        if not self.__heard_lines_text:
            return
        hide_player = self.hides_player()
        # A line someone in the conversation said, or that was said to them, isn't "nearby". The player only counts while part of it
        in_conversation = {person.ref_id for person in self.__context.npcs_in_conversation.get_all_characters()
                           if not (hide_player and person.is_player_character)}
        texts = [text for text in (self.__heard_lines_text.nearby_now(line, hide_player, in_conversation) for line in sorted(lines, key=lambda l: l.time)) if text]
        message.add_heard_dialogue(texts[-max(1, self.__context.config.heard_lines_most_per_npc):])
        if message.get_heard_dialogue():
            vanilla_dialogues_log.debug(f'Heard nearby since previous exchange:\n{message.get_heard_dialogue_text()}')

    def __store_heard_dialogue(self):
        """Vanilla Dialogues: when the conversation ends, every line heard during it is kept as a heard line by the people in it who heard it,
        whether or not it went with a message and whether or not a summary is written. Not before: while the conversation runs, the line
        is already in it, and a prompt built again (a join, each Game Master turn) would show it twice"""
        lines = self.__heard_for_memory
        self.__heard_for_memory = []
        self.__context.clear_heard_dialogue()
        if not lines or not self.__heard_lines_store:
            return
        by_listener: dict[HeardPerson, list[HeardLine]] = {}
        for line in lines:
            for person in line.heard_by:
                by_listener.setdefault(person, []).append(line)
        for person, person_lines in by_listener.items():
            try: # One NPC that can't be written loses no one else's lines
                self.__heard_lines_store.add(self.__context.world_id, person, person_lines)
            except Exception as e:
                vanilla_dialogues_log.error(f"Vanilla Dialogues: could not keep the lines {person.name} heard during the conversation: {e}")

    @utils.time_it
    def retrieve_sentence_from_queue(self, add_to_history: bool = True) -> Sentence | None:
        """Retrieves the next sentence from the queue.
        If there is a sentence, adds the sentence to the last assistant_message of the message_thread.
        If the last message is not an assistant_message, a new one will be added.

        Args:
            add_to_history (bool): False to add it later with add_sentence_to_history, e.g. only once it is sent to the game

        Returns:
            sentence | None: The next sentence from the queue or None if the queue is empty
        """
        # The interrupt hotkey stops the wait, e.g. while the LLM has not answered yet. So does the end of the conversation
        is_interrupted = lambda: (self.__is_interrupt_requested and not self.__is_ending) or self.__is_stopping
        next_sentence: Sentence | None = self.__sentences.get_next_sentence(is_interrupted) #This is a blocking call. Execution will wait here until queue is filled again
        if next_sentence and next_sentence.raw_reply_log:
            # The end of a reply: its lines have all been sent to the game, so the raw reply comes after them, as it always did
            log_raw_reply(next_sentence.raw_reply_log)
        if next_sentence and add_to_history:
            self.add_sentence_to_history(next_sentence)
        return next_sentence

    def add_sentence_to_history(self, next_sentence: Sentence):
        if not next_sentence.is_system_generated_sentence and not next_sentence.speaker.is_player_character:
            last_message = self.__messages.get_last_message()
            if not isinstance(last_message, AssistantMessage):
                last_message = AssistantMessage(self.__context.config)
                last_message.is_multi_npc_message = self.__context.npcs_in_conversation.contains_multiple_npcs()
                last_message.thoughts_in_place = isinstance(self.__conversation_type, multi_npc_game_master)
                self.__messages.add_message(last_message)
            last_message.add_sentence(next_sentence)

    @utils.time_it
    def initiate_end_sequence(self):
        """Replaces all remaining sentences with a "goodbye" sentence that also prompts the game to request a stop to the conversation using an action
        """
        if not self.__has_already_ended:
            config = self.__context.config            
            self.__is_ending = True # The goodbye must not be interrupted, it carries the action that ends the conversation in game
            self.__cancel_game_master_turn()
            self.__stop_generation()
            self.__sentences.clear()
            if self.__stt:
                self.__stt.stop_listening()
                self.__allow_mic_input = False
            # say goodbyes
            npc = self.__context.npcs_in_conversation.last_added_character
            if npc:
                goodbye_sentence = self.__output_manager.generate_sentence(SentenceContent(npc, config.goodbye_npc_response, SentenceTypeEnum.SPEECH, True))
                if goodbye_sentence:
                    goodbye_sentence.actions.append({'identifier': comm_consts.ACTION_ENDCONVERSATION})
                    self.__sentences.put(goodbye_sentence)
                    
    @utils.time_it
    def contains_character(self, ref_id: str) -> bool:
        for actor in self.__context.npcs_in_conversation.get_all_characters():
            if actor.ref_id == ref_id:
                return True
        return False
    
    @utils.time_it
    def get_character(self, ref_id: str) -> Character | None:
        for actor in self.__context.npcs_in_conversation.get_all_characters():
            if actor.ref_id == ref_id:
                return actor
        return None

    @utils.time_it
    def end(self):
        """Ends a conversation
        """
        log.info("The conversation ends")
        self.__has_already_ended = True
        self.__cancel_game_master_turn()
        self.__stop_generation()
        self.__sentences.clear()
        self.__store_heard_dialogue()
        self.__save_summary()
    
    def can_be_shown(self) -> bool:
        """Show only works in a conversation the player is in"""
        return not self.__has_already_ended and self.__context.npcs_in_conversation.contains_player_character()

    def show_picture(self, picture: VisionCapture) -> bool:
        """Show: the picture goes with the player's next line. A picture shown before and not sent yet is replaced.
        Called from the thread of another request"""
        if not self.can_be_shown():
            return False
        self.__shown_picture = picture
        log.info("The player shows the NPCs what they see. The picture goes with their next line")
        return True

    def request_interrupt(self):
        """Stops the NPCs when an event is picked on the Conversation Wheel, or cancelled.
        Called while a continue request may still be running, which picks the interruption up"""
        if self.__context.npcs_in_conversation.contains_player_character() or self.__context.npc_event:
            self.__is_interrupt_requested = True

    def interrupt_now(self) -> bool:
        """Interrupt hotkey. The NPCs stop and the player gets the turn, like when the player interrupts with the mic.
        Called while a request may still be running: the game is told it is the player's turn right away, without waiting for it.
        That request picks the interruption up and stops the NPCs.
        During an NPC Event it is also how a bystander player steps in: the game sends them with their input, after the interruption

        Returns:
            bool: False if there is nothing to interrupt: the player isn't part of it, or the goodbye that ends the conversation is being said
        """
        if self.__is_ending or self.__has_already_ended or self.__is_stopping:
            return False
        if not (self.__context.npcs_in_conversation.contains_player_character() or self.__context.npc_event):
            return False
        self.__is_interrupt_requested = True
        self.__is_player_interrupting = True # Also when the request still running never gets to stop the NPCs (it waits for a Game Master turn)
        return True

    def stop_now(self):
        """The game ended the conversation. Called while a request may still be running: whatever it waits for (the AI model, the
        player's voice, a Game Master turn) stops waiting, so it ends right away. The conversation itself ends with end(), after it"""
        with self.__speech_wait_lock:
            self.__is_stopping = True
            if self.__is_waiting_for_speech and self.__stt:
                self.__stt.cancel_wait()

    def __interrupt_for_player(self) -> bool:
        """Stops the NPCs' response if the interrupt hotkey was pressed

        Returns:
            bool: True if the response was stopped and the player gets the turn
        """
        if not self.__is_interrupt_requested or self.__is_ending:
            return False
        self.__is_interrupt_requested = False
        log.info("Player interrupted. The player gets the turn.")
        self.__cancel_game_master_turn()
        self.__stop_generation()
        self.__sentences.clear()
        self.__is_player_interrupting = True
        return True

    def __cancel_game_master_turn(self):
        """Game Master Mode only. Throws away the running NPC turn, so nothing more is generated for it"""
        if isinstance(self.__conversation_type, multi_npc_game_master):
            self.__game_master_turns.cancel()

    def __select_conversation_type(self, config: ConfigLoader) -> conversation_type:
        """The conversation type for the people in the conversation now"""
        characters = self.__context.npcs_in_conversation
        event = self.__context.npc_event
        if event and not event.player_joined and npc_event.is_enabled(config) and self.__output_manager.game_master_client:
            # Before the radiant check: the event runs without the player. Once they join, it is a normal Shout conversation
            return npc_event(config)
        if not characters.contains_player_character():
            return radiant(config)
        can_direct = not self.__context.started_by_npc
        if isinstance(self.__context.shout, ShoutState) and shout.is_enabled(config) and self.__output_manager.game_master_client:
            # Shout always runs with a Game Master, whether or not Game Master Mode is enabled
            return shout(config, can_direct)
        if characters.active_character_count() >= 3:
            return self.__create_multi_npc_type(config)
        if self.__can_be_directed(config) and characters.active_character_count() >= 2:
            # Director Mode: one NPC is directed too, with the director prompts of a group conversation
            return self.__create_multi_npc_type(config)
        return pc_to_npc(config)

    def __can_be_directed(self, config: ConfigLoader) -> bool:
        """Director Mode is on, and the player started this conversation (one an NPC started is never directed)"""
        return multi_npc.is_director_enabled(config) and not self.__context.started_by_npc

    def __create_multi_npc_type(self, config: ConfigLoader) -> multi_npc:
        """Game Master Mode when it is enabled and its LLM client exists, otherwise the regular multi-NPC conversation"""
        can_direct = not self.__context.started_by_npc
        if multi_npc_game_master.is_enabled(config) and self.__output_manager.game_master_client:
            return multi_npc_game_master(config, can_direct)
        return multi_npc(config, can_direct)

    @utils.time_it
    def resume_after_action(self) -> bool:
        """An action the NPC picked answers back (Advanced Actions, e.g. CheckDirections), and the game has sent its answer with this request:
        it is added to the conversation as in-game events, and the NPC goes on with it. Looking it up and the line after count as one reply,
        so in Game Master Mode nobody else speaks in between. The line after can't pick another action that answers back

        Returns:
            bool: True if the conversation was waiting for an answer
        """
        if not self.__awaiting_action_answer:
            return False
        self.__awaiting_action_answer = False
        player = self.__context.npcs_in_conversation.get_player_character()
        message = UserMessage(self.__context.config, "", player.name if player else "", True)
        message.is_multi_npc_message = self.__context.npcs_in_conversation.contains_multiple_npcs()
        message.is_director_message = True # Only the events, without the player's name
        message = self.update_game_events(message)
        if message.count_ingame_events() == 0:
            message.add_event(["Nothing came of it."]) # The game sent no answer in time
        actions_log.info(f"The game answered the action: {message.get_ingame_events_text().strip()}")
        asker = self.__action_asker
        if asker and message.is_multi_npc_message and not isinstance(self.__conversation_type, multi_npc_game_master):
            # One reply speaks for every NPC here: the one who found it out speaks first
            message.add_event([f"{asker.name} speaks first, with what came of it."])
        self.__messages.add_message(message)
        if isinstance(self.__conversation_type, multi_npc_game_master):
            if not self.__game_master_turns.answer_arrived(message):
                # The reply that picked the action was stopped as the conversation changed while the game worked out its answer, e.g.
                # ReportCrime called a guard over: a new NPC turn goes on with the answer
                actions_log.info("The reply that picked the action was stopped. The NPCs go on with the game's answer in a new turn")
                self.__start_generating_npc_sentences(allow_tools=False)
        else:
            self.__start_generating_npc_sentences(allow_tools=False)
        return True

    def __start_generating_npc_sentences(self, allow_player_first: bool = False, allow_tools: bool = True):
        """Starts a background Thread to generate sentences into the SentenceQueue

        Args:
            allow_player_first (bool): Game Master Mode: the Game Master may give the player the turn right away, e.g. who reacts first to an event
            allow_tools (bool): Advanced Actions: the NPC can pick actions. Not right after the game answered one, so it can't loop
        """
        if allow_tools:
            self.__awaiting_action_answer = False # A new reply: an answer still expected for the last one is too late now
        if isinstance(self.__conversation_type, multi_npc_game_master):
            with self.__generation_start_lock:
                turn_id = self.__game_master_turns.start_turn(allow_player_first, allow_tools)
                self.__sentences.is_more_to_come = True
                Thread(None, self.__game_master_turns.run_turn, None, [turn_id, lambda: self.__conversation_type, lambda: self.__output_manager, self.__messages]).start()
            return
        with self.__generation_start_lock:
            if not self.__generation_thread:
                # Refresh the system prompt each request so `{lorebook}` can react
                # to the latest conversation history and context variables.
                try:
                    new_prompt = self.__conversation_type.generate_prompt(
                        self.__context,
                        messages_for_lorebook=self.__messages
                    )
                    is_multi = self.__context.npcs_in_conversation.contains_multiple_npcs()
                    self.__messages.modify_messages(
                        new_prompt,
                        multi_npc_conversation=is_multi,
                        remove_system_flagged_messages=False
                    )
                except PromptSectionError as e:
                    log.error(str(e))
                    log.error("NPC reply was not generated. The conversation is waiting. Fix the active prompt profile, or end the conversation.")
                    return
                except Exception as e:
                    log.warning(f"Could not refresh system prompt before generation: {e}")
                self.__sentences.is_more_to_come = True
                # Without Game Master Mode, one reply speaks for every NPC in the conversation
                tool_request = tool_request_for(self.__conversation_type, self.__context, self.__output_manager.game,
                                                self.__context.npcs_in_conversation.contains_multiple_npcs()) if allow_tools else None
                self.__generation_thread = Thread(None, self.__output_manager.generate_response, None, [self.__messages, self.__context.npcs_in_conversation, self.__sentences, self.npc_actions()],
                                                  {"tool_request": tool_request}).start()   

    @utils.time_it
    def __stop_generation(self):
        """Stops the current generation of sentences if there is one
        """
        self.__output_manager.stop_generation()
        self.__game_master_turns.stop_generation()
        while self.__generation_thread and self.__generation_thread.is_alive():
            time.sleep(0.1)
        self.__generation_thread = None

    @utils.time_it
    def __prepare_eject_npc_from_conversation(self, npc: Character):
        if not self.__has_already_ended:            
            self.__stop_generation()
            self.__sentences.clear()            
            # say goodbye
            goodbye_sentence = self.__output_manager.generate_sentence(SentenceContent(npc, self.__context.config.goodbye_npc_response, SentenceTypeEnum.SPEECH, False))
            if goodbye_sentence:
                goodbye_sentence.actions.append({'identifier': comm_consts.ACTION_REMOVECHARACTER})
                self.__sentences.put(goodbye_sentence)        

    @utils.time_it
    def __save_summary(self):
        """Saves conversation log and state for each NPC in the conversation"""
        npcs = self.__context.npcs_in_conversation
        pending_shares = npcs.get_pending_shares()
        npcs.clear_pending_shares()
        saved_summaries = self.__save_summary_for_characters(
            self.__messages,
            npcs.get_all_characters()
        )
        if pending_shares:
            # ShareConversation: also with summaries off
            try:
                self.__rememberer.share_conversation(self.__messages, npcs, self.__context.world_id, pending_shares, saved_summaries or {},
                                                     self.__conversation_start_ts, self.__was_directed)
            except Exception as e:
                log.error(f"Could not share the conversation: {e}")

    @utils.time_it
    def save_summary_only(self, is_reload: bool = False) -> None:
        """Triggers a summary/log save WITHOUT ending the conversation.

        Important: The rememberer may mutate the message thread (e.g., by inserting join/leave messages).
        To avoid affecting the live conversation state, we take a snapshot of persistent messages.
        """
        try:
            # Snapshot persistent messages so the rememberer can safely mutate the copy.
            messages_snapshot = message_thread(self.__context.config, None)
            messages_snapshot.add_non_system_messages(self.__messages.get_persistent_messages())
            self.__save_summary_for_characters(
                messages_snapshot,
                self.__context.npcs_in_conversation.get_all_characters(),
                is_reload=is_reload,
                force=True,
                save_thoughts=bool(getattr(self.__context.config, "save_summary_now_also_save_inner_thoughts", True)),
                save_reflections=bool(getattr(self.__context.config, "save_summary_now_also_save_personal_reflection", True)),
            )
        except Exception as e:
            log.error(f"save_summary_only failed: {e}", exc_info=True)

    @utils.time_it
    def save_inner_thoughts_only(self) -> None:
        """Triggers a private-thoughts save WITHOUT ending the conversation or writing a new summary.

        Snapshots persistent messages so the rememberer can safely mutate the copy.
        """
        try:
            messages_snapshot = message_thread(self.__context.config, None)
            messages_snapshot.add_non_system_messages(self.__messages.get_persistent_messages())
            characters_object = Characters()
            for npc in self.__context.npcs_in_conversation.get_all_characters():
                if not npc.is_player_character:
                    characters_object.add_or_update_character(npc)
            save_timestamp = int(time.time())
            self.__rememberer.save_thoughts_only(
                messages_snapshot,
                characters_object,
                self.__context.world_id,
                save_timestamp,
                conversation_start_ts=self.__conversation_start_ts,
            )
        except Exception as e:
            log.error(f"save_inner_thoughts_only failed: {e}", exc_info=True)

    @utils.time_it
    def save_personal_reflection_only(self) -> None:
        """Triggers a personal-reflection save WITHOUT ending the conversation or writing a new summary.

        Snapshots persistent messages so the rememberer can safely mutate the copy.
        """
        try:
            messages_snapshot = message_thread(self.__context.config, None)
            messages_snapshot.add_non_system_messages(self.__messages.get_persistent_messages())
            characters_object = Characters()
            for npc in self.__context.npcs_in_conversation.get_all_characters():
                if not npc.is_player_character:
                    characters_object.add_or_update_character(npc)
            save_timestamp = int(time.time())
            self.__rememberer.save_reflections_only(
                messages_snapshot,
                characters_object,
                self.__context.world_id,
                save_timestamp,
                conversation_start_ts=self.__conversation_start_ts,
            )
        except Exception as e:
            log.error(f"save_personal_reflection_only failed: {e}", exc_info=True)

    @utils.time_it
    def get_conversation_as_json(self) -> str | None:
        """Returns the current persistent messages as a lossless JSON string for the live editor.
        Returns None if there are no persistent messages.
        """
        persistent = self.__messages.get_persistent_messages()
        if not persistent:
            return None
        return serialize_persistent_messages(persistent, self.__context.config)

    @utils.time_it
    def apply_conversation_from_json(self, json_str: str) -> bool:
        """Replaces the persistent messages with the deserialized content from the given JSON string.
        Returns True on success, False on error.
        """
        try:
            # NPCs who already left are no longer active; reuse their Character from the
            # current history so they don't become blank-bio stubs.
            history_characters: dict[str, Character] = {}
            for message in self.__messages.get_persistent_messages():
                if isinstance(message, (join_message, leave_message)) and message.character is not None:
                    history_characters[message.character.ref_id] = message.character
            def resolve(ref_id: str, _name: str, _base_id: str):
                return self.get_character(ref_id) or history_characters.get(ref_id)
            new_messages = deserialize_persistent_messages(
                json_str,
                self.__context.config,
                character_resolver=resolve,
            )
            self.__messages.replace_persistent_messages(new_messages)
            # Replies generated ahead were built from the old history. The player gets the turn after the reply being played
            if isinstance(self.__conversation_type, multi_npc_game_master) and self.__game_master_turns.drop_lookahead():
                self.__stop_generation() # The reply generated ahead may be on the chat manager from before a settings change
            return True
        except Exception as e:
            log.error(f"apply_conversation_from_json failed: {e}", exc_info=True)
            return False

    @utils.time_it
    def undo_last_round(self) -> bool:
        """Removes the last real player turn and everything after it from the live conversation.

        A round starts at the last UserMessage with is_system_generated_message == False
        (system greetings / radiant prompts / goodbye flags are skipped). Stops any in-flight
        generation and clears the sentence queue. Returns True if a round was removed.
        """
        with self.__generation_start_lock:
            self.__cancel_game_master_turn()
            self.__stop_generation()
            self.__sentences.clear()

            persistent = self.__messages.get_persistent_messages()
            cut_index = None
            for i in range(len(persistent) - 1, -1, -1):
                msg = persistent[i]
                if isinstance(msg, UserMessage) and not msg.is_system_generated_message:
                    cut_index = i
                    break
            if cut_index is None:
                return False
            self.__messages.replace_persistent_messages(persistent[:cut_index])
            return True

    @utils.time_it
    def undo_last_npc_reply(self) -> bool:
        """Removes everything after the last real player turn, keeping the user message.
        Game Master Mode: removes only the last NPC turn, as one player turn can be followed by many NPC turns.

        Stops any in-flight generation and clears the sentence queue. Returns True if
        anything was removed.
        """
        with self.__generation_start_lock:
            self.__cancel_game_master_turn()
            self.__stop_generation()
            self.__sentences.clear()

            persistent = self.__messages.get_persistent_messages()
            if isinstance(self.__conversation_type, multi_npc_game_master) and persistent and isinstance(persistent[-1], AssistantMessage):
                last = persistent[-1]
                if last.remove_last_speaker_turn():
                    if not last.has_sentences():
                        persistent.pop()
                    self.__messages.replace_persistent_messages(persistent)
                    return True
            cut_index = None
            for i in range(len(persistent) - 1, -1, -1):
                msg = persistent[i]
                if isinstance(msg, UserMessage) and not msg.is_system_generated_message:
                    cut_index = i
                    break
            if cut_index is None or cut_index >= len(persistent) - 1:
                return False
            self.__messages.replace_persistent_messages(persistent[: cut_index + 1])
            return True

    @utils.time_it
    def undo_last_user_message(self) -> bool:
        """Removes the last message only if it is a real player UserMessage.

        Stops any in-flight generation and clears the sentence queue. Returns True if
        the last message was a player turn and was removed.
        """
        with self.__generation_start_lock:
            self.__cancel_game_master_turn()
            self.__stop_generation()
            self.__sentences.clear()

            persistent = self.__messages.get_persistent_messages()
            if not persistent:
                return False
            last = persistent[-1]
            if not isinstance(last, UserMessage) or last.is_system_generated_message:
                return False
            self.__messages.replace_persistent_messages(persistent[:-1])
            return True

    @utils.time_it
    def __save_conversation_log_for_characters(self, characters_to_save_for: list[Character], messages: message_thread | None = None):
        """Saves the messages (by default all messages of the conversation) to a json file for each NPC in the conversation"""
        messages = messages if messages is not None else self.__messages
        for npc in characters_to_save_for:
            if not npc.is_player_character:
                conversation_log.save_conversation_log(npc, message_thread.transform_to_openai_messages(messages.get_talk_only()), self.__context.world_id)
                
    @utils.time_it
    def __save_summary_for_characters(
        self,
        messages_to_summarize: message_thread,
        characters_to_save_for: list[Character],
        is_reload: bool = False,
        force: bool = False,
        save_thoughts: bool | None = None,
        save_reflections: bool | None = None,
        save_log: bool = True,
    ):
        characters_object = Characters()
        for npc in characters_to_save_for:
            if not npc.is_player_character:
                characters_object.add_or_update_character(npc)
        
        
       
        
        # Only save conversation state (which triggers summary generation) if summaries are enabled,
        # unless explicitly forced (e.g., manual button).
        if force or self.__context.config.conversation_summary_enabled:
            # Save the summary
            save_timestamp = int(time.time())
            saved_summaries = self.__rememberer.save_conversation_state(
                messages_to_summarize,
                characters_object,
                self.__context.world_id,
                is_reload,
                save_timestamp=save_timestamp,
                save_thoughts=save_thoughts,
                save_reflections=save_reflections,
                conversation_start_ts=self.__conversation_start_ts,
                was_directed=self.__was_directed,
            )
            # Save the log
            if save_log:
                self.__save_conversation_log_for_characters(characters_to_save_for)
            return saved_summaries if isinstance(saved_summaries, dict) else {}
        return {}

    @utils.time_it
    def __initiate_reload_conversation(self):
        """Places a "gather thoughts" sentence add the front of the queue that also prompts the game to request a reload of the conversation using an action"""
        latest_npc = self.__context.npcs_in_conversation.last_added_character
        if not latest_npc: 
            self.initiate_end_sequence()
            return
        
        # Play gather thoughts
        collecting_thoughts_text = self.__context.config.collecting_thoughts_npc_response
        collecting_thoughts_sentence = self.__output_manager.generate_sentence(SentenceContent(latest_npc, collecting_thoughts_text, SentenceTypeEnum.SPEECH, True))
        if collecting_thoughts_sentence:
            collecting_thoughts_sentence.actions.append({'identifier': comm_consts.ACTION_RELOADCONVERSATION})
            self.__sentences.put_at_front(collecting_thoughts_sentence)
    
    @utils.time_it
    def reload_conversation(self):
        """Reloads the conversation
        """
        try:
            new_prompt = self.__conversation_type.generate_prompt(self.__context)
        except PromptSectionError as e:
            log.error(str(e))
            if isinstance(self.__conversation_type, radiant):
                self.__conversation_type.mark_prompt_unusable()
            return
        # Like a cut after an actor change: the dropped lines become memories and go into the conversation log
        self.reload_and_cull_thread(new_prompt)

    @utils.time_it
    def __has_conversation_ended(self, last_user_text: str) -> bool:
        """Checks if the last player text has ended the conversation

        Args:
            last_user_text (str): the text to check

        Returns:
            bool: true if the conversation has ended, false otherwise
        """
        # transcriber = self.__stt
        config = self.__context.config
        transcript_cleaned = utils.clean_text(last_user_text)

        # check if user is ending conversation
        return Transcriber.activation_name_exists(transcript_cleaned, self.__end_conversation_keywords)

    @utils.time_it
    def __does_dismiss_npc_from_conversation(self, last_user_text: str) -> Character | None:
        """Checks if the last player text dismisses an NPC from the conversation

        Args:
            last_user_text (str): the text to check

        Returns:
            bool: true if the conversation has ended, false otherwise
        """
        transcript_cleaned = utils.clean_text(last_user_text)

        words = transcript_cleaned.split()
        for i, word in enumerate(words):
            if word in self.__end_conversation_keywords:
                if i < (len(words) - 1):
                    next_word = words[i + 1]
                    for npc_name in self.__context.npcs_in_conversation.get_all_names():
                        if next_word in npc_name.lower().split():
                            return self.__context.npcs_in_conversation.get_character_by_name(npc_name)
        return None
    
    @utils.time_it
    def __should_voice_player_input(self, player_character: Character) -> bool:
        game_value: Any = player_character.get_custom_character_value(comm_consts.KEY_ACTOR_PC_VOICEPLAYERINPUT)
        if game_value == None:
            return self.__context.config.voice_player_input
        return game_value