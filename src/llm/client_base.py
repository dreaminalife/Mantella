from threading import Lock
from typing import AsyncGenerator, Any
import asyncio
from openai import APIConnectionError, APIError, APITimeoutError, BadRequestError, OpenAI, AsyncOpenAI, RateLimitError
import httpx
import json
import logging
import time
import tiktoken
import os
import requests
from pathlib import Path
from src.llm.ai_client import AIClient
from src.llm.message_thread import message_thread
from src.llm.messages import Message, ImageMessage, UserMessage
from src.llm.llm_model_list import LLMModelList
import src.utils as utils
from src.llm.sonnet_cache_connector import SonnetCacheConnector
from src.prompt_conditions import apply_model_conditions_to_request
from src.logs.log_topic import LINE_WHITE, LogTopic
from src.logs.topic_logger import get_topic_logger, in_colour

log = get_topic_logger(LogTopic.AI_REPLIES)
settings_log = get_topic_logger(LogTopic.SETTINGS_PAGE)

BRAILLE_BLANK = "⠀"

class FirstTokenTimeout(Exception):
    """The first spoken token did not arrive within the response timeout."""


class ToolCalls(list):
    """What streaming_call yields when the AI model picks actions (Advanced Actions): its tool calls,
    {"id", "type": "function", "function": {"name", "arguments"}} with the arguments as JSON text. The text comes as plain strings"""


class ProviderError(Exception):
    """The LLM service returned an error before any text (e.g. a content block). Another model may still answer."""


def format_openai_messages(openai_messages: list[dict[str, Any]]) -> str:
    """The messages sent to the LLM as readable text for the log, one block per message. Images are shown as [image]"""
    lines: list[str] = []
    for message in openai_messages:
        content = message.get("content")
        if isinstance(content, list):
            parts = [part.get("text", "") if part.get("type") == "text" else f"[{part.get('type', 'unknown')}]" for part in content if isinstance(part, dict)]
            content = "\n".join(parts)
        lines += [f"===== {message.get('role', 'unknown')} =====", str(content or ""), ""]
    return "\n".join(lines).rstrip()


def describe_request(messages: Message | message_thread, openai_messages: list[dict[str, Any]]) -> str:
    """For the log: which prompt the request sends (see src/prompt_label.py), and whether a picture goes with it"""
    label = messages.prompt_label if isinstance(messages, message_thread) else (getattr(messages.text, "label", "") or "")
    has_picture = any(isinstance(message.get("content"), list) and any(isinstance(part, dict) and part.get("type") == "image_url" for part in message["content"])
                      for message in openai_messages)
    heading = f"LLM request for {label}" if label else "LLM request"
    return heading + (", with a picture" if has_picture else "")


def _tool_name(tool: Any) -> str:
    function = tool.get("function") if isinstance(tool, dict) else None
    return str(function.get("name", "?")) if isinstance(function, dict) else "?"


def describe_params(request_params: dict[str, Any]) -> str:
    """For the log: the request settings, with the actions offered to the model by name only (their full descriptions are long, see format_tools)"""
    text = f"params={ {k: v for k, v in request_params.items() if k != 'tools'} }"
    tools = request_params.get("tools")
    if tools:
        text += f", actions=[{', '.join(_tool_name(tool) for tool in tools)}]"
    return text


def format_tools(request_params: dict[str, Any]) -> str:
    """For the detailed log: the full descriptions of the actions offered to the model, after the messages. Empty if none"""
    tools = request_params.get("tools")
    if not tools:
        return ""
    return "\n\n===== actions =====\n" + json.dumps(tools, indent=2, ensure_ascii=False)


