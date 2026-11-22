import pytest

from src.llm.decision_service_resolver import DECISIONS_URLS, TYPED_ADDRESS_PREFIXES, KnownOrTypedDecisionServiceResolver, decision_service_resolver


@pytest.mark.parametrize("service, url", [
    ("OpenRouter", "https://openrouter.ai/api/alpha/decisions"),
    ("NanoGPT", "https://nano-gpt.com/api/v1/decisions"),
])
def test_known_service_gives_its_endpoint(service, url):
    assert decision_service_resolver.get_decisions_url(service) == url


@pytest.mark.parametrize("typed, url", [
    ("http://localhost:8009/v1/systemone", "http://localhost:8009/v1/systemone"),
    ("  http://127.0.0.1:8000/v1/systemone  ", "http://127.0.0.1:8000/v1/systemone"), # Spaces around it are dropped, nothing is added
    ("HTTPS://my-server.example/decisions", "HTTPS://my-server.example/decisions"),
])
def test_typed_address_is_used_as_typed(typed, url):
    assert decision_service_resolver.get_decisions_url(typed) == url


@pytest.mark.parametrize("service", ["OpenAI", "KoboldCpp", "localhost:8009/v1/systemone", ""])
def test_anything_else_is_not_usable(service):
    assert decision_service_resolver.get_decisions_url(service) is None


def test_known_services_come_from_the_given_list():
    resolver = KnownOrTypedDecisionServiceResolver({"Other": "https://other.example/decisions"}, TYPED_ADDRESS_PREFIXES)
    assert resolver.get_decisions_url("Other") == "https://other.example/decisions"
    assert resolver.get_decisions_url("OpenRouter") is None
    assert "OpenRouter" in DECISIONS_URLS
