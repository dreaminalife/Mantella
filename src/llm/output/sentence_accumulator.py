import re

from src.llm.output.clean_sentence_parser import clean_sentence_parser


class sentence_accumulator:
    """Accumulates the token-wise output of an LLM into raw sentences.
    """
    def __init__(self, cut_indicators: list[str]) -> None:
        self.__cut_indicators = cut_indicators
        self.__cleaned_llm_output: str = ""
        # Updated regex: only match a period if it is not part of an ellipsis (three or more periods)
        period = '\.'
        other_chars = [c for c in cut_indicators if c != '.']
        other_chars_escaped = ''.join([re.escape(c) for c in other_chars])
        base_regex_def = rf"^.*?(?:(?<!\.)\.(?!\.)|[{other_chars_escaped}])+"
        self.__sentence_end_reg = re.compile(base_regex_def)
        self.__unparseable: str = ""
        self.__prepared_match: str = ""
        self.__cleaner = clean_sentence_parser()
    
    def has_next_sentence(self) -> bool:
        if len(self.__prepared_match) > 0:
            return True
        
        match = self.__sentence_end_reg.match(self.__cleaned_llm_output)
        if not match:
            return False
        else:
            self.__prepared_match = match.group()
            self.__cleaned_llm_output = self.__cleaned_llm_output.removeprefix(self.__prepared_match)
            return True
    
    def get_next_sentence(self) -> str:
        result = self.__unparseable + self.__prepared_match
        self.__unparseable = ""
        self.__prepared_match = ""
        return result
    
    def accumulate(self, llm_output: str):
        llm_output = self.__cleaner.clean_sentence(llm_output)
        self.__cleaned_llm_output += llm_output
    
    def refuse(self, refused_text: str):
        self.__unparseable = refused_text

    def flush_remaining(self) -> str:
        """Emits any accumulated text that has not yet formed a complete sentence.

        Called when the LLM stream has ended. Returns whatever is left in the
        accumulator (any refused text, prepared match, or trailing output that
        never reached a sentence-ending character) so the final partial sentence
        is not silently dropped. Returns an empty string if nothing remains.
        """
        remaining = self.__unparseable + self.__prepared_match + self.__cleaned_llm_output
        self.__unparseable = ""
        self.__prepared_match = ""
        self.__cleaned_llm_output = ""
        return remaining
    


    
