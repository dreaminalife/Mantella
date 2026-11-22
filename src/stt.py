import numpy as np
from src.config.config_loader import ConfigLoader
import src.utils as utils
from typing import Optional
from datetime import datetime
import queue
import threading
import time
import os
import wave
import onnxruntime as ort
from sounddevice import InputStream
from src import stt_backends
from src.stt_backends.base import SpeechBackend, SpeechError
from src.stt_backends.names import is_hint_copy
from silero_vad import VADIterator, load_silero_vad
from src.ptt_controller import PTTController

import onnxruntime as ort
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.MICROPHONE)
ort.set_default_logger_severity(4)

class Transcriber:
    """Listens to the microphone, notices when the player starts and stops talking, and hands the recording to the chosen speech-to-text option (src/stt_backends)"""
    
    SAMPLING_RATE = 16000
    CHUNK_SIZE = 512  # Required chunk size for Silero VAD
    CHUNK_DURATION = CHUNK_SIZE / SAMPLING_RATE  # Explicit calculation of chunk duration in seconds
    LOOKBACK_CHUNKS = 5  # Number of chunks to keep in buffer when not recording
    
    @utils.time_it
    def __init__(self, config: ConfigLoader, stt_secret_key_file: str, secret_key_file: str):
        self.language = config.stt_language
        self.listen_timeout = config.listen_timeout
        self.prompt = ''
        self.names: list[str] = [] # the same names as the hint, one by one, for options that take words to listen for
        self.show_mic_warning = True
        self.play_cough_sound = config.play_cough_sound
        self.transcription_times = []
        self.__choice = stt_backends.chosen(config)
        self.proactive_mic_mode = self.__proactive_mode_for(config)
        self.min_refresh_secs = config.min_refresh_secs # Minimum time between transcription updates
        self.refresh_freq = self.min_refresh_secs // self.CHUNK_DURATION # Number of chunks between transcription updates
        self.pause_threshold = config.pause_threshold
        self._temporary_pause: float | None = None # The Listen action: a longer pause for the player's next line only
        self.audio_threshold = config.audio_threshold
        log.debug( f"Audio threshold set to {self.audio_threshold}. If the mic is not picking up your voice, try lowering this `Speech-to-Text`->`Audio Threshold` value in the Mantella UI. If the mic is picking up too much background noise, try increasing this value.\n")

        # PTT settings
        self.ptt_enabled = getattr(config, 'ptt_enabled', False)
        self._ptt = PTTController(getattr(config, 'ptt_hotkey', None) if self.ptt_enabled else None)
        self._ptt_active = False

        self.__audio_input_error_count = 0
        self.__mic_input_process_error_count = 0
        self.__processing_audio_error_count = 0
        self.__warning_frequency = 5
        
        self.__save_mic_input = config.save_mic_input
        if self.__save_mic_input:
            self.__mic_input_path: str = config.save_folder+'data\\tmp\\mic'
            os.makedirs(self.__mic_input_path, exist_ok=True)

        # stt_secret_key_file (STT_SECRET_KEY.txt) is no longer read here: each service has its own key file, and the old one was copied there when the settings moved over
        self.__secret_key_file = secret_key_file
        self.__reported_problems: set[str] = set() # each kind of problem is shown once, until the settings change
        language_problem = stt_backends.language_problem(self.__choice, self.language)
        if language_problem:
            log.warning(language_problem)
        log.debug( f"Speech-to-text: {self.__choice.preset.name}, model {self.__choice.model or '(set by the program)'}")
        self.__backend: SpeechBackend = stt_backends.build_backend(self.__choice, config, self.__secret_key_file)
        try:
            self.__backend.load()
        except SpeechError as e: # tried again with the first recording; Mantella keeps running and typing still works
            self.__report(e)

        # Initialize VAD
        self.vad_model = load_silero_vad(onnx=True)
        self.vad_iterator: VADIterator = self._create_vad_iterator()
        
        # Audio processing state
        self._audio_buffer = np.array([], dtype=np.float32)
        self._audio_queue = queue.Queue()
        self._stream: Optional[InputStream] = None
        
        # Threading and synchronization
        self._lock = threading.Lock()
        self._processing_thread: Optional[threading.Thread] = None
        self._running = False
        
        # Speech detection state
        self._speech_detected = False
        self._speech_start_time = 0
        self._speech_end_time = 0
        self._last_update_time = 0
        self._current_transcription = ""
        self._transcription_ready = threading.Event()
        self._is_wait_cancelled = False # See cancel_wait
        self._consecutive_empty_count = 0
        self._max_consecutive_empty = 10

    @property
    def is_listening(self) -> bool:
        """Returns True if actively listening."""
        return self._processing_thread is not None and self._processing_thread.is_alive()

    @property
    def has_player_spoken(self) -> bool:
        """Check if speech has been detected."""
        with self._lock:
            return self._speech_detected
        

    @property
    def choice(self) -> stt_backends.STTChoice:
        """The speech-to-text option in use, and its model"""
        return self.__choice

    def __proactive_mode_for(self, config: ConfigLoader) -> bool:
        """Proactive Mode re-sends the whole recording every Refresh Frequency seconds: free on this PC, but every re-send is billed online.
        So it is off for the Online Service choices. Custom Service keeps it, as Mantella can't tell a paid address from a free program at home"""
        if config.proactive_mic_mode and self.__choice.preset.online:
            log.info(f"Proactive Mode is off while speech-to-text uses {self.__choice.preset.name}: each update would be a separate paid request.")
            return False
        return config.proactive_mic_mode

    def __report(self, error: SpeechError) -> None:
        """Shows a problem in the Mantella window once per kind, so a wrong key doesn't fill the log with every line the player says"""
        if error.kind in self.__reported_problems:
            log.debug(f"Speech-to-text: {error.message}")
            return
        self.__reported_problems.add(error.kind)
        log.error(f"Speech-to-text: {error.message}")

    @utils.time_it
    def _transcribe(self, audio: np.ndarray) -> str:
        """Turns the recording into text with the chosen option. A problem gives an empty text, so the cough sound plays"""
        # Count speech end time from when the last transcribe is called
        self._speech_end_time = time.time()
        try:
            transcription = self.__backend.transcribe(audio, self.prompt, self.names, self.language)
        except SpeechError as e:
            self.__report(e)
            transcription = ''
        except Exception as e:
            self.__report(SpeechError("unexpected", f"{self.__choice.preset.name} could not turn the recording into text: {e}"))
            transcription = ''
        if self.__backend.takes_hint and is_hint_copy(transcription, self.prompt): # Whisper sometimes answers silence with the names it was given
            log.debug(f"Speech-to-text returned only the names it was given ('{transcription}'), so it is thrown away")
            transcription = ''

        self.transcription_times.append((time.time() - self._speech_end_time))
        if (self.proactive_mic_mode) and (len(self.transcription_times) % 5 == 0):
            max_transcription_time = max(self.transcription_times[-5:])
            if max_transcription_time > self.min_refresh_secs:
                log.warning(f'Mic transcription took {round(max_transcription_time,3)} to process. To improve performance, try setting `Speech-to-Text`->`Refresh Frequency` to a value slightly higher than {round(max_transcription_time,3)} in the Mantella UI')

        if self.proactive_mic_mode:
            log.debug( f'Interim transcription: {transcription}')
        
        # Only update the transcription if it contains a value, otherwise keep the existing transcription
        if transcription:
            return transcription
        else:
            self._consecutive_empty_count += 1
            return self._current_transcription


    @utils.time_it
    def start_listening(self, prompt: str = '', names: list[str] | None = None) -> None:
        '''Start background listening thread. *prompt*: the hint sentence of names; *names*: the same names one by one'''
        if self._running:
            return
            
        self._running = True
        self._reset_state()
        # Clear any stale state (ensure fresh session)
        self._transcription_ready.clear()
        self._current_transcription = ''
        self.prompt = prompt
        self.names = list(names or [])
        
        # Start audio stream
        self._stream = InputStream(
            samplerate=self.SAMPLING_RATE,
            channels=1,
            blocksize=self.CHUNK_SIZE,
            dtype=np.float32,
            callback=self._create_input_callback(self._audio_queue),
            latency = 'low'
        )
        self._stream.start()
        
        # Start processing thread
        self._processing_thread = threading.Thread(
            target=self._process_audio,
            daemon = True
        )
        self._processing_thread.start()
        log.info( 'Listening...')


    def _process_audio(self) -> None:
        """Process audio data in a separate thread."""
        lookback_size = self.LOOKBACK_CHUNKS * self.CHUNK_SIZE
        chunk_count = 0
        
        while self._running:
            try:
                # Get audio chunk and status from queue
                chunk, status = self._audio_queue.get(timeout=0.1)
                if status:
                    if self.__processing_audio_error_count % self.__warning_frequency == 0:
                        log.warning( f"STT WARNING: Processing audio error: {status}")
                    self.__processing_audio_error_count += 1
                    continue

                with self._lock:
                    # PTT gating: when enabled, only capture while key is held; ignore VAD end/pause logic
                    if self.ptt_enabled:
                        is_pressed = self._ptt.is_pressed()
                        if is_pressed:
                            # Begin or continue PTT-capture
                            self._audio_buffer = np.concatenate((self._audio_buffer, chunk))
                            if not self._ptt_active:
                                log.info( 'PTT pressed')
                                self._ptt_active = True
                                self._speech_detected = True
                                self._speech_start_time = time.time()
                                self._last_update_time = time.time()
                        else:
                            if self._ptt_active:
                                log.info( 'PTT released - transcribing')
                                # Check both buffer length and minimum duration to avoid corrupted audio
                                audio_duration = len(self._audio_buffer) / self.SAMPLING_RATE if len(self._audio_buffer) > 0 else 0
                                min_duration = 0.3  # Minimum 100ms of audio
                                
                                if len(self._audio_buffer) > 0 and audio_duration >= min_duration:
                                    self._current_transcription = self._transcribe(self._audio_buffer)
                                    if self.__save_mic_input:
                                        self._save_audio(self._audio_buffer)
                                    self._transcription_ready.set()
                                else:
                                    log.debug(f'PTT released with insufficient audio (duration: {audio_duration:.3f}s, min: {min_duration}s)')
                                self._reset_state()
                                self._soft_reset_vad()
                                self._ptt_active = False
                            else:
                                # Idle: do not accumulate buffer when PTT is not pressed
                                self._audio_buffer = np.array([], dtype=np.float32)
                        # Check PTT timeout before continue to avoid unbounded buffering
                        if self._ptt_active and (len(self._audio_buffer) / self.SAMPLING_RATE) > self.listen_timeout:
                            log.warning(f'PTT timeout of {self.listen_timeout} seconds reached. Processing mic input...')
                            # No minimum duration check needed for timeout case - force transcription
                            if len(self._audio_buffer) > 0:
                                self._current_transcription = self._transcribe(self._audio_buffer)
                                if self.__save_mic_input:
                                    self._save_audio(self._audio_buffer)
                                self._transcription_ready.set()
                            self._reset_state()
                            self._soft_reset_vad()
                            self._ptt_active = False
                        # Skip VAD processing entirely in PTT mode
                        continue

                    # Update audio buffer
                    self._audio_buffer = np.concatenate((self._audio_buffer, chunk))
                    if not self._speech_detected:
                        # Keep limited lookback buffer when not recording
                        self._audio_buffer = self._audio_buffer[-lookback_size:]
                    
                    # Process with VAD
                    speech_dict = self.vad_iterator(chunk)
                    
                    # Handle speech detection
                    if speech_dict:
                        if "start" in speech_dict and not self._speech_detected:
                            log.debug( 'Speech detected')
                            self._speech_detected = True
                            self._speech_start_time = time.time()
                            self._last_update_time = time.time()
                        
                        if "end" in speech_dict and self._speech_detected:
                            log.debug( 'Speech ended')
                            # If proactive mode is disabled, transcribe mic input only when speech end has been detected
                            if not self.proactive_mic_mode:
                                self._current_transcription = self._transcribe(self._audio_buffer)
                            if self.__save_mic_input:
                                self._save_audio(self._audio_buffer)

                            self._transcription_ready.set()
                            self._reset_state()
                    
                    # Update transcription periodically during speech
                    elif self._speech_detected:
                        chunk_count += 1
                        
                        # Check for maximum speech duration
                        if (len(self._audio_buffer) / self.SAMPLING_RATE) > self.listen_timeout:
                            log.warning(f'Listen timeout of {self.listen_timeout} seconds reached. Processing mic input...')
                            self._current_transcription = self._transcribe(self._audio_buffer)
                            self._transcription_ready.set()

                            self._reset_state()
                            self._soft_reset_vad()
                        # Regular update during speech
                        elif (self.proactive_mic_mode) and (chunk_count >= self.refresh_freq):
                            log.debug(f'Transcribing {self.min_refresh_secs} of mic input...')
                            self._current_transcription = self._transcribe(self._audio_buffer)

                            if self._consecutive_empty_count >= self._max_consecutive_empty:
                                log.warning(f'Could not transcribe input')
                                self._transcription_ready.set()
                                self._reset_state()
                                self._soft_reset_vad()

                            chunk_count = 0  # Reset counter
            
            except queue.Empty:
                log.debug('Queue is empty')
                continue
            except Exception as e:
                if self.__mic_input_process_error_count % self.__warning_frequency == 0:
                    log.warning( f'STT WARNING: Error processing mic input: {str(e)}')
                self.__mic_input_process_error_count += 1
                self._reset_state()
                time.sleep(0.1)


    def _create_vad_iterator(self) -> VADIterator:
        """Create a new VAD iterator with configured parameters."""
        return VADIterator(
            model=self.vad_model,
            sampling_rate=self.SAMPLING_RATE,
            threshold=self.audio_threshold,
            min_silence_duration_ms=int((self._temporary_pause or self.pause_threshold) * 1000),
            speech_pad_ms = 30 # default
        )


    def set_temporary_pause(self, pause_seconds: float) -> None:
        """The Listen action: the player's next line ends only after this much silence, giving them more time to think. Then the pause is as set again"""
        with self._lock:
            self._temporary_pause = pause_seconds
            if self._running and not self._speech_detected:
                self.vad_iterator = self._create_vad_iterator()


    def _create_input_callback(self, q: queue.Queue):
        """Create callback for audio input stream."""
        def input_callback(indata, frames, time, status):
            if status:
                if self.__audio_input_error_count % self.__warning_frequency == 0:
                    log.warning( f"STT WARNING: Audio input error: {status}")
                self.__audio_input_error_count += 1
            # Store both data and status in queue
            q.put((indata.copy().flatten(), status))
        return input_callback


    def _soft_reset_vad(self) -> None:
        """Soft reset VAD iterator without affecting model state."""
        self.vad_iterator.triggered = False
        self.vad_iterator.temp_end = 0
        self.vad_iterator.current_sample = 0


    def _reset_state(self) -> None:
        """Reset internal state."""
        self._speech_detected = False
        # Ensure PTT state is cleared on any reset
        try:
            self._ptt_active = False
        except Exception:
            pass
        self._audio_buffer = np.array([], dtype=np.float32)
        self.vad_iterator = self._create_vad_iterator()
        self._consecutive_empty_count = 0


    @utils.time_it
    def _save_audio(self, audio: np.ndarray) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        audio_path = os.path.join(self.__mic_input_path, f'mic_input_{timestamp}.wav')
        with wave.open(audio_path, 'wb') as wf:
            wf.setnchannels(1)  # Mono audio
            wf.setsampwidth(2)  # 16-bit audio
            wf.setframerate(self.SAMPLING_RATE)
            # Convert float32 to int16
            audio_int16 = (audio * 32767).astype(np.int16)
            wf.writeframes(audio_int16.tobytes())


    @utils.time_it
    def hot_swap_settings(self, new_config: ConfigLoader) -> bool:
        """Apply updated STT settings without rebuilding the model when possible.

        Returns True if applied in place; False if a full reinit is required.
        """
        try:
            new_choice = stt_backends.chosen(new_config)
            # Another option, model, address, device or format needs a new backend: the Transcriber is made again
            heavy_change = new_choice.identity != self.__choice.identity

            if heavy_change:
                return False

            # In-place updates with synchronization
            with self._lock:
                self.language = new_config.stt_language

                self.listen_timeout = new_config.listen_timeout
                self.proactive_mic_mode = self.__proactive_mode_for(new_config)
                self.min_refresh_secs = new_config.min_refresh_secs
                self.refresh_freq = self.min_refresh_secs // self.CHUNK_DURATION
                self.play_cough_sound = new_config.play_cough_sound
                # PTT updates
                ptt_mode_changed = self.ptt_enabled != getattr(new_config, 'ptt_enabled', False)
                self.ptt_enabled = getattr(new_config, 'ptt_enabled', False)
                if self.ptt_enabled:
                    hotkey = getattr(new_config, 'ptt_hotkey', None)
                    if self._ptt:
                        self._ptt.update_key(hotkey)
                    else:
                        self._ptt = PTTController(hotkey)
                else:
                    if self._ptt:
                        self._ptt.update_key(None)
                
                # Reset state when toggling PTT on/off to ensure clean transition
                if ptt_mode_changed:
                    self._ptt_active = False
                    self._reset_state()
                    self._soft_reset_vad()
                    log.info(f"PTT mode toggled to: {'enabled' if self.ptt_enabled else 'disabled'} - audio state reset")

                # VAD thresholds
                vad_threshold_changed = (self.audio_threshold != new_config.audio_threshold) or (self.pause_threshold != new_config.pause_threshold)
                self.audio_threshold = new_config.audio_threshold
                self.pause_threshold = new_config.pause_threshold

                # Save mic input path
                self.__save_mic_input = new_config.save_mic_input
                if self.__save_mic_input:
                    self.__mic_input_path = new_config.save_folder+'data\\tmp\\mic'
                    os.makedirs(self.__mic_input_path, exist_ok=True)

                if vad_threshold_changed:
                    self.vad_iterator = self._create_vad_iterator()
                else:
                    self._soft_reset_vad()

            # A key saved in the Key box since: used from the next recording
            self.refresh_key(new_config)
            self.__reported_problems.clear()
            language_problem = stt_backends.language_problem(self.__choice, self.language)
            if language_problem:
                log.warning(language_problem)

            log.info("Applied STT hot-swap settings in place.")
            return True
        except Exception as e:
            log.error(f"STT hot-swap failed: {e}")
            return False


    def transcribe_once(self, audio: np.ndarray, hint: str, names: list[str]) -> str:
        """The text of one recording with the option in use, e.g. for the Test button. Raises SpeechError"""
        self.__backend.forget_failed_load() # the Test button tries a model that failed to load again; if it loads now, the game uses it too
        return self.__backend.transcribe(audio, hint if self.__backend.takes_hint else '', names, self.language)

    def refresh_key(self, config: ConfigLoader) -> None:
        """Reads the chosen online service's key again, e.g. after it was saved in the Key box"""
        if self.__choice.preset.key_file:
            key, _ = stt_backends.key_for(self.__choice.preset, getattr(config, 'llm_api', ''), self.__secret_key_file)
            self.__backend.set_key(key)

    def cancel_wait(self) -> None:
        """Makes get_latest_transcription return an empty text instead of waiting for speech, e.g. when the player picks an event
        on the Conversation Wheel during their turn. Also a call that comes later, until reset_cancel_wait"""
        with self._lock:
            self._is_wait_cancelled = True
        self._transcription_ready.set()

    def reset_cancel_wait(self) -> None:
        """Forgets a cancel_wait that came while nobody was waiting"""
        with self._lock:
            self._is_wait_cancelled = False

    @utils.time_it
    def get_latest_transcription(self) -> str:
        """Get the latest transcription, blocking until speech ends. Empty if the wait was cancelled (see cancel_wait)"""
        while True:
            self._transcription_ready.wait()
            with self._lock:
                if self._is_wait_cancelled:
                    self._is_wait_cancelled = False
                    self._transcription_ready.clear()
                    return ""
                transcription = self._current_transcription
                self._current_transcription = ''
                if transcription:
                    self._transcription_ready.clear()
                    self._speech_detected = False
                    if self._temporary_pause is not None: # Listen lasts for one line
                        self._temporary_pause = None
                        self.vad_iterator = self._create_vad_iterator()
                    log.info( f"Player said '{transcription.strip()}'")
                    return transcription
                
            if self.play_cough_sound:
                utils.play_no_mic_input_detected_sound()
            log.warning('Could not detect speech from mic input')

            self._transcription_ready.clear()
            self._speech_detected = False
            self._current_transcription = ''

            time.sleep(0.1)


    def stop_listening(self) -> None:
        """Stop listening for speech."""
        if not self._running:
            return
            
        self._running = False
        self._speech_detected = False
        
        # Stop and clean up audio stream
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        
        # Wait for processing thread to finish
        if self._processing_thread:
            self._processing_thread.join()  # timeout=1.0 Add timeout to prevent hanging
            self._processing_thread = None
        
        # Clear queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
                
        self._reset_state()
        log.info( 'Stopped listening for mic input')


    @staticmethod
    @utils.time_it
    def activation_name_exists(transcript: str, activation_names: str | list[str]) -> bool:
        """Identifies keyword in the input transcript"""
        if not transcript:
            return False
        
        # Convert to a list even if there is only one activation name
        if isinstance(activation_names, str):
            activation_names = [activation_names]

        # Check for a match among individual words in the transcript
        transcript_words = transcript.split()
        if set(transcript_words).intersection(activation_names):
            return True
        
        # Alternatively, if the entire transcript is a keyword, return True
        for activation_name in activation_names:
            if transcript == activation_name:
                return True
        
        return False


    @staticmethod
    @utils.time_it
    def _remove_activation_word(transcript, activation_name):
        transcript = transcript.replace(activation_name, '')
        return transcript