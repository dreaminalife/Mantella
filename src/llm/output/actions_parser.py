from src.llm.output.output_parser import output_parser, sentence_generation_settings
from src.llm.sentence_content import SentenceContent, has_action
from src.conversation.action import Action
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.ACTIONS)

LOOK = "mantella_npc_vision"


def _drop_keyword(content: SentenceContent, keyword: str) -> None:
    """The keyword goes from the line a voice service with sounds and directions gets, as it went from the text"""
    if content.voice_text is not None:
        content.voice_text = " ".join(content.voice_text.replace(keyword, " ").split())
    
class actions_parser(output_parser):
    def __init__(self, actions: list[Action], other_actions: list[Action] | None = None) -> None:
        """
        Args:
            actions (list[Action]): the actions the NPC can use here
            other_actions (list[Action] | None): every other action. One of these at the start of a line is taken out of it, so the NPC
                doesn't say its name, but nothing happens: it is switched off for this kind of conversation, or the NPC can't use it here
        """
        super().__init__()
        self.__actions = actions
        allowed = {a.identifier for a in actions}
        self.__other_actions = [a for a in other_actions or [] if a.identifier not in allowed]

    def cut_sentence(self, output: str, current_settings: sentence_generation_settings) -> tuple[SentenceContent|None, str|None]:
        return None, output

    def modify_sentence_content(self, cut_content: SentenceContent, last_content: SentenceContent | None, settings: sentence_generation_settings) -> tuple[SentenceContent | None, SentenceContent | None]:
        if ":" in cut_content.text:
            for action in self.__actions:
                keyword = action.keyword + ":"
                if action.identifier == LOOK:
                    if cut_content.text.lstrip().startswith(keyword) and settings.looked:
                        # Already looking: models often start with "Look:" again. It is taken out, and the line is said
                        cut_content.text = cut_content.text.lstrip()[len(keyword):].strip()
                        _drop_keyword(cut_content, keyword)
                        if not cut_content.text:
                            return None, last_content
                        continue
                    if cut_content.text.lstrip().startswith(keyword):
                        # The NPC wants to see first: the reply is asked for again with a screenshot, and this line is never said
                        log.info(f"{cut_content.speaker.name} looks around")
                        settings.vision_requested = True
                        settings.stop_generation = True
                        return None, last_content
                    continue
                if keyword in cut_content.text:
                    cut_content.text = cut_content.text.replace(keyword,"").strip()
                    _drop_keyword(cut_content, keyword)
                    if not has_action(cut_content.actions, action.identifier): # Named twice, done once
                        cut_content.actions.append({'identifier': action.identifier})
                    log.info(f"Action: {action.name} ({cut_content.speaker.name})")
                    # TODO: Add back this functionality while keeping the first delivered sentence intact
                    # ie "Inventory: Here you go." instead of just "Inventory:" with no further response
                    # if action.is_interrupting:
                    #     settings.stop_generation = True
            for action in self.__other_actions:
                keyword = action.keyword + ":"
                if cut_content.text.lstrip().startswith(keyword):
                    cut_content.text = cut_content.text.lstrip()[len(keyword):].strip()
                    _drop_keyword(cut_content, keyword)
                    log.info(f"{cut_content.speaker.name} used {action.name}, which NPCs can't use in this conversation. Nothing happens")
        return cut_content, last_content
    
    def get_cut_indicators(self) -> list[str]:
        return [":"]
