"""Tests for agent/refill_agent.py -- exercises the ADK FunctionTool closure
directly (same approach as projects/sovereign/tests/test_fleet.py), which
is exactly the code path the LLM would invoke via tool-calling. No live
model call is made.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from agent.refill_agent import ProposalRecord, build_refill_agent


def _build():
    log: list[ProposalRecord] = []
    agent = build_refill_agent(log)
    return agent, log


def test_agent_is_llm_agent_with_gemini_flash():
    agent, _log = _build()
    assert isinstance(agent, LlmAgent)
    assert agent.model == "gemini-2.5-flash"
    assert len(agent.tools) == 1


def test_tool_agrees_when_model_claim_matches_calculator():
    agent, log = _build()
    tool = agent.tools[0]
    result = tool.func(
        last_fill_date="2026-01-01",
        days_supply=30,
        plan="standard",
        model_claimed_date="2026-01-29",
    )
    assert result.startswith("AGREE")
    assert "2026-01-29" in result
    assert len(log) == 1
    assert log[0].agreed is True


def test_tool_disagrees_and_relays_calculator_date():
    agent, log = _build()
    tool = agent.tools[0]
    result = tool.func(
        last_fill_date="2026-01-01",
        days_supply=30,
        plan="standard",
        model_claimed_date="2026-02-15",  # wrong guess
    )
    assert result.startswith("DISAGREE")
    assert "2026-01-29" in result  # calculator's correct date is relayed
    assert "authoritative" in result
    assert len(log) == 1
    assert log[0].agreed is False
    assert log[0].calculator_date == "2026-01-29"
    assert log[0].model_claimed_date == "2026-02-15"


def test_tool_errors_cleanly_on_bad_date_format():
    agent, log = _build()
    tool = agent.tools[0]
    result = tool.func(
        last_fill_date="not-a-date",
        days_supply=30,
        plan="standard",
        model_claimed_date="2026-01-29",
    )
    assert result.startswith("ERROR")
    assert len(log) == 0  # no proposal logged for a malformed call


def test_agent_instruction_forbids_arguing_past_disagreement():
    agent, _log = _build()
    assert "MUST NOT argue" in agent.instruction or "authoritative" in agent.instruction


def test_run_chat_refuses_to_run_without_an_api_key(monkeypatch):
    """The live-agent entrypoint must stop loudly rather than fabricate a
    conversation. A silent fallback here would mean the 'multi-turn ADK
    dialogue' claim could be demonstrated with no model involved at all.
    """
    import pytest

    from agent import run_chat

    for var in run_chat.API_KEY_VARS:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(SystemExit) as excinfo:
        run_chat.require_api_key()
    assert "GOOGLE_API_KEY" in str(excinfo.value)


def test_run_chat_proceeds_past_the_key_check_when_a_key_is_set(monkeypatch):
    """Guard against the check being unconditional (which would make the
    test above pass for the wrong reason)."""
    from agent import run_chat

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used-for-a-call")
    run_chat.require_api_key()  # must not raise
