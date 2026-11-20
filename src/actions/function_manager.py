from __future__ import annotations
import json
import logging
from pathlib import Path
from copy import deepcopy
from typing import TYPE_CHECKING
from src.conversation.action import Action
from src import utils
if TYPE_CHECKING: # Only for type hints: the config loads the actions, and these import the config
    from openai.types.chat import ChatCompletionMessageToolCall
    from src.character_manager import Character
    from src.characters_manager import Characters
    from src.games.gameable import Gameable

logger = logging.getLogger()


class FunctionManager:
    _actions: dict[str, dict] = {}  # Map identifier -> action data
    _last_tool_calls: list[dict] = []  # Cache of last turn's parsed tool calls for duplicate filtering
    _disabled_action_names: list[str] = []  # Track disabled actions for logger

    @staticmethod
    def parse_function_calls(tools_called: list[ChatCompletionMessageToolCall], characters: Characters = None, game: Gameable | None = None,
                             filter_repeats: bool = True, record_shares: bool = True) -> list[dict]:
        """Parse function calls from the LLM response and validate arguments
        
        Args:
            tools_called: The result from the LLM
            characters: Characters manager for NPC name validation (optional)
            game: Game instance for resolving game-specific parameters like idle IDs (optional)
            filter_repeats: drop a call that repeats one of the last calls read (our conversations never do: see read_tool_calls)
            record_shares: ShareConversation is remembered right away. Ours only once its action reaches the game (record_shares)
            
        Returns:
            List of parsed function call information with validated parameters
        """
        parsed_tools = []
        
        if tools_called:
            for tool_call in tools_called:
                try:
                    # Find the action identifier from the function name
                    identifier = None
                    for action_id, action_data in FunctionManager._actions.items():
                        if action_data['name'] == tool_call['function']['name']:
                            identifier = action_id
                            break
                    
                    if not identifier:
                        break  # Unknown function, skip

                    try:
                        # While the LLM should return arguments in JSON format, 
                        # the OpenAI package returns them in a string format in case of malformed JSON
                        if tool_call['function']['arguments']: # Not all actions have arguments
                            parsed_arguments = json.loads(tool_call['function']['arguments'])
                        else:
                            parsed_arguments = {}
                    except json.JSONDecodeError:
                        logger.warning(f"Could not parse function arguments as JSON: {tool_call['function']['arguments']}")
                        parsed_arguments = {}

                    # Get the action definition to validate against
                    action_def = FunctionManager._actions[identifier]
                    defined_params = action_def.get('parameters', {})
                    
                    # Validate that arguments match the schema
                    validated_args = FunctionManager._validate_arguments_against_schema(parsed_arguments, defined_params, identifier)
                    
                    # Validate entity names and resolve IDs based on parameter definitions
                    if validated_args:
                        for param_name, param_value in list(validated_args.items()):
                            param_def = defined_params.get(param_name, {})
                            scope: str | None = param_def.get('scope')
                            resolve_type: str | None = param_def.get('resolve_to_id')
                            
                            # Validate parameters with scopes (NPC names, etc.)
                            if scope and characters:
                                # Ensure the value is a list for easier validation
                                entity_names = param_value if isinstance(param_value, list) else [param_value]

                                if scope.startswith('conversation') or scope.startswith('nearby') or scope.startswith('all_npcs'):
                                    # Determine validation flags based on scope
                                    include_player = scope.endswith('_w_player')
                                    include_nearby = scope.startswith('nearby') or scope.startswith('all_npcs')
                                    nearby_only = scope.startswith('nearby')
                                    
                                    # Validate the NPC names
                                    validated_entities = FunctionManager._validate_npc_names(
                                        entity_names, 
                                        characters, 
                                        exclude_player=not include_player,
                                        include_nearby=include_nearby,
                                        nearby_only=nearby_only
                                    )
                                    
                                    # Update the validated args with the validated list
                                    # Preserve the original type (string or list)
                                    if isinstance(param_value, list):
                                        if validated_entities:
                                            validated_args[param_name] = validated_entities
                                        else:
                                            FunctionManager._log_skipped(identifier, param_name, entity_names, characters, nearby_only)
                                            validated_args = None
                                            break
                                    else:
                                        if validated_entities:
                                            validated_args[param_name] = validated_entities[0]
                                        else:
                                            FunctionManager._log_skipped(identifier, param_name, entity_names, characters, nearby_only)
                                            validated_args = None
                                            break
                            
                            # Resolve parameters to game IDs (eg idle names to FormIDs, NPC names to ref_ids)
                            if resolve_type and game and validated_args:
                                resolved_value = FunctionManager._resolve_parameter_to_id(param_value, resolve_type, game)
                                if resolved_value is not None:
                                    # Add resolved ID as new argument with _id suffix
                                    validated_args[f"{param_name}_id"] = resolved_value
                                    validated_args[f'{param_name}_succeeded'] = True
                                else:
                                    # For NPC resolution, add failure feedback but don't skip the action
                                    if resolve_type == 'npc':
                                        validated_args[f'{param_name}_succeeded'] = False
                                    else:
                                        logger.warning(f"Skipping action '{identifier}' - could not resolve '{param_value}' to {resolve_type} ID")
                                        validated_args = None
                                        break

                    # Only add the parsed tool if validation didn't fail
                    if validated_args is not None:
                        parsed_tool = {
                            'identifier': identifier
                        }
                        
                        # Only include arguments if there are any,
                        # tools without actions will be treated as basic actions
                        if validated_args:
                            parsed_tool['arguments'] = validated_args
                        
                        # Check for duplicate tool call from previous turn
                        if filter_repeats and FunctionManager._is_duplicate_call(parsed_tool, action_def):
                            logger.log(23, f"Filtered duplicate tool call: {identifier} with args {parsed_tool.get('arguments', {})}")
                            continue
                        
                        # Handle action-specific side effects
                        FunctionManager._handle_action_side_effects(parsed_tool, identifier, characters, record_shares)
                        
                        parsed_tools.append(parsed_tool)
                except Exception as e:
                    logger.error(f"Error parsing function call: {e}")
        
        # Update cache with this turn's tool calls
        if len(parsed_tools) > 0:
            FunctionManager._last_tool_calls = parsed_tools
        
        return parsed_tools


    @staticmethod
    def load_all_actions(disabled_actions: list[str] | None = None, actions_dir: str | None = None) -> None:
        """Load all actions from the data/actions/ folder at server startup

        Args:
            disabled_actions: List of action names to disable. If None, all actions are loaded.
            actions_dir: The folder to load from instead of data/actions/ (e.g. in a test)
        """
        actions_dir = Path(actions_dir) if actions_dir else Path(utils.resolve_path()) / "data" / "actions"
        
        if not actions_dir.exists():
            logger.warning(f"Actions directory '{actions_dir}' not found")
            return

        FunctionManager._actions.clear()
        FunctionManager._last_tool_calls = []
        FunctionManager._disabled_action_names = []

        disabled_set = set(n.lower() for n in disabled_actions) if disabled_actions is not None else set()

        # Load top-level action files
        for file_path in actions_dir.glob("*.json"):
            try:
                FunctionManager._load_action_file(file_path, disabled_set)
            except Exception as e:
                logger.warning(f"Failed to load action file {file_path}: {e}")

        # TODO: Load new functions folder
        # Load functions folder
        # functions_dir = actions_dir / "functions"
        # if functions_dir.exists():
        #     for file_path in functions_dir.glob("*.json"):
        #         try:
        #             FunctionManager._load_action_file(file_path)
        #         except Exception as e:
        #             logger.warning(f"Failed to load function file {file_path}: {e}")

        logger.log(23, f"""Loaded {len(FunctionManager._actions)} actions from: 
    {actions_dir}""")


    @staticmethod
    def log_actions_enabled(advanced_actions_enabled: bool) -> None:
        """Log a summary of available actions based on current mode
        
        Args:
            advanced_actions_enabled: Whether advanced (tool-based) actions are enabled
        """
        if advanced_actions_enabled:
            # All loaded actions are available
            enabled_names = [action.get('name', 'Unknown') for action in FunctionManager._actions.values()]
        else:
            # Only actions with prompts (Basic actions) are available
            enabled_names = [action.get('name', 'Unknown') for action in FunctionManager._actions.values() if action.get('prompt')]
        
        mode = "Enabled" if advanced_actions_enabled else "Basic"
        if enabled_names:
            logger.log(23, f"{mode} actions: {', '.join(enabled_names)}")
        else:
            logger.log(23, f"{mode} actions: None")
        
        # Log disabled actions
        if FunctionManager._disabled_action_names:
            logger.log(23, f"Disabled actions: {', '.join(FunctionManager._disabled_action_names)}\n")
        else:
            logger.log(23, "")


    @staticmethod
    def get_legacy_actions() -> list:
        """Convert loaded actions to legacy Action objects for prompt-based system
        
        Returns:
            List of Action objects for use with the legacy prompt-based action system
        """
        
        result = []
        for action_data in FunctionManager._actions.values():
            if action_data.get('prompt') is None:
                continue  # Skip actions without prompts for legacy system
            identifier = action_data['identifier']
            name = action_data.get('name', '')
            key = action_data.get('key', '')
            description = action_data.get('description', '')
            prompt = action_data.get('prompt', '')
            requires_response = bool(action_data.get('requires_response', False))
            is_interrupting = bool(action_data.get('is-interrupting', action_data.get('is_interrupting', False)))
            one_on_one = bool(action_data.get('one-on-one', action_data.get('one_on_one', False)))
            multi_npc = bool(action_data.get('multi-npc', action_data.get('multi_npc', False)))
            radiant = bool(action_data.get('radiant', False))
            
            result.append(Action(identifier, name, key, description, prompt, requires_response, is_interrupting, one_on_one, multi_npc, radiant))
        
        return result


    @staticmethod
    def get_all_actions() -> list[Action]:
        """Every loaded action as an Action: the basic ones (see get_legacy_actions) and the ones only Advanced Actions can use, whose
        prompt text is empty"""
        result = []
        for action_data in FunctionManager._actions.values():
            result.append(Action(action_data['identifier'], action_data.get('name', ''), action_data.get('key', ''), action_data.get('description', ''),
                                 action_data.get('prompt') or '', bool(action_data.get('requires_response', False)),
                                 bool(action_data.get('is-interrupting', action_data.get('is_interrupting', False))),
                                 bool(action_data.get('one-on-one', action_data.get('one_on_one', False))),
                                 bool(action_data.get('multi-npc', action_data.get('multi_npc', False))), bool(action_data.get('radiant', False))))
        return result


    @staticmethod
    def _load_action_file(file_path: Path, disabled_set: set[str] | None = None) -> None:
        """Load a single action file

        Args:
            disabled_set: Set of lowercased action names to skip to check current action against.
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Handle both single action and array of actions
        actions_data = data if isinstance(data, list) else [data]

        for action_data in actions_data:
            name = action_data.get('name')
            if not name:
                logger.warning(f"Action in '{file_path.name}' is missing a 'name' field, skipping")
                continue

            # Skip actions whose name is in the disabled set
            if disabled_set is not None and name.lower() in disabled_set:
                FunctionManager._disabled_action_names.append(name)
                continue
            
            # Ensure identifier starts with 'mantella_'. Lower case, as the game's scripts may change the case of a word
            identifier = action_data['identifier'].lower()
            action_data['identifier'] = f"mantella_{identifier}" if not identifier.startswith('mantella_') else identifier

            FunctionManager._actions[action_data['identifier']] = action_data
            logger.debug(f"Loaded action: {action_data['identifier']}")


    @staticmethod
    def generate_context_aware_tools(context, game: Gameable = None) -> list[dict]:
        """Generate OpenAI tools based on current conversation context
        
        Args:
            context: The conversation context
            game: The Gameable instance for game-specific operations (e.g., idle lookups)
        """
        tools = []
        
        for action in FunctionManager._actions.values():
            # Filter by game compatibility
            if 'allowed_games' in action and action['allowed_games']:
                base_game_name = context.config.game.base_game.display_name.lower().replace(" ", "")
                cleaned_allowed_games = [g.lower().replace(" ", "") for g in action['allowed_games']]
                if base_game_name not in cleaned_allowed_games:
                    continue

            # Filter by conversation type
            is_multi_npc = context.npcs_in_conversation.contains_multiple_npcs()
            is_radiant = is_multi_npc and not context.npcs_in_conversation.contains_player_character()
            if is_radiant:
                if not action.get('radiant', False):
                    continue
            elif is_multi_npc:
                if not action.get('multi_npc', action.get('multi-npc', False)):
                    continue
            else:
                if not action.get('one_on_one', action.get('one-on-one', False)):
                    continue

            # Create OpenAI tool definition
            tool = {
                'type': 'function',
                'function': {
                    'name': action['name'],
                    'description': action['description']
                }
            }

            
            tool['function']['parameters'] = {}
            tool['function']['parameters']['type'] = 'object'
            tool['function']['parameters']['properties'] = {}
            # Only populate parameters if they exist
            if 'parameters' in action:
                tool['function']['parameters']['properties'] = deepcopy(action['parameters'])
                if 'required' in action:
                    tool['function']['parameters']['required'] = action['required']
                
                # Populate dynamic enums and clean internal fields
                if not FunctionManager._populate_dynamic_enums(tool['function']['parameters'], game, action['identifier']):
                    continue
                
                # Add context-aware entity listings, skipping tools when no scoped params have available entities
                if not FunctionManager._add_npc_context_to_parameters(tool['function']['parameters'], context):
                    continue

            tools.append(tool)

        return tools


    @staticmethod
    def generate_tools(actions: list[Action], characters: Characters, game: Gameable | None, base_game_name: str, include_player: bool,
                       free_places: int | None = None, several_speakers: bool = False) -> list[dict]:
        """The tools for the actions an NPC can use in this conversation (see action_rules.tool_actions_for)

        Each NPC acts only for themselves: an action's source, who does it, is left out of its tool, and the speaker is put in when the call is
        read (parse_tool_calls). AddToConversation keeps it, as it names who gets called over. When one reply speaks for several NPCs
        (several_speakers), every tool names who does it instead, one of the NPCs in the conversation, never the player

        Args:
            actions (list[Action]): the actions the NPC can use
            characters (Characters): the conversation, and who is nearby, for the names the tools list
            base_game_name (str): for the actions that only work in some games
            include_player (bool): False while the NPCs don't know the player is there: no tool can name the player
            free_places (int | None): how many more people can join (Most People in Conversation). AddToConversation is left out at 0
            several_speakers (bool): one reply speaks for several NPCs (a group conversation without Game Master Mode, a radiant conversation)
        """
        tools = []
        for action in actions:
            action_def = FunctionManager._actions.get(action.identifier)
            if not action_def:
                continue
            if action_def.get('allowed_games'):
                cleaned_allowed_games = [g.lower().replace(" ", "") for g in action_def['allowed_games']]
                if base_game_name.lower().replace(" ", "") not in cleaned_allowed_games:
                    continue
            is_add = action.identifier == FunctionManager.ADD_TO_CONVERSATION
            if is_add and free_places is not None and free_places <= 0:
                continue
            properties: dict = deepcopy(action_def.get('parameters', {}))
            required: list[str] = list(action_def.get('required', []))
            if not is_add and several_speakers:
                # Who does it, one of the NPCs: also for the actions that are otherwise always done by the speaker
                properties['source'] = {**properties.get('source', {'type': 'string'}), 'scope': 'conversation',
                                        'description': FunctionManager.DOER_DESCRIPTION}
                required = [r for r in required if r != 'source'] + ['source']
            elif not is_add and 'source' in properties:
                del properties['source']
                required = [r for r in required if r != 'source']
            for param_def in properties.values():
                scope = param_def.get('scope')
                if scope and not include_player and scope.endswith('_w_player'):
                    param_def['scope'] = scope[:-len('_w_player')]
            if is_add and free_places is not None:
                properties['source']['description'] = (properties['source'].get('description', '') + f" At most {free_places} can join.").strip()
            parameters: dict = {'type': 'object', 'properties': properties}
            if required:
                parameters['required'] = required
            if not FunctionManager._populate_dynamic_enums(parameters, game, action.identifier):
                continue
            if not FunctionManager._add_names_to_parameters(parameters, characters):
                continue
            for param_def in properties.values():
                param_def.pop('scope', None) # Only for Mantella: the names are in the description
            tools.append({'type': 'function', 'function': {'name': action_def['name'], 'description': action_def.get('description', ''), 'parameters': parameters}})
        return tools


    ADD_TO_CONVERSATION = "mantella_npc_addtoconversation"
    DOER_DESCRIPTION = "Who does it: the NPC in the conversation whose line goes with it."


    @staticmethod
    def parse_tool_calls(tool_calls: list[dict], speaker: Character, characters: Characters, game: Gameable | None, actions: list[Action],
                         include_player: bool, free_places: int | None = None, several_speakers: bool = False) -> list[dict]:
        """The actions of read_tool_calls, without who does each"""
        return [action for _, action in FunctionManager.read_tool_calls(tool_calls, speaker, characters, game, actions, include_player, free_places,
                                                                        several_speakers)]


    @staticmethod
    def read_tool_calls(tool_calls: list[dict], speaker: Character, characters: Characters, game: Gameable | None, actions: list[Action],
                        include_player: bool, free_places: int | None = None, several_speakers: bool = False) -> list[tuple[Character, dict]]:
        """Reads the AI model's tool calls for the speaker's reply into the actions sent to the game, each with the NPC who does it

        Each NPC acts only for themselves: whoever the model names as the source, the speaker does it (AddToConversation names who to call
        over instead). When one reply speaks for several NPCs (several_speakers), the model names who does it, one of the NPCs in the
        conversation, and the speaker does it only if it names none of them. Only the actions the NPC can use here are kept (actions), never
        one that names the player while the NPCs don't know the player is there, and AddToConversation calls over at most free_places people.
        Repeated actions are kept here (see ChatManager.__send_picked_actions for the NPC's previous reply)

        Args:
            tool_calls (list[dict]): {"id", "type", "function": {"name", "arguments"}}, the arguments as JSON text
        """
        allowed = {a.identifier: a for a in actions}
        npcs = {c.name: c for c in characters.get_all_characters() if not c.is_player_character}
        npc_names = {name.lower(): name for name in npcs} # The model may write "hulda"
        player_names = {"player"} | ({characters.get_player_name().lower()} if characters.get_player_name() else set())
        result = []
        for tool_call in tool_calls:
            try:
                name = tool_call['function']['name']
                identifier = next((i for i, a in FunctionManager._actions.items() if a.get('name') == name), None)
                if not identifier or identifier not in allowed:
                    logger.log(23, f"{speaker.name} picked {name}, which NPCs can't use in this conversation. Nothing happens")
                    continue
                raw_arguments = tool_call['function'].get('arguments') or '{}'
                try:
                    arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
                except json.JSONDecodeError:
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                source = FunctionManager._actions[identifier].get('parameters', {}).get('source')
                doers = [speaker.name]
                if identifier != FunctionManager.ADD_TO_CONVERSATION:
                    named = arguments.pop('source', None)
                    if several_speakers:
                        named_npcs = [npc_names[n.strip().lower()] for n in (named if isinstance(named, list) else [named])
                                      if isinstance(n, str) and n.strip().lower() in npc_names]
                        doers = list(dict.fromkeys(named_npcs)) or doers
                    if source:
                        arguments['source'] = doers if source.get('type') == 'array' else doers[0]
                call = {'id': tool_call.get('id', ''), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}
                parsed = FunctionManager.parse_function_calls([call], characters, game, filter_repeats=False, record_shares=False)
            except Exception as e:
                logger.error(f"Could not read the action {tool_call}: {e}")
                continue
            for action in parsed:
                arguments = action.get('arguments', {})
                if not include_player:
                    named = [str(n).lower() for value in arguments.values() for n in (value if isinstance(value, list) else [value]) if isinstance(n, str)]
                    if any(n in player_names for n in named):
                        logger.log(23, f"{speaker.name}'s {action['identifier']} names the player, who isn't part of this conversation. Nothing happens")
                        continue
                if action['identifier'] == FunctionManager.ADD_TO_CONVERSATION and free_places is not None and isinstance(arguments.get('source'), list):
                    if free_places <= 0:
                        continue
                    arguments['source'] = arguments['source'][:free_places]
                result.append((npcs.get(doers[0], speaker), action))
        return result


    @staticmethod
    def record_shares(actions: list[dict], characters: Characters) -> None:
        """ShareConversation: once its action has reached the game, the recipient learns about this conversation when it ends"""
        for action in actions:
            if not isinstance(action, dict) or action.get('identifier') != 'mantella_npc_shareconversation':
                continue
            arguments = action.get('arguments', {})
            if arguments.get('recipient_succeeded') and arguments.get('recipient_id'):
                sharer = arguments.get('source', '')
                sharer = sharer[0] if isinstance(sharer, list) and sharer else sharer
                characters.add_pending_share(str(sharer), arguments.get('recipient', ''), arguments.get('recipient_id', ''))


    @staticmethod
    def _add_names_to_parameters(parameters: dict, characters: Characters) -> bool:
        """_add_npc_context_to_parameters for our conversations: the names each scoped parameter can take go in its description, from the
        conversation and the people nearby the game sent. False if no scoped parameter has anyone to name, so the tool is left out"""
        scoped_params = 0
        scoped_with_names = 0
        has_unscoped_params = False
        for param_def in parameters.get('properties', {}).values():
            scope = param_def.get('scope')
            if not scope:
                has_unscoped_params = True
                continue
            scoped_params += 1
            include_player = scope.endswith('_w_player')
            if scope.startswith('conversation'):
                names = characters.get_all_names_w_nearby(include_player=include_player)
            elif scope.startswith('nearby'):
                names = characters.get_all_names_w_nearby(include_player=include_player, nearby_only=True)
            elif scope.startswith('all_npcs'):
                names = characters.get_all_names_w_nearby(include_player=include_player, include_nearby=True)
            else:
                names = []
            description = param_def.get('description', '')
            if names:
                param_def['description'] = f"{description} Available: {', '.join(names)}".strip()
                scoped_with_names += 1
            else:
                param_def['description'] = f"{description} No entities available.".strip()
        return scoped_params == 0 or scoped_with_names > 0 or has_unscoped_params


    @staticmethod
    def any_action_requires_response(actions: list[dict]) -> bool:
        """Return True if any action in the list requires a response from the game
        
        Args:
            actions: List of action dicts with 'identifier' keys
            
        Returns:
            bool: True if at least one action requires game response
        """
        return any(
            FunctionManager._action_requires_response(action.get('identifier', ''))
            for action in actions if isinstance(action, dict)
        )


    @staticmethod
    def _action_requires_response(identifier: str) -> bool:
        """Check if a single action requires game response
        
        Args:
            identifier: Action identifier string
            
        Returns:
            bool: True if action has 'requires_response' flag set to True
        """
        action = FunctionManager._actions.get(identifier)
        if not action:
            return False
        return bool(action.get('requires_response', False))


    @staticmethod
    def _is_duplicate_call(parsed_tool: dict, action_def: dict) -> bool:
        """Check if this tool call is a duplicate of one from the previous turn"""
        if action_def.get('allow_repeat', False):
            return False
        
        for last_call in FunctionManager._last_tool_calls:
            if (parsed_tool.get('identifier') == last_call.get('identifier') and
                parsed_tool.get('arguments', {}) == last_call.get('arguments', {})):
                return True
        return False

    @staticmethod
    def is_vision_action_active() -> bool:
        """Return True if the Vision action is loaded and enabled"""
        return 'mantella_npc_vision' in FunctionManager._actions

    @staticmethod
    def get_action_pause_seconds(identifier: str) -> float:
        """Get the pause_seconds value for an action if defined (Listen action)
        
        Args:
            identifier: The action identifier
            
        Returns:
            The pause_seconds value if defined, otherwise a default value
        """
        default_seconds = 10
        action = FunctionManager._actions.get(identifier)
        if not action:
            return default_seconds
        return action.get('pause_seconds', default_seconds)

    @staticmethod
    def _handle_action_side_effects(parsed_tool: dict, identifier: str, characters: Characters | None, record_shares: bool = True) -> None:
        """Handle action-specific side effects after parsing
        
        Args:
            parsed_tool: The parsed tool dict with 'identifier' and 'arguments'
            identifier: The action identifier
            characters: The Characters manager (may be None)
        """
        # ShareConversation: store pending share for end of conversation
        if identifier == 'mantella_npc_shareconversation' and characters:
            args = parsed_tool.get('arguments', {})
            if args.get('recipient_succeeded') and args.get('recipient_id'):
                sharer_name = args.get('source', '')
                recipient_name = args.get('recipient', '')
                recipient_id = args.get('recipient_id', '')
                was_added = characters.add_pending_share(sharer_name, recipient_name, recipient_id) if record_shares                     else not characters.is_share_pending(recipient_id)
                if was_added:
                    args['debug_message'] = f"{sharer_name} will share this conversation with {recipient_name}."
                else:
                    args['debug_message'] = f"This conversation will already be shared with {recipient_name}."
            else:
                recipient_name = args.get('recipient', 'recipient')
                args['debug_message'] = f"Could not find '{recipient_name}' to share conversation with."


    @staticmethod
    def _validate_arguments_against_schema(llm_arguments: dict, defined_params: dict, action_identifier: str) -> dict:
        """Validate that LLM-provided arguments match the action's parameter schema
        
        Args:
            llm_arguments: Arguments provided by the LLM
            defined_params: Parameter schema from the action JSON config
            action_identifier: Identifier of the action (for logger)
            
        Returns:
            Dictionary containing only valid arguments that match the schema
        """
        validated_args = {}
        
        # Check each LLM-provided argument against the schema
        for arg_name, arg_value in llm_arguments.items():
            if arg_name in defined_params:
                # Argument is valid according to schema
                validated_args[arg_name] = arg_value
            else:
                # LLM hallucinated a parameter that doesn't exist in the schema
                logger.warning(
                    f"LLM provided unknown argument '{arg_name}' for action '{action_identifier}'. "
                    f"Valid parameters are: {list(defined_params.keys())}. Ignoring this argument."
                )
        
        return validated_args


    @staticmethod
    def _in_conversation(names: list[str], characters: Characters) -> bool:
        """Every one of these people is in the conversation already"""
        present = {name.lower() for name in characters.get_all_names_w_nearby(include_player=True)}
        return bool(names) and all(str(name).lower() in present for name in names)

    @staticmethod
    def _log_skipped(identifier: str, param_name: str, names: list[str], characters: Characters, nearby_only: bool) -> None:
        """An action nobody it names can do. Asking for someone already in the conversation (to join it, or to come over) is no mistake:
        it's done already"""
        if nearby_only and FunctionManager._in_conversation(names, characters):
            logger.info(f"Action '{identifier}' not needed: {', '.join(str(n) for n in names)} {'is' if len(names) == 1 else 'are'} already in the conversation")
            return
        logger.warning(f"Skipping action '{identifier}' - no valid entities for parameter '{param_name}'")

    @staticmethod
    def _validate_npc_names(npc_names: list[str], characters: Characters, exclude_player: bool = True, include_nearby: bool = False, nearby_only: bool = False) -> list[str]:
        """Validate that NPC names exist based on scope
        
        Args:
            npc_names: List of NPC names to validate (from LLM)
            characters: Characters manager containing character and nearby NPC information
            exclude_player: If True, filter out player character (default: True)
            include_nearby: If True, validate against conversation + nearby NPCs (default: False)
            nearby_only: If True, only allow nearby NPC names (default: False)
            
        Returns:
            List of valid, unique NPC names
        """
        if not npc_names:
            return []
        
        # Get the actual player name for "player" alias resolution
        actual_player_name = characters.get_player_name()
        
        # Get all valid names based on scope
        valid_name_list = characters.get_all_names_w_nearby(
            include_player = not exclude_player,
            include_nearby = include_nearby,
            nearby_only = nearby_only
        )
        
        # Build case-insensitive lookup: lowercase -> actual name
        char_names_lower = {name.lower(): name for name in valid_name_list}
        
        valid_names = []
        seen_lower = set()
        
        for llm_name in npc_names:
            llm_lower = llm_name.lower()
            
            if llm_lower in seen_lower:
                continue
            
            # Handle "player" alias - convert to actual player name
            if llm_lower == "player":
                if actual_player_name and not exclude_player:
                    # Use the actual player name
                    valid_names.append(actual_player_name)
                    seen_lower.add(actual_player_name.lower())
                continue
            
            if llm_lower in char_names_lower:
                # Preserve LLM casing
                valid_names.append(llm_name)
                seen_lower.add(llm_lower)
            elif nearby_only and FunctionManager._in_conversation([llm_name], characters):
                continue # Already in the conversation: nothing to do for them. Said once in _log_skipped when it's everyone the action names
            else:
                if nearby_only:
                    available = "nearby"
                else:
                    available = "in conversation" if not include_nearby else "in conversation + nearby"
                logger.warning(f"NPC name '{llm_name}' not found {available}. Available NPCs: {list(char_names_lower.values())}")
        
        return valid_names


    @staticmethod
    def _populate_dynamic_enums(parameters: dict, game: Gameable, action_identifier: str) -> bool:
        """Populate dynamic enum values and clean internal fields from parameter schema
        
        Returns True when the tool should remain available,
        False when an enum_source has no available values (skip the action)
        
        Args:
            parameters: The 'parameters' dict from the tool schema
            game: The Gameable instance for game-specific lookups
            action_identifier: Action name for logger purposes
        """
        if 'properties' not in parameters:
            return True
        
        for param_name, param_def in parameters['properties'].items():
            enum_source = param_def.get('enum_source')
            if enum_source:
                enum_values = FunctionManager._get_enum_values_for_source(enum_source, game)
                if enum_values:
                    param_def['enum'] = enum_values
                else:
                    logger.warning(f"Skipping action '{action_identifier}' - no enum values for source '{enum_source}'")
                    return False
                # Remove enum_source from the schema as the LLM doesn't need to see it
                del param_def['enum_source']
            
            # Remove resolve_to_id from schema as the LLM doesn't need to see it
            if 'resolve_to_id' in param_def:
                del param_def['resolve_to_id']
        
        return True


    @staticmethod
    def _add_npc_context_to_parameters(parameters: dict, context) -> bool:
        """Add available NPC context to parameters based on their scope
        
        This function enhances parameter descriptions with lists of available entities
        based on the 'scope' property defined in the action JSON.
        Returns True when the tool should remain available, 
        ie it has at least one scoped parameter with entities available (or has no scoped parameters at all)
        
        Supported scopes for NPCs:
        - 'conversation': NPCs in conversation (excludes player)
        - 'conversation_w_player': Everyone in conversation (includes player)
        - 'nearby': Nearby NPCs not in conversation (excludes player)
        - 'nearby_w_player': Nearby NPCs not in conversation (includes player)
        - 'all_npcs': Conversation NPCs + nearby NPCs (excludes player)
        - 'all_npcs_w_player': Everyone (conversation + nearby, includes player)
        """
        if 'properties' not in parameters:
            return True

        # Process each parameter that has a scope defined
        scoped_params = 0
        scoped_with_entities = 0
        has_unscoped_params = False
        for param_name, param_def in parameters['properties'].items():
            scope = param_def.get('scope')
            
            # Skip parameters without scope (not entity references)
            if not scope:
                has_unscoped_params = True
                continue
            scoped_params += 1
            
            current_desc = param_def.get('description', '')
            
            # Parse scope to get entity list
            entity_list = FunctionManager._get_entities_for_scope(scope, context)
            
            # Append entity list to parameter description for LLM context
            if entity_list:
                param_def['description'] = f"{current_desc} Available: {entity_list}".strip()
                scoped_with_entities += 1
            else:
                param_def['description'] = f"{current_desc} No entities available."
        
        if scoped_params == 0:
            return True
        if scoped_with_entities > 0:
            return True
        return has_unscoped_params
    
    
    @staticmethod
    def _get_entities_for_scope(scope: str, context) -> str:
        """Get list of available entities based on scope
        
        Args:
            scope: Scope identifier (eg 'conversation', 'nearby', 'all_npcs_w_player')
            context: Conversation context
            
        Returns:
            Comma-separated list of entity names, or empty string if scope unknown
        """
        # Determine if player should be included based on '_w_player' suffix
        include_player = scope.endswith('_w_player')
        
        # NPC scopes
        if scope.startswith('conversation'):
            return context.get_character_names_as_text(include_player=include_player, include_nearby=False)
        elif scope.startswith('nearby'):
            return context.get_character_names_as_text(include_player=include_player, nearby_only=True)
        elif scope.startswith('all_npcs'):
            return context.get_character_names_as_text(include_player=include_player, include_nearby=True)
        
        else:
            logger.warning(f"Unknown scope '{scope}'. No entities will be added to parameter description.")
            return ""


    @staticmethod
    def _get_enum_values_for_source(enum_source: str, game: Gameable) -> list[str]:
        """Get enum values from a dynamic source
        
        Args:
            enum_source: Source identifier (eg 'idles')
            game: The Gameable instance for game-specific lookups
            
        Returns:
            List of enum values, or empty list if source unknown or unavailable
        """
        if not game:
            logger.warning(f"No game instance available for enum source lookup '{enum_source}'")
            return []
        elif enum_source == 'idles':
            # Get enabled idle names from the game's idle table
            if hasattr(game, 'get_enabled_idle_names'):
                return game.get_enabled_idle_names()
            else:
                logger.warning(f"Game does not support idle enum source")
                return []
        else:
            logger.warning(f"Unknown enum_source '{enum_source}'")
            return []


    @staticmethod
    def _resolve_parameter_to_id(value: str, resolve_type: str, game: Gameable) -> str | None:
        """Resolve a parameter value to its in-game ID
        
        Args:
            value: The parameter value to resolve (eg idle name or NPC name)
            resolve_type: Type of resolution ('idle' or 'npc')
            game: The Gameable instance for game-specific lookups
            
        Returns:
            The resolved ID string, or None if resolution failed
        """
        resolved_id = None
        if resolve_type == 'idle':
            if hasattr(game, 'resolve_idle_id'):
                resolved_id =  game.resolve_idle_id(value)
        elif resolve_type == 'npc':
            resolved_id = game.resolve_npc_refid_by_name(value)
        return resolved_id
