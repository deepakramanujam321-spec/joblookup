"""Exercises the real LiteLLM call path (tools + forced tool choice) with a
mocked response -- no network, no key. Catches dependency breakage like the
litellm release that failed every call with "No module named 'mcp'", which
unit tests that mock llm.call_tool can't see."""

import litellm


def test_litellm_tool_call_path_works_offline():
    tool = {"type": "function", "function": {"name": "f", "description": "d",
            "parameters": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}}}
    resp = litellm.completion(
        model="openai/gpt-4o-mini", api_key="sk-test", mock_response="ok", tools=[tool],
        tool_choice={"type": "function", "function": {"name": "f"}},
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}],
    )
    assert resp.choices[0].message is not None
