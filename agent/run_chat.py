"""Runnable entrypoint for the Refill ADK agent.

`agent/refill_agent.py` builds the LlmAgent; this module is what actually
RUNS it against the live Gemini API, so the "the dialogue lives in an ADK
agent" claim in SUBMISSION.md has an executable path behind it rather than
only a constructor exercised by tests.

    python -m agent.run_chat "my mom's refill was denied, plan is standard"

Requires GOOGLE_API_KEY (or GEMINI_API_KEY). With no key set, google-genai
raises with a message naming the key; this module does not degrade to a
canned conversation.

The calculator veto is not bypassed by running the agent live: the model
reaches a date only by calling `propose_next_eligible_date`, and that tool
runs `validator.eligibility.compute_next_eligible` itself. Every call lands
in `proposal_log` with the model's claim and the calculator's answer side
by side, printed at exit.

Sessions are deliberately short (one chase, then exit) for the ADK
100-event cap and the `_init_session` replay issue -- see LIMITATIONS.md.
"""

from __future__ import annotations

import asyncio
import os
import sys

from agent.refill_agent import ProposalRecord, build_refill_agent

API_KEY_VARS = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
_VERTEX_ENV_VARS = ("GOOGLE_GENAI_USE_ENTERPRISE", "GOOGLE_GENAI_USE_VERTEXAI")

APP_NAME = "refill"


def _vertex_mode_requested() -> bool:
    """True if the environment asks for Vertex AI mode, the same way
    `google.genai`'s own client resolves it (matches Sovereign's
    `agent/tools.py:_vertex_mode_requested`, same bug class)."""
    for var in _VERTEX_ENV_VARS:
        value = os.environ.get(var)
        if value is not None:
            return value.strip().lower() in ("true", "1")
    return False


def require_api_key() -> None:
    """Fail loud, for whichever auth mode is actually selected.

    Vertex mode (`GOOGLE_GENAI_USE_VERTEXAI=TRUE`) authenticates via the
    Cloud Run Job's own service account through Application Default
    Credentials, not an API key. Requiring `GOOGLE_API_KEY`/`GEMINI_API_KEY`
    unconditionally made the real deploy path -- no key anywhere -- fail
    even when it would otherwise work. `google.adk`'s `LlmAgent` already
    resolves Vertex mode from these same env vars on its own; this check
    only needs to confirm a project is configured before proceeding.
    """
    if _vertex_mode_requested():
        if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
            raise SystemExit(
                "GOOGLE_GENAI_USE_VERTEXAI is set but GOOGLE_CLOUD_PROJECT "
                "is not: set GOOGLE_CLOUD_PROJECT (and normally "
                "GOOGLE_CLOUD_LOCATION) before running the Refill agent in "
                "Vertex AI mode. Authentication itself comes from "
                "Application Default Credentials -- the Cloud Run Job's "
                "service account in production, or `gcloud auth "
                "application-default login` locally -- not from an API key."
            )
        return
    if not any(os.environ.get(v) for v in API_KEY_VARS):
        raise SystemExit(
            "GOOGLE_API_KEY (or GEMINI_API_KEY) is not set. The Refill agent "
            "talks to the live Gemini API and will not fabricate a "
            "conversation without one. For a model-free walkthrough of the "
            "validator and the artifact, run: python demo_local.py"
        )


async def _ask(runner, session_id: str, user_id: str, text: str) -> str:
    from google.genai import types

    message = types.Content(role="user", parts=[types.Part(text=text)])
    final_text = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=message
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(p.text or "" for p in event.content.parts)
    return final_text


async def run_single_turn(
    prompt: str, user_id: str, proposal_log: list[ProposalRecord]
) -> str:
    """Run exactly one turn of the Refill ADK agent against the live
    Gemini API and return its final text reply.

    This is what `job/main.py`'s chase path calls so the model that
    proposes a date is the SAME `LlmAgent` + `InMemoryRunner` wiring this
    module's interactive CLI already exercises manually -- one runner
    implementation, two callers, not a parallel reimplementation on the
    deployed path. `proposal_log` is populated by the
    `propose_next_eligible_date` tool exactly as it is in `_chat` below.

    Does NOT call `require_api_key()` itself; callers are expected to
    call it first (both `main()` below and `job/main.py` do), so a
    missing key fails before any session/runner is even constructed.
    """
    from google.adk.runners import InMemoryRunner

    agent = build_refill_agent(proposal_log)
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=user_id
    )
    return await _ask(runner, session.id, user_id, prompt)


async def _chat(opening: str, user_id: str, proposal_log: list[ProposalRecord]) -> None:
    from google.adk.runners import InMemoryRunner

    agent = build_refill_agent(proposal_log)
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=user_id
    )

    print(f"you> {opening}")
    reply = await _ask(runner, session.id, user_id, opening)
    print(f"agent> {reply}\n")

    # Interactive follow-up turns. EOF or an empty line ends the chase, so
    # the session stays short by construction.
    while True:
        try:
            nxt = input("you> ").strip()
        except EOFError:
            break
        if not nxt:
            break
        reply = await _ask(runner, session.id, user_id, nxt)
        print(f"agent> {reply}\n")


def main(argv: list[str]) -> int:
    require_api_key()
    opening = " ".join(argv[1:]).strip()
    if not opening:
        raise SystemExit(
            'usage: python -m agent.run_chat "describe the refill denial"'
        )

    user_id = os.environ.get("REFILL_USER_ID", "demo-caregiver")
    proposal_log: list[ProposalRecord] = []
    asyncio.run(_chat(opening, user_id, proposal_log))

    # The run record the spec asks for: the model's claim and the
    # calculator's answer, side by side, for every proposal made.
    print("\n--- calculator adjudication log ---")
    if not proposal_log:
        print("(the agent never proposed a date, so the calculator was never "
              "asked to adjudicate one)")
    for record in proposal_log:
        print(
            f"  model_claimed={record.model_claimed_date} "
            f"calculator={record.calculator_date} agreed={record.agreed} "
            f"(last_fill={record.last_fill_date} days_supply={record.days_supply} "
            f"plan={record.plan})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
