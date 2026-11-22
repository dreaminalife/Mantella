"""
Finds where decision model requests go, from the Game Master Decision Service setting.
Like the LLM Service settings, it takes a known service name or an address the player types, for a decision model running on their own computer.
"""

from abc import ABC, abstractmethod

# The decision endpoints of the services that can be picked by name
DECISIONS_URLS = {
    "OpenRouter": "https://openrouter.ai/api/alpha/decisions",
    "NanoGPT": "https://nano-gpt.com/api/v1/decisions",
}
TYPED_ADDRESS_PREFIXES = ("http://", "https://")
UNUSABLE_SERVICE_MESSAGE = "Game Master Decision Service must be OpenRouter, NanoGPT or a full address starting with http:// or https://, not '{service}'."


class DecisionServiceResolver(ABC):
    """Turns the Game Master Decision Service setting into the address decision requests are sent to"""

    @abstractmethod
    def get_decisions_url(self, service: str) -> str | None:
        """The address decision requests are sent to. None if the setting is neither a known service nor an address"""
        pass


class KnownOrTypedDecisionServiceResolver(DecisionServiceResolver):
    """A known service name gives its endpoint. A typed address is used exactly as typed: decision servers have no shared path to add to it"""

    def __init__(self, known_urls: dict[str, str], address_prefixes: tuple[str, ...]):
        self.__known_urls = known_urls
        self.__address_prefixes = address_prefixes

    def get_decisions_url(self, service: str) -> str | None:
        if service in self.__known_urls:
            return self.__known_urls[service]
        address = service.strip()
        return address if address.lower().startswith(self.__address_prefixes) else None


decision_service_resolver: DecisionServiceResolver = KnownOrTypedDecisionServiceResolver(DECISIONS_URLS, TYPED_ADDRESS_PREFIXES)
