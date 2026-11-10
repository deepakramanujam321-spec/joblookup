"""If one AI provider's key expires or its credits run out, the next
configured provider takes over -- scoring and drafts must not just stop."""

from types import SimpleNamespace

import litellm
import pytest

from jobseeker import llm

TOOL = {"type": "function", "function": {"name": "f", "description": "d", "parameters": {"type": "object", "properties": {}}}}
EXPIRED = "litellm.BadRequestError: OpenAIException - Your API key has expired. Create a new API key to continue."


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("LLM_MODEL", "OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    llm._cooling_down.clear()
    llm._announced.clear()
    yield
    llm._cooling_down.clear()
    llm._announced.clear()


def fake_completion(calls, broken=()):
    """litellm.completion stand-in: models in `broken` raise, others answer."""
    def completion(model, **kw):
        calls.append(model)
        if model in broken:
            raise broken[model]
        args = SimpleNamespace(arguments='{"answer": "' + model + '"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[SimpleNamespace(function=args)]))])
    return completion


def test_every_configured_provider_is_a_candidate_cheapest_first(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    assert llm.configured_models() == ["openai/gpt-4o-mini", "gemini/gemini-flash-latest"]
    assert llm.resolve_model() == "openai/gpt-4o-mini"
    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-2.5-pro")  # explicit goes first; same provider isn't listed twice
    assert llm.configured_models() == ["gemini/gemini-2.5-pro", "openai/gpt-4o-mini"]
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.delenv("LLM_MODEL")
    assert not llm.is_configured()
    with pytest.raises(llm.LLMUnavailable):
        llm.resolve_model()


def test_expired_key_falls_back_to_the_next_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    calls = []
    monkeypatch.setattr(litellm, "completion", fake_completion(calls, {"openai/gpt-4o-mini": litellm.BadRequestError(EXPIRED, "openai", "gpt-4o-mini")}))
    args, used = llm.call_tool("p", TOOL)
    assert used == "gemini/gemini-flash-latest" and args == {"answer": used}
    assert calls == ["openai/gpt-4o-mini", "gemini/gemini-flash-latest"]


def test_a_dead_provider_is_skipped_until_its_cooldown_ends(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    calls = []
    monkeypatch.setattr(litellm, "completion", fake_completion(calls, {"openai/gpt-4o-mini": litellm.BadRequestError(EXPIRED, "openai", "gpt-4o-mini")}))
    llm.call_tool("p", TOOL)
    calls.clear()
    llm.call_tool("p", TOOL)
    assert calls == ["gemini/gemini-flash-latest"]  # no wasted call to the dead key
    for model in llm._cooling_down:
        llm._cooling_down[model] = 0  # cooldown over: the key may have been replaced
    calls.clear()
    llm.call_tool("p", TOOL)
    assert calls[0] == "openai/gpt-4o-mini"


def test_transient_errors_fall_back_without_benching_the_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    calls = []
    monkeypatch.setattr(litellm, "completion", fake_completion(calls, {"openai/gpt-4o-mini": litellm.Timeout("slow", "gpt-4o-mini", "openai")}))
    assert llm.call_tool("p", TOOL)[1] == "gemini/gemini-flash-latest"
    assert llm._cooling_down == {}
    calls.clear()
    llm.call_tool("p", TOOL)
    assert calls[0] == "openai/gpt-4o-mini"  # still tried first next time


def test_all_providers_failing_reports_each_reason(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    calls = []
    broken = {"openai/gpt-4o-mini": litellm.BadRequestError(EXPIRED, "openai", "gpt-4o-mini"),
              "gemini/gemini-flash-latest": litellm.RateLimitError("quota exceeded", "gemini", "flash")}
    monkeypatch.setattr(litellm, "completion", fake_completion(calls, broken))
    with pytest.raises(llm.LLMError) as e:
        llm.call_tool("p", TOOL)
    assert "openai/gpt-4o-mini" in str(e.value) and "gemini/gemini-flash-latest" in str(e.value) and "expired" in str(e.value)


def test_preferred_model_still_leads_and_single_provider_still_works(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    calls = []
    monkeypatch.setattr(litellm, "completion", fake_completion(calls))
    assert llm.call_tool("p", TOOL, model="gemini/gemini-2.5-pro")[1] == "gemini/gemini-2.5-pro"
    assert calls == ["gemini/gemini-2.5-pro"]