class ClientBase(AIClient):
    '''Base class for connecting to OpenAI-compatible endpoints

    Handles API key management, client generation (sync/async), request execution,
    token counting, endpoint resolution, and model list retrieval
    '''
    # Set from the "Replace Braille Blanks" setting. Shared by every LLM client.
    replace_braille_blank: bool = False

    @staticmethod
    def clean_response_text(text: str) -> str:
        """Replaces Braille Blank characters (U+2800) with standard spaces when the setting is on."""
        if ClientBase.replace_braille_blank and BRAILLE_BLANK in text:
            return text.replace(BRAILLE_BLANK, " ")
        return text

    api_token_limits = {}
    tiktoken_cache_dir = "data"
    os.environ["TIKTOKEN_CACHE_DIR"] = tiktoken_cache_dir
    CONNECT_TIMEOUT_SECONDS = 5.0
    WRITE_POOL_TIMEOUT_SECONDS = 600.0
    DEFAULT_RESPONSE_TIMEOUT_SECONDS = 120

    def __init__(self, api_url: str, llm: str, llm_params: dict[str, Any] | None, custom_token_count: int, secret_key_files: list[str], response_timeout: float = DEFAULT_RESPONSE_TIMEOUT_SECONDS) -> None:
        '''
        Args:
            api_url (str): The API endpoint URL or a known service name (e.g., 'OpenAI', 'OpenRouter')
            llm (str): The name of the language model to use
            llm_params (dict[str, Any] | None): Additional parameters for the LLM requests (eg temperature, max_tokens)
            custom_token_count (int): A fallback token limit if the model's limit isn't known
            secret_key_files (list[str]): A list of filenames to search for the API key, in order of priority
            response_timeout (float): Seconds to wait for the next piece of an LLM response. The connection limit stays at 5 seconds.
        '''
        super().__init__()
        self._generation_lock: Lock = Lock()
        self._service_name: str = api_url
        self._model_name: str = llm
        self._base_url = self.__get_endpoint(api_url)
        self._startup_async_client: AsyncOpenAI | None = None
        self._request_params: dict[str, Any] | None = llm_params
        self._response_timeout = float(response_timeout)
        self._response_deadline: float | None = None
        self._image_client = None
        self._sonnet_cache_connector: SonnetCacheConnector | None = None

        if 'https' in self._base_url: # Cloud LLM
            self._is_local: bool = False
            api_key = ClientBase._get_api_key(secret_key_files)
            if api_key:
                self._api_key = api_key
            else:
                self._api_key: str = 'abc123'
        else: # Local LLM
            self._is_local: bool = True
            self._api_key: str = 'abc123'

        referer = "https://art-from-the-machine.github.io/Mantella/"
        xtitle = "Mantella"
        self._header: dict[str, str] = {"HTTP-Referer": referer, "X-Title": xtitle}
        self._token_limit: int = self.__get_token_limit(self._model_name, custom_token_count, self._is_local)
        self._encoding = self.__get_model_encoding(api_url, self._model_name)


    @property
    def token_limit(self) -> int:
        """The token limit of the model
        """
        return self._token_limit

    @property
    def model_name(self) -> str:
        """The name of the model
        """
        return self._model_name

    @property
    def service_name(self) -> str:
        """The configured service name or endpoint."""
        return self._service_name
    
    @property
    def is_local(self) -> bool:
        """Is the model run locally?
        """
        return self._is_local
    
    @property
    def api_key(self) -> str:
        """The secret key
        """
        return self._api_key

    @property
    def max_tokens_param(self) -> int:
        """Returns the max_tokens value from request params, or defaults to 250"""
        return self._request_params.get("max_tokens", 250) if self._request_params else 250

    def _for_this_model(self, openai_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The messages with the prompts' model blocks ({if model has claude}) applied for this model, the one the request goes to"""
        return apply_model_conditions_to_request(openai_messages, self.model_name)

    def _http_timeout(self, read_seconds: float | None = None) -> httpx.Timeout:
        """Wait for response data. Connecting still times out after 5 seconds."""
        read = float(self._response_timeout if read_seconds is None else read_seconds)
        return httpx.Timeout(
            self.WRITE_POOL_TIMEOUT_SECONDS,
            connect=self.CONNECT_TIMEOUT_SECONDS,
            read=max(read, 0.001),
        )

    def _raise_if_first_token_late(self, deadline: float) -> None:
        if time.time() >= deadline:
            raise FirstTokenTimeout(f"LLM did not respond within {self._response_timeout} seconds")

    def _completion_request_params(self, request_params: dict[str, Any] | None) -> dict[str, Any]:
        """Copy request params and drop a client-side timeout so it cannot replace the connection limit."""
        params = dict(request_params) if request_params else {}
        params.pop("timeout", None)
        return params


    @utils.time_it
    def generate_async_client(self) -> AsyncOpenAI:
        """Generates a new AsyncOpenAI client already setup to be used right away.
        Close the client after usage using 'await client.close()'

        The client needs to be closed after every call (and a new one created for the next call) to avoid connection issues

        Use :func:`streaming_call` for a normal streaming call to the LLM

        Returns:
            AsyncOpenAI: The new async client object
        """
        headers = self._header
        if self._sonnet_cache_connector and self._sonnet_cache_connector.is_applicable(self._base_url, self._model_name):
            headers = self._sonnet_cache_connector.augment_headers(headers)
        return AsyncOpenAI(api_key=self._api_key, base_url=self._base_url, default_headers=headers, timeout=self._http_timeout())


    @utils.time_it
    def generate_sync_client(self) -> OpenAI:
        """Generates a new OpenAI client already setup to be used right away.
        Close the client after usage using 'client.close()'

        Use :func:`request_call` for a normal call to the LLM

        Returns:
            OpenAI: The new sync client object
        """
        headers = self._header
        if self._sonnet_cache_connector and self._sonnet_cache_connector.is_applicable(self._base_url, self._model_name):
            headers = self._sonnet_cache_connector.augment_headers(headers)
        return OpenAI(api_key=self._api_key, base_url=self._base_url, default_headers=headers, timeout=self._http_timeout())


    @utils.time_it
    def request_call(self, messages: Message | message_thread, topic_log: logging.Logger | None = None) -> str | None:
        """Args:
            topic_log: the log topic of the feature asking (e.g. Game Master), which shows the whole request and reply at Full.
                LLM requests and replies when not given"""
        topic_log = topic_log or log
        with self._generation_lock:
            read_limit = None
            if self._response_deadline is not None:
                self._raise_if_first_token_late(self._response_deadline)
                read_limit = self._response_deadline - time.time()
            sync_client = self.generate_sync_client()
            if read_limit is not None:
                sync_client = sync_client.with_options(max_retries=0, timeout=self._http_timeout(read_limit))
            chat_completion = None

            if isinstance(messages, Message) or isinstance(messages, ImageMessage):
                openai_messages = [messages.get_openai_message()]
            else:
                openai_messages = messages.get_openai_messages()

            request_params = self._completion_request_params(self._request_params)
            
            # Apply Sonnet cache breakpoint AFTER all message transformations
            if self._sonnet_cache_connector and self._sonnet_cache_connector.is_applicable(self._base_url, self._model_name):
                try:
                    openai_messages = self._sonnet_cache_connector.transform_messages(openai_messages, model_name=self._model_name)
                except Exception as e:
                    topic_log.warning(f"Sonnet caching transform (sync) failed: {e}")
            if isinstance(messages, message_thread):
                openai_messages = messages.append_decensor_commitment(openai_messages)
            openai_messages = self._for_this_model(openai_messages)

            self.__log_request(topic_log, messages, openai_messages, request_params)

            try:
                chat_completion = sync_client.chat.completions.create(
                    model=self.model_name,
                    messages=openai_messages,
                    timeout=self._http_timeout(read_limit),
                    **request_params,
                )
            except APITimeoutError:
                if self._response_deadline is not None:
                    raise FirstTokenTimeout(f"LLM did not respond within {self._response_timeout} seconds")
                raise
            except RateLimitError:
                topic_log.warning('Could not connect to LLM API, retrying in 5 seconds...')
                time.sleep(5)
            finally:
                sync_client.close()

            if (
                not chat_completion or 
                not chat_completion.choices or 
                chat_completion.choices.__len__() < 1 or 
                not chat_completion.choices[0].message.content
            ):
                topic_log.info(f"{self.model_name} sent back no reply")
                return None
            
            reply = self.clean_response_text(chat_completion.choices[0].message.content)
            topic_log.debug(f"Reply from {self.model_name}:\n{reply}")
            return reply
        

    @utils.time_it
    async def streaming_call(self, messages: Message | message_thread, is_multi_npc: bool, response_started_at: float | None = None,
                             tools: list[dict] | None = None) -> AsyncGenerator[str | ToolCalls | None, None]:
        """Streams the reply's text. With tools (Advanced Actions), the model can also pick actions: their calls come last, as one ToolCalls"""
        with self._generation_lock:
            started_at = time.time() if response_started_at is None else response_started_at
            deadline = started_at + float(self._response_timeout)
            self._response_deadline = deadline
            log.info(f"Asking {self.model_name} for a reply")

            if self._startup_async_client:
                async_client = self._startup_async_client
                self._startup_async_client = None # do not reuse the same client
            else:
                async_client = self.generate_async_client()

            request_params = self._completion_request_params(self._request_params)
            if tools:
                request_params["tools"] = tools
            tool_calls: dict[int, dict] = {} # By index: the calls arrive in pieces
            if is_multi_npc: # override max_tokens to be at least 250 in radiant / multi-NPC conversations
                original_max_tokens = request_params.get("max_tokens", self.max_tokens_param)
                request_params["max_tokens"] = max(self.max_tokens_param, 250)
                if original_max_tokens != request_params["max_tokens"]:
                    log.debug(f"Multi-NPC conversation: max_tokens overridden from {original_max_tokens} to {request_params['max_tokens']}")
            received_content = False
            try:
                # Prepare the messages including the image if provided
                vision_hints = ''
                if isinstance(messages, Message):
                    openai_messages = [messages.get_openai_message()]
                    if isinstance(messages, UserMessage):
                        vision_hints = messages.get_ingame_events_text()
                else:
                    openai_messages = messages.get_openai_messages()
                    last_message = messages.get_last_message()
                    if isinstance(last_message, UserMessage):
                        vision_hints = last_message.get_ingame_events_text()

                if self._image_client:
                    self._raise_if_first_token_late(deadline)
                    self._image_client._response_deadline = deadline
                    try:
                        capture = messages.vision_capture if isinstance(messages, message_thread) else None
                        openai_messages = self._image_client.add_image_to_messages(openai_messages, vision_hints, capture)
                    finally:
                        self._image_client._response_deadline = None
                    self._raise_if_first_token_late(deadline)
                
                # Apply Sonnet cache breakpoint AFTER all message transformations (including vision)
                if self._sonnet_cache_connector and self._sonnet_cache_connector.is_applicable(self._base_url, self._model_name):
                    try:
                        openai_messages = self._sonnet_cache_connector.transform_messages(openai_messages, model_name=self._model_name)
                    except Exception as e:
                        log.warning(f"Sonnet caching transform (stream) failed: {e}")
                if isinstance(messages, message_thread):
                    openai_messages = messages.append_decensor_commitment(openai_messages)
                openai_messages = self._for_this_model(openai_messages)

                self.__log_request(log, messages, openai_messages, request_params)

                stream_client = async_client.with_options(max_retries=0)
                while True:
                    remaining = deadline - time.time()
                    self._raise_if_first_token_late(deadline)
                    try:
                        stream = await asyncio.wait_for(
                            stream_client.chat.completions.create(
                                model=self.model_name,
                                messages=openai_messages,
                                stream=True,
                                timeout=self._http_timeout(remaining),
                                **request_params,
                            ),
                            timeout=remaining,
                        )
                        break
                    except asyncio.TimeoutError:
                        raise FirstTokenTimeout(f"LLM did not respond within {self._response_timeout} seconds")
                    except APITimeoutError as e:
                        # The connection (e.g. the TLS handshake) took over 5 seconds, so the request was never sent and can be tried again
                        if not isinstance(e.__cause__, httpx.ConnectTimeout):
                            raise
                        log.warning(f"Could not connect to the LLM within {self.CONNECT_TIMEOUT_SECONDS} seconds, retrying...")

                iterator = stream.__aiter__()
                while True:
                    try:
                        if received_content:
                            chunk = await iterator.__anext__()
                        else:
                            remaining = deadline - time.time()
                            self._raise_if_first_token_late(deadline)
                            try:
                                chunk = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
                            except asyncio.TimeoutError:
                                raise FirstTokenTimeout(f"LLM did not respond within {self._response_timeout} seconds")
                    except StopAsyncIteration:
                        break
                    try:
                        if chunk and chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                            received_content = True
                            yield self.clean_response_text(chunk.choices[0].delta.content)
                        if chunk and chunk.choices and chunk.choices[0].delta and getattr(chunk.choices[0].delta, 'tool_calls', None):
                            received_content = True
                            for piece in chunk.choices[0].delta.tool_calls:
                                call = tool_calls.setdefault(piece.index or 0, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                                if piece.id:
                                    call["id"] = piece.id
                                if piece.function and piece.function.name:
                                    call["function"]["name"] += piece.function.name
                                if piece.function and piece.function.arguments:
                                    call["function"]["arguments"] += piece.function.arguments
                    except Exception as e:
                        log.error(f"LLM API Connection Error: {e}")
                        break
                if tool_calls:
                    picked = ToolCalls(tool_calls[i] for i in sorted(tool_calls))
                    log.debug(f"Actions picked by {self.model_name}: {list(picked)}")
                    yield picked
            except FirstTokenTimeout:
                log.error(f"LLM did not respond within {self._response_timeout} seconds")
                raise
            except APITimeoutError:
                if not received_content:
                    log.error(f"LLM did not respond within {self._response_timeout} seconds")
                    raise FirstTokenTimeout(f"LLM did not respond within {self._response_timeout} seconds")
                log.error(f"LLM API Error: timed out after the first token")
            except Exception as e:
                utils.play_error_sound()
                if isinstance(e, APIConnectionError):
                    if e.code in [401, 'invalid_api_key']: # incorrect API key
                        if self._base_url == 'https://api.openai.com/v1':
                            service_connection_attempt = 'OpenRouter' # check if player means to connect to OpenRouter
                        else:
                            service_connection_attempt = 'OpenAI' # check if player means to connect to OpenAI
                        log.error(f"Invalid API key. If you are trying to connect to {service_connection_attempt}, please choose an {service_connection_attempt} model via the 'model' setting in MantellaSoftware/config.ini. If you are instead trying to connect to a local model, please ensure the service is running.")
                    else:
                        log.error(f"LLM API Error: {e}")
                elif isinstance(e, BadRequestError):
                    if (e.type == 'invalid_request_error') and (self._image_client): # invalid request
                        log.error(f"Invalid request. Try disabling Vision in Mantella's settings and try again.")
                    else:
                        log.error(f"LLM API Error: {e}")
                else:
                    # Errors sent inside the stream (e.g. a provider's content block) only carry the reason in the body
                    body = getattr(e, 'body', None)
                    log.error(f"LLM API Error: {e}" + (f": {body}" if body else ""))
                if isinstance(e, APIError) and not received_content:
                    raise ProviderError(str(e)) from e
            finally:
                self._response_deadline = None
                await async_client.close()


    def __log_request(self, topic_log: logging.Logger, messages: Message | message_thread, openai_messages: list[dict[str, Any]],
                      request_params: dict[str, Any]) -> None:
        """At Short: what is asked of which model, with its settings. At Full: also the whole request"""
        topic_log.info(f"{describe_request(messages, openai_messages)}: model='{self.model_name}', base_url='{self._base_url}', {describe_params(request_params)}",
                       extra=in_colour(LINE_WHITE))
        if not topic_log.isEnabledFor(logging.DEBUG):
            return
        topic_log.debug(f"Request to {self.model_name}:\n{format_openai_messages(openai_messages)}{format_tools(request_params)}")

    @utils.time_it
    def request_tool_calls(self, messages: message_thread, tools: list[dict], topic_log: logging.Logger | None = None) -> ToolCalls:
        """Advanced Actions with a separate model: asks it which actions to take, without a reply text. Empty if none, or if it failed
        Args:
            topic_log: the log topic that shows the whole request and the actions picked at Full. LLM requests and replies when not given"""
        topic_log = topic_log or log
        with self._generation_lock:
            sync_client = self.generate_sync_client()
            openai_messages = self._for_this_model(messages.get_openai_messages())
            request_params = self._completion_request_params(self._request_params)
            request_params["tools"] = tools
            self.__log_request(topic_log, messages, openai_messages, request_params)
            try:
                chat_completion = sync_client.chat.completions.create(model=self.model_name, messages=openai_messages, timeout=self._http_timeout(),
                                                                      **request_params)
            except Exception as e:
                topic_log.error(f"The Advanced Actions model could not pick actions: {e}")
                return ToolCalls()
            finally:
                sync_client.close()
            if not chat_completion or not chat_completion.choices:
                return ToolCalls()
            picked = ToolCalls({"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments or ""}}
                               for call in (getattr(chat_completion.choices[0].message, 'tool_calls', None) or []))
            topic_log.debug(f"Actions picked by {self.model_name}: {list(picked)}")
            return picked


    @utils.time_it
    def hot_swap_settings(self, api_url: str, llm: str, llm_params: dict[str, Any] | None, custom_token_count: int, secret_key_files: list[str], response_timeout: float | None = None) -> bool:
        """Attempts to hot-swap settings without recreating the client instance.
        
        Args:
            api_url: The API endpoint URL or service name
            llm: The name of the language model to use
            llm_params: Additional parameters for the LLM requests
            custom_token_count: A fallback token limit if the model's limit isn't known
            secret_key_files: A list of filenames to search for the API key
            response_timeout: Seconds to wait for the next piece of an LLM response. Omit to keep the current value.
            
        Returns:
            bool: True if hot-swap was successful, False otherwise
        """
        with self._generation_lock:
            try:
                # Update model name
                self._model_name = llm
                self._service_name = api_url
                
                # Update base URL
                self._base_url = self.__get_endpoint(api_url)
                
                # Update request parameters
                self._request_params = llm_params
                if response_timeout is not None:
                    self._response_timeout = float(response_timeout)
                
                # Update API key handling
                if 'https' in self._base_url:  # Cloud LLM
                    self._is_local = False
                    new_api_key = ClientBase._get_api_key(secret_key_files)
                    if new_api_key:
                        self._api_key = new_api_key
                    else:
                        self._api_key = 'abc123'
                else:  # Local LLM
                    self._is_local = True
                    self._api_key = 'abc123'
                
                # Update token limit for new model
                self._token_limit = self.__get_token_limit(self._model_name, custom_token_count, self._is_local)
                
                # Update encoding for new model
                self._encoding = self.__get_model_encoding(api_url, self._model_name)
                
                # Reset startup async client so it gets recreated with new settings
                # Note: We can't properly close the existing client here since this is a sync method
                # and AsyncOpenAI.close() is async. The client will be garbage collected.
                # TODO: Consider making hot_swap_settings async to properly close existing clients
                self._startup_async_client = None
                
                settings_log.info(f"ClientBase hot-swap completed successfully for model '{llm}'")
                return True
                
            except Exception as e:
                settings_log.error(f"ClientBase hot-swap failed: {e}")
                return False


    @utils.time_it
    def __get_endpoint(self, api_url_or_name: str) -> str:
        '''
        Resolves a service name (eg 'openai', 'koboldcpp') if known, or else assumes the input is a direct URL

        Args:
            api_url_or_name (str): The service name or the direct API base URL

        Returns:
            endpoint (str): The resolved API endpoint URL
        '''
        endpoints = {
            'openai': 'https://api.openai.com/v1', # don't set an endpoint, just use the OpenAI default
            'openrouter': 'https://openrouter.ai/api/v1',
            'nanogpt': 'https://nano-gpt.com/api/v1',
            'kobold': 'http://127.0.0.1:5001/v1',
            'textgenwebui': 'http://127.0.0.1:5000/v1',
        }

        cleaned_api_url_or_name = api_url_or_name.strip().lower().replace(' ', '')
        if cleaned_api_url_or_name == 'openai':
            endpoint = endpoints['openai']
        elif cleaned_api_url_or_name == 'openrouter':
            endpoint = endpoints['openrouter']
        elif cleaned_api_url_or_name == 'nanogpt':
            endpoint = endpoints['nanogpt']
        elif cleaned_api_url_or_name in ['kobold','koboldcpp']:
            endpoint = endpoints['kobold']
        elif cleaned_api_url_or_name in ['textgenwebui','text-gen-web-ui','textgenerationwebui','text-generation-web-ui']:
            endpoint = endpoints['textgenwebui']
        else: # if endpoint isn't named, assume it is a direct URL
            endpoint = api_url_or_name

        return endpoint
    

    def __get_llm_priority(self, llm: str, priority: str, api_url: str) -> str:
        '''https://openrouter.ai/docs/features/provider-routing'''
        # Priority is only compatible with OpenRouter
        if api_url.strip().lower().replace(' ', '') != 'openrouter':
            return ''
        
        # Free models cannot have a priority
        if llm.endswith(':free'):
            return ''
        
        priorities = {
            'Balanced': '',
            'Price': ':price', 
            'Speed': ':nitro'
        }
        return priorities.get(priority, '')
    

    @utils.time_it
    @staticmethod
    def _get_api_key(key_files: str | list[str], show_error: bool = True) -> str | None:
        '''
        Attempts to read an API key from a list of files in order of priority.

        Args:
            key_files (list[str]): A list of file names to check for the API key, in order of priority.
        '''
        if isinstance(key_files, str):
            key_files = [key_files]
        
        mod_parent_folder = Path(utils.resolve_path()).parent.parent.parent
        
        api_key = None
        for key_file in key_files:
            # try to check the mod folder first
            try:
                with open(mod_parent_folder / key_file, 'r') as f:
                    api_key = f.readline().strip()
                    if api_key:
                        break
            except (FileNotFoundError, PermissionError):
                pass
            
            # try to check locally (same folder as executable)
            try:
                with open(key_file, 'r') as f:
                    api_key = f.readline().strip()
                    if api_key:
                        break
            except (FileNotFoundError, PermissionError):
                pass

        if not api_key or api_key == '':
                if show_error:
                    # No error sound or pause: the settings page asks for the key once per model list, so a missing key would beep many times at start-up
                    log.warning(f'''No secret key found in GPT_SECRET_KEY.txt.
Please create a secret key and paste it in your Mantella mod folder's GPT_SECRET_KEY.txt file.
If you are using OpenRouter (default), you can create a secret key in Account -> Keys once you have created an account: https://openrouter.ai/
If using OpenAI, see here on how to create a secret key: https://help.openai.com/en/articles/4936850-where-do-i-find-my-openai-api-key
If you are running a model locally, please ensure the service (eg Kobold / Text generation web UI) is selected and running via: http://localhost:4999/ui
For more information, see here: https://art-from-the-machine.github.io/Mantella/''')

        return api_key
    

    @utils.time_it
    def __get_token_limit(self, llm, custom_token_count: int, is_local):
        '''Determines the token limit for the given LLM, using known values, manual overrides, or a default

        Args:
            llm (str): The name of the language model
            custom_token_count (int): A user-defined fallback token count from the config
            is_local (bool): Whether the model is running locally

        Returns:
            token_limit (int): The determined token limit for the model
        '''
        manual_limits = utils.get_model_token_limits()
        token_limit_dict = {**self.api_token_limits, **manual_limits}

        if '/' in llm:
            llm = llm.split('/')[-1]

        if llm in token_limit_dict:
            token_limit = token_limit_dict[llm]
        else:
            log.info(f"Could not find number of available tokens for {llm}. Defaulting to token count of {custom_token_count}. This number can be changed via the `Large Language Model`->`Custom Token Count` / `Vision`->`Custom Vision Model Token Count` settings in the Mantella UI")
            try:
                token_limit = custom_token_count
            except ValueError:
                log.error(f"Invalid custom_token_count value: {custom_token_count}. It should be a valid integer. Please update your configuration.")
                token_limit = 4096  # Default to 4096 in case of an error.
        if token_limit <= 4096:
            if is_local:
                llm = 'Local language model'
            log.warning(f"{llm} has a low token count of {token_limit}. For better NPC memories, try changing to a model with a higher token count")
        
        return token_limit
    

    @utils.time_it
    def __get_model_encoding(self, api_url: str, llm: str) -> tiktoken.Encoding:
        '''Gets the appropriate tiktoken encoding for the specified model or a sensible default

        Args:
            api_url (str): The API service/URL being used
            llm (str): The name of the language model

        Returns:
            tiktoken.Encoding: The encoding object for token counting
        '''
        chosenmodel = llm
        # if using an alternative API to OpenAI, use encoding for GPT-3.5 by default
        # NOTE: this encoding may not be the same for all models, leading to incorrect token counts
        #       this can lead to the token limit of the given model being overrun
        try:
            if api_url == 'OpenAI':
                encoding = tiktoken.encoding_for_model(chosenmodel) # get encoding for specific model
            else:
                encoding = tiktoken.get_encoding('cl100k_base') # get generic encoding
        except:
            try:
                encoding = tiktoken.get_encoding('cl100k_base') # try loading a generic encoding
            except:
                log.error('Error loading model. If you are using an alternative to OpenAI, please find the setting `Large Language Model`->`LLM Service` in the Mantella UI and follow the instructions to change this setting')
                raise
        
        return encoding
    
    @utils.time_it
    def get_count_tokens(self, messages: message_thread | list[Message] | Message | str) -> int:
        if isinstance(messages, message_thread | list) :
            return self.__num_tokens_from_messages(messages)
        elif isinstance(messages, Message):
            return self.__num_tokens_from_message(messages)
        else:
            return len(self._encoding.encode(messages))

    @utils.time_it
    def is_too_long(self, messages: message_thread | list[Message] | Message | str, token_limit_percent: float) -> bool:
        countTokens: int = self.get_count_tokens(messages)
        return countTokens > self.token_limit * token_limit_percent

    @utils.time_it
    def __num_tokens_from_messages(self, messages: message_thread | list[Message]) -> int:
        '''Calculates token count for a list of messages formatted for OpenAI API calls

        Args:
            messages (message_thread | list[Message]): The messages to count tokens for

        Returns:
            num_tokens (int): The estimated total token count
        '''
        messages_to_check = []
        if isinstance(messages, message_thread):
            messages_to_check = messages.append_decensor_commitment(messages.get_openai_messages())
        else:
            for m in messages:
                messages_to_check.append(m.get_openai_message())

        # note: this calculation is based on GPT-3.5, future models may deviate from this
        num_tokens = 0
        for message in messages_to_check:
            num_tokens += 4  # every message follows <im_start>{role/name}\n{content}<im_end>\n
            for key, value in message.items():
                if isinstance(value, str):
                    num_tokens += len(self._encoding.encode(value))
                    if key == "name":  # if there's a name, the role is omitted
                        num_tokens += -1  # role is always required and always 1 token
        num_tokens += 2  # every reply is primed with <im_start>assistant
        return num_tokens
    
    @utils.time_it
    def __num_tokens_from_message(self, message_to_measure: Message | str) -> int:
        '''Calculates the approximate token count for a single Message object or string,
        considering the overhead of the message structure

        Args:
            message_to_measure (Message | str): The message or string content

        Returns:
            num_tokens (int): Estimated token count
        '''
        text: str = ""
        if isinstance(message_to_measure, Message):
            text = message_to_measure.get_formatted_content()
        else:
            text = message_to_measure

        num_tokens = 4 # every message follows <im_start>{role/name}\n{content}<im_end>\n
        num_tokens += len(self._encoding.encode(text))
        if isinstance(message_to_measure, Message) and message_to_measure.get_openai_message().__contains__("name"):# if there's a name, the role is omitted
            num_tokens += -1# role is always required and always 1 token
        
        return num_tokens
    
    @staticmethod
    def _reads_images(architecture: dict) -> bool:
        """Whether a model takes images among what it is given. Many also take files, audio or video (Gemini: text+image+file+audio+video->text)"""
        inputs = architecture.get("input_modalities")
        if not inputs:
            inputs = architecture.get("modality", "").split("->")[0].split("+")
        return "image" in inputs

    @staticmethod
    def custom_model_list() -> LLMModelList:
        """The model list of a service without one, like a local server: the player types the model name"""
        return LLMModelList([("Custom model","Custom model")], "Custom model", allows_manual_model_input=True)

    @staticmethod
    def get_model_list(service: str, secret_key_file: str, default_model: str = "openrouter/free", is_vision: bool = False) -> LLMModelList:
        if service not in ['OpenAI', 'OpenRouter', 'NanoGPT']:
            return ClientBase.custom_model_list()
        try:
            if service == "OpenAI":
                default_model = "gpt-4o-mini"
                models = utils.get_openai_model_list()
                # OpenAI models are not a "live" list, so manual input needs to be allowed for when new models not listed are released
                allow_manual_model_input = True
            elif service == "OpenRouter":
                default_model = default_model
                secret_key_files = [secret_key_file, 'GPT_SECRET_KEY.txt'] if secret_key_file != 'GPT_SECRET_KEY.txt' else [secret_key_file]
                secret_key = ClientBase._get_api_key(secret_key_files, not is_vision)
                if not secret_key:
                    return LLMModelList([(f"No secret key found in {secret_key_file}", "Custom model")], "Custom model", allows_manual_model_input=True)
                # NOTE: while a secret key is not needed for this request, this may change in the future
                client = OpenAI(api_key=secret_key, base_url='https://openrouter.ai/api/v1')
                # don't log initial 'HTTP Request: GET https://openrouter.ai/api/v1/models "HTTP/1.1 200 OK"'
                logging.getLogger('openai').setLevel(logging.ERROR)
                logging.getLogger("httpx").setLevel(logging.ERROR)
                models = client.models.list()
                logging.getLogger('openai').setLevel(logging.NOTSET)
                logging.getLogger("httpx").setLevel(logging.NOTSET)
                client.close()
                allow_manual_model_input = False
            elif service == "NanoGPT":
                default_model = "gpt-4o-mini"  # Use a common default that's likely to be available
                # Use key file resolver to get appropriate key files
                from src.llm.key_file_resolver import key_file_resolver
                secret_key_files = key_file_resolver.get_key_files_for_service("NanoGPT", secret_key_file)
                secret_key = ClientBase._get_api_key(secret_key_files, not is_vision)
                if not secret_key:
                    return LLMModelList([(f"No secret key found in NANOGPT_SECRET_KEY.txt or {secret_key_file}", "Custom model")], "Custom model", allows_manual_model_input=True)
                
                # Use requests to call NanoGPT with detailed=true parameter
                headers = {"Authorization": f"Bearer {secret_key}"}
                url = "https://nano-gpt.com/api/v1/models?detailed=true"
                
                # don't log HTTP requests during model fetching
                logging.getLogger('openai').setLevel(logging.ERROR)
                logging.getLogger("httpx").setLevel(logging.ERROR)
                logging.getLogger('urllib3').setLevel(logging.ERROR)
                
                response = requests.get(url, headers=headers)
                
                logging.getLogger('openai').setLevel(logging.NOTSET)
                logging.getLogger("httpx").setLevel(logging.NOTSET)
                logging.getLogger('urllib3').setLevel(logging.NOTSET)
                
                if response.status_code != 200:
                    raise Exception(f"NanoGPT API returned status {response.status_code}: {response.text}")
                
                models_data = response.json()
                allow_manual_model_input = True  # Allow manual input for NanoGPT in case new models are added

            options = []
            multiplier = 1_000_000
            
            # Handle different response formats based on service
            if service == "NanoGPT":
                # NanoGPT returns detailed JSON response directly
                models_list = models_data.get("data", [])
                for model in models_list:
                    try:
                        model_id = model.get("id", "unknown")
                        model_name = model.get("name", model_id)
                        context_size = model.get("context_length", 0)
                        pricing = model.get("pricing", {})
                        
                        # Always try to show detailed information, even if some parts are missing
                        model_display_parts = [f"{model_name} ({model_id})"]
                        
                        # Add context size if available
                        if context_size and context_size > 0:
                            model_display_parts.append(f"Context: {utils.format_context_size(context_size)}")
                            ClientBase.api_token_limits[model_id.split('/')[-1]] = context_size
                        
                        # Add pricing if available
                        if pricing:
                            prompt_cost = float(pricing.get("prompt", 0))
                            completion_cost = float(pricing.get("completion", 0))
                            
                            # Show pricing even if one cost is missing/zero
                            if prompt_cost >= 0 or completion_cost >= 0:
                                cost_parts = []
                                cost_parts.append(f"Prompt: {utils.format_price(prompt_cost)}")
                                cost_parts.append(f"Completion: {utils.format_price(completion_cost)}")
                                model_display_parts.append(f"Cost per 1M tokens: {'. '.join(cost_parts)}")
                        
                        # Join all available parts with " | "
                        model_display_name = " | ".join(model_display_parts)
                     
                    except Exception as e:
                        # Fallback to model ID if parsing fails
                        model_display_name = model.get("id", "unknown")
                    
                    options.append((model_display_name, model.get("id", "unknown")))
            else:
                # OpenRouter and OpenAI use the existing models.data format
                for model in models.data:
                    try:
                        if model.model_extra:
                            context_size: int = model.model_extra["context_length"]
                            prompt_cost: float = float(model.model_extra["pricing"]["prompt"]) * multiplier
                            completion_cost: float = float(model.model_extra["pricing"]["completion"]) * multiplier
                            vision_available: str = ' | Vision Available' if ClientBase._reads_images(model.model_extra["architecture"]) else ''
                            model_display_name = f"{model.id} | Context: {utils.format_context_size(context_size)} | Cost per 1M tokens: Prompt: {utils.format_price(prompt_cost)}. Completion: {utils.format_price(completion_cost)}{vision_available}"
                            
                            ClientBase.api_token_limits[model.id.split('/')[-1]] = context_size
                        else:
                            model_display_name = model.id
                    except:
                        model_display_name = model.id
                    options.append((model_display_name, model.id))
            
            # check if any models are marked as vision-capable
            has_vision_models = any(' | Vision Available' in name for name, _ in options)
            # filter model list if this is supposed to be a vision model list and there are models explicitly marked as such
            if is_vision and has_vision_models:
                options = [(name, model_id) for name, model_id in options if ' | Vision Available' in name]
            
            # For NanoGPT, we could also check for vision capability in the model name or description
            # This is a fallback in case NanoGPT doesn't use the same vision marking system
            if service == "NanoGPT" and is_vision and not has_vision_models:
                # Filter based on common vision model indicators in name/description
                vision_indicators = ['vision', 'visual', 'image', 'multimodal', 'vlm']
                options = [(name, model_id) for name, model_id in options 
                          if any(indicator in name.lower() or indicator in model_id.lower() for indicator in vision_indicators)]
            
            return LLMModelList(options, default_model, allows_manual_model_input=allow_manual_model_input)
        except Exception as e:
            utils.play_error_sound()
            error = f"Failed to retrieve list of models from {service}. A valid API key in 'GPT_SECRET_KEY.txt' is required. The file is in your mod folder of Mantella. Error: {e}"
            return LLMModelList([(error,"error")], "error", allows_manual_model_input=False)

    # The decision model list of each service, and the number its prompt price is multiplied by to get the price per 1M tokens
    DECISION_MODELS_URLS = {
        "OpenRouter": ("https://openrouter.ai/api/v1/models?output_modalities=decisions", 1_000_000),
        "NanoGPT": ("https://nano-gpt.com/api/v1/models?detailed=true", 1),
    }
    DEFAULT_DECISION_MODEL = "perplexity/pplx-decider-v1.2"

    @staticmethod
    def get_decision_model_list(service: str, secret_key_file: str, default_model: str = DEFAULT_DECISION_MODEL, is_vision: bool = False) -> LLMModelList:
        """Lists the decision models on OpenRouter or NanoGPT (e.g. perplexity/pplx-decider-v1.2). These do not appear in the OpenRouter chat model list.
        For a typed address (a decision server on the player's own computer), the model name is typed, as for a local LLM.
        The arguments match the other model list getters. The lists need no key, so the key file is not used.
        """
        if service not in ClientBase.DECISION_MODELS_URLS:
            return ClientBase.custom_model_list()
        default_model = ClientBase.DEFAULT_DECISION_MODEL
        try:
            url, price_multiplier = ClientBase.DECISION_MODELS_URLS[service]
            logging.getLogger('urllib3').setLevel(logging.ERROR)
            response = requests.get(url, timeout=30)
            logging.getLogger('urllib3').setLevel(logging.NOTSET)
            response.raise_for_status()
            options = []
            for model in response.json().get("data", []):
                model_id = model["id"]
                # NanoGPT lists all its models, so only the decision models are kept
                if service == "NanoGPT" and "decisions" not in ((model.get("architecture") or {}).get("output_modalities") or []):
                    continue
                try:
                    context_size = int(model.get("context_length") or 0)
                    prompt_cost = float(model["pricing"]["prompt"]) * price_multiplier
                    context_text = f"Context: {utils.format_context_size(context_size)}" if context_size > 0 else "Context: unknown"
                    model_display_name = f"{model_id} | {context_text} | Cost per 1M tokens: Prompt: {utils.format_price(prompt_cost)}"
                    if context_size > 0:
                        ClientBase.api_token_limits[model_id.split('/')[-1]] = context_size
                except Exception:
                    model_display_name = model_id
                options.append((model_display_name, model_id))
            return LLMModelList(options, default_model, allows_manual_model_input=False)
        except Exception as e:
            log.error(f"Failed to retrieve the list of decision models from {service}: {e}")
            # Manual input keeps the saved decision model selected while the list can't be loaded
            return LLMModelList([(default_model, default_model)], default_model, allows_manual_model_input=True)