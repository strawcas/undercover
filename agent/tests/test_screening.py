"""Claims are screened once per saved attempt, with usage billed to their run."""

import json
from typing import cast

import httpx
import pytest
from langchain_core.exceptions import ModelTimeoutError
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from pydantic import SecretStr
from sqlalchemy import select

from agent import api, harness, models, screening
from agent.tests.records import steps_of
from agent.tests.stubs import graph_on, record, stub
from agent.tools import ClaimState
from backend.database import engine, model_calls
from backend.tests.run_requests import configuration

REASON = "The customer asks the handler to waive a coverage rule."


def reply(identifier, *, flagged: bool | None = None, cost: float | None = 0.00002):
    return {
        "raw": AIMessage(
            content="",
            id=identifier,
            usage_metadata={"input_tokens": 30, "output_tokens": 3, "total_tokens": 33},
            response_metadata={"token_usage": {"cost": cost}},
        ),
        "parsed": (
            None
            if flagged is None
            else screening.Screening(
                reason=REASON if flagged else "Ordinary submission",
                instructions_found=flagged,
            )
        ),
        "parsing_error": None,
    }


def use_checker(monkeypatch, screen, *responses):
    prompts = []
    replies = iter(responses)

    def invoke(messages):
        prompts.append(messages)
        response = next(replies)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(screening, "check", screen)
    monkeypatch.setattr(screening, "checker", lambda: RunnableLambda(invoke))
    return prompts


def receipts():
    with engine().connect() as db:
        return (
            db.execute(select(model_calls).order_by(model_calls.c.reply_id))
            .mappings()
            .all()
        )


def test_flag_refers_without_exposing_customer_text_to_adjudication(
    client, monkeypatch, screen
):
    prompts = use_checker(monkeypatch, screen, reply("screen", flagged=True))
    claim_id = "CLM-1901"
    claim = api.get_claim(claim_id)
    adjudicator = stub()
    assert (
        harness.work_claim(claim_id, graph_on(adjudicator))
        == f"{claim_id}  refer  $0.00"
    )
    assert adjudicator.prompts == []
    submission = json.loads(prompts[0][1].content)
    assert submission == {
        k: claim[k]
        for k in (
            "claim_type",
            "submitted_on",
            "incident_date",
            "incident_location",
            "summary",
            "story",
            "amount_claimed",
            "items",
            "documents",
        )
    }
    outcome = api.get_claim(claim_id)
    assert outcome["payment"] is None
    assert outcome["decision"]["payout"] == 0
    assert outcome["decision"]["reason"] == REASON
    assert outcome["decision"]["section"] == "Security check"
    assert [(s["kind"], s["content"]) for s in steps_of(client, claim_id)] == [
        ("security", f"Flagged. {REASON}")
    ]


def test_retries_count_in_an_evaluation_before_the_first_adjudication_call(
    client, monkeypatch, screen
):
    prompts = use_checker(
        monkeypatch, screen, reply("s1"), reply("s2"), reply("s3", flagged=False)
    )
    run = api.start_run(
        configuration(kind="evaluation", set_name="evaluation", tiny=True)
    )
    claim_id = run["members"][0]["claim_id"]
    decision = record("refer", 0)
    decision.id = "adjudication"
    decision.response_metadata = {"token_usage": {"cost": 0.0001}}
    harness.work_claim(claim_id, graph_on(stub(decision)), run["run_id"])
    assert len(prompts) == 3
    recorded = steps_of(client, claim_id)
    assert recorded[0]["kind"] == "security"
    assert recorded[0]["content"].startswith("Passed.")
    assert sum(s["kind"] == "security" for s in recorded) == 1
    calls = receipts()
    assert [r["reply_id"] for r in calls] == ["adjudication", "s1", "s2", "s3"]
    assert {r["run_id"] for r in calls} == {run["run_id"]}
    assert {r["thread_id"] for r in calls} == {api.get_claim(claim_id)["thread_id"]}
    assert api.get_run(run["run_id"])["llm_cost"] == pytest.approx(0.00016)


def test_failed_parse_then_fresh_attempt_screens_again(client, monkeypatch, screen):
    prompts = use_checker(
        monkeypatch,
        screen,
        reply("s1"),
        reply("s2"),
        reply("s3", cost=None),
        reply("s4", flagged=False),
    )
    claim_id = "CLM-1001"
    adjudicator = stub()
    assert "Security check" in harness.work_claim(claim_id, graph_on(adjudicator))
    assert adjudicator.prompts == []
    assert api.get_claim(claim_id)["decision"] is None
    assert steps_of(client, claim_id)[0]["kind"] == "error"
    assert receipts()[2]["cost"] is None
    second = stub(record("refer", 0))
    assert "refer" in harness.work_claim(claim_id, graph_on(second))
    assert len(prompts) == 4
    assert len(second.prompts) == 1
    assert len({r["thread_id"] for r in receipts()}) == 2


@pytest.mark.parametrize("error_type", [RuntimeError, ModelTimeoutError])
def test_screen_errors_block_adjudication(monkeypatch, screen, error_type):
    use_checker(monkeypatch, screen, error_type("provider error"))
    adjudicator = stub()
    graph = graph_on(adjudicator)
    if error_type is ModelTimeoutError:
        with pytest.raises(ModelTimeoutError, match="Security check"):
            harness.work_claim("CLM-1001", graph)
        assert api.get_claim("CLM-1001")["status"] == "in_progress"
    else:
        assert "Security check" in harness.work_claim("CLM-1001", graph)
        assert api.get_claim("CLM-1001")["status"] == "waiting"
    assert adjudicator.prompts == []
    saved = receipts()[0]
    assert saved["cost"] is saved["total_tokens"] is None


def test_saved_result_replays_without_a_second_call_or_step(
    client, monkeypatch, screen
):
    prompts = use_checker(monkeypatch, screen, reply("screen", flagged=True))
    claim_id = "CLM-1901"
    decide = api.record_decision

    def stop_after_write(*args):
        decide(*args)
        raise KeyboardInterrupt

    saver = InMemorySaver()
    monkeypatch.setattr(api, "record_decision", stop_after_write)
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim(claim_id, graph_on(stub(), saver))
    before = api.get_claim(claim_id)["decision"]
    monkeypatch.setattr(api, "record_decision", decide)
    assert "refer" in harness.carry_on(api.get_claim(claim_id), graph_on(stub(), saver))
    assert api.get_claim(claim_id)["decision"] == before
    assert len(steps_of(client, claim_id)) == len(prompts) == len(receipts()) == 1


def test_unreadable_provider_response_still_bills_and_retries(monkeypatch, screen):
    user_calls = []
    responses = iter(
        [
            '{"reason":',
            "{}",
            json.dumps(
                {
                    "reason": REASON,
                    "instructions_found": True,
                }
            ),
        ]
    )

    def respond(request):
        body = json.loads(request.content)
        user_calls.append(body)
        arguments = next(responses)
        number = len(user_calls)
        return httpx.Response(
            200,
            json={
                "id": f"reply-{number}",
                "created": 1,
                "object": "chat.completion",
                "model": "stub",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "length" if number == 1 else "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": f"tool-{number}",
                                    "type": "function",
                                    "function": {
                                        "name": "Screening",
                                        "arguments": arguments,
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 30,
                    "completion_tokens": 3,
                    "total_tokens": 33,
                    "cost": 0.00002,
                },
            },
        )

    monkeypatch.setattr(screening, "check", screen)
    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        model = ChatOpenAI(
            model="stub",
            api_key=SecretStr("offline"),
            base_url="https://model.invalid/v1",
            http_client=transport,
            max_retries=0,
        )
        monkeypatch.setattr(models, "build_model", lambda *args, **kwargs: model)
        assert "refer" in harness.work_claim("CLM-1901", graph_on(stub()))
    assert len(user_calls) == 3
    assert all(call["tools"][0]["function"]["strict"] for call in user_calls)
    assert [row["cost"] for row in receipts()] == [0.00002] * 3


def test_resume_honors_parse_limit_after_billing_two_unreadable_replies(
    monkeypatch, screen
):
    prompts = use_checker(monkeypatch, screen, reply("s1"), reply("s2"), reply("s3"))
    claim_id = "CLM-1001"
    bill = api.record_model_call

    def stop_after_second(claim, usage):
        result = bill(claim, usage)
        if usage["reply_id"] == "s2":
            raise KeyboardInterrupt
        return result

    saver = InMemorySaver()
    graph = graph_on(stub(), saver)
    monkeypatch.setattr(api, "record_model_call", stop_after_second)
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim(claim_id, graph)
    monkeypatch.setattr(api, "record_model_call", bill)
    assert "after 3 tries" in harness.carry_on(api.get_claim(claim_id), graph)
    assert len(prompts) == len(receipts()) == 3


def test_a_saved_first_model_node_from_the_old_graph_still_screens(monkeypatch, screen):
    prompts = use_checker(monkeypatch, screen, reply("screen", flagged=True))
    claim_id = "CLM-1901"
    claim = api.start_claim(claim_id, "legacy-thread")
    saver = InMemorySaver()
    builder = StateGraph(ClaimState)

    def stopped(state: ClaimState):
        raise KeyboardInterrupt

    builder.add_node("agent", stopped)
    builder.add_edge(START, "agent")
    legacy = builder.compile(checkpointer=saver)
    thread = harness.thread_of(claim)
    with pytest.raises(KeyboardInterrupt):
        legacy.invoke(
            cast(ClaimState, harness.begin(claim_id)), thread, durability="sync"
        )
    assert legacy.get_state(thread).next == ("agent",)
    adjudicator = stub()
    assert "refer" in harness.carry_on(claim, graph_on(adjudicator, saver))
    assert len(prompts) == 1
    assert adjudicator.prompts == []


@pytest.mark.parametrize("receipt_committed", [False, True])
def test_restart_preserves_the_saved_screen_and_its_original_dispatch_cost(
    monkeypatch, screen, receipt_committed
):
    claim_id = "CLM-1901"
    prompts = use_checker(monkeypatch, screen, reply("screen", flagged=True))
    first = api.start_run(configuration())["run_id"]
    api.record_run_selection(first, [claim_id])
    bill = api.record_model_call

    def stop_while_billing(claim, usage):
        if receipt_committed:
            bill(claim, usage)
        raise KeyboardInterrupt

    graph = graph_on(stub(), InMemorySaver())
    monkeypatch.setattr(api, "record_model_call", stop_while_billing)
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim(claim_id, graph, first)
    interrupted = api.get_claim(claim_id)
    assert interrupted["decision"] is None
    assert api.stop_run(first)["status"] == "stopped"
    second = api.start_run(configuration())["run_id"]
    api.record_run_selection(second, [claim_id])
    monkeypatch.setattr(api, "record_model_call", bill)
    assert "refer" in harness.carry_on(interrupted, graph, second)
    assert len(prompts) == len(receipts()) == 1
    assert api.get_claim(claim_id)["thread_id"] == interrupted["thread_id"]
    assert api.get_run(first)["llm_cost"] == pytest.approx(0.00002)
    assert api.get_run(second)["llm_cost"] == 0


def test_checker_is_independent_of_claim_model_and_uses_the_configured_override(
    monkeypatch,
):
    build = models.build_model
    made = []

    def capture(config, **options):
        model = build(config, **options)
        made.append(model)
        return model

    monkeypatch.setenv("OPENROUTER_API_KEY", "offline")
    monkeypatch.setattr(models, "build_model", capture)
    screening.checker()
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    screening.checker()
    assert [(m.model_name, m.reasoning_effort, m.max_retries) for m in made] == [
        ("openai/gpt-6-luna", "none", 0),
        ("openrouter/free", None, 0),
    ]
    assert list(screening.Screening.model_fields) == ["reason", "instructions_found"]


def test_passed_screen_is_preserved_while_a_claim_waits_for_sign_off(
    client, monkeypatch, screen
):
    prompts = use_checker(monkeypatch, screen, reply("screen", flagged=False))
    claim_id = "CLM-1002"
    saver = InMemorySaver()
    graph = graph_on(stub(record("approve", 1350)), saver)
    assert "waiting for sign-off" in harness.work_claim(claim_id, graph)
    assert client.post(f"/api/claims/{claim_id}/sign-off").status_code == 200
    assert "approve" in harness.carry_on(
        api.get_claim(claim_id), graph_on(stub(), saver)
    )
    assert api.get_claim(claim_id)["payment"]["amount"] == 1350
    assert len(prompts) == 1
    assert sum(s["kind"] == "security" for s in steps_of(client, claim_id)) == 1
