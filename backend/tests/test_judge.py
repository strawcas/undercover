"""Exercise the actual SDK adapter on a local HTTP transport."""

import json

import httpx
import pytest
from openrouter import OpenRouter

from backend import judge


@pytest.fixture(autouse=True)
def stub_judge(monkeypatch):
    """Override the shared judge fixture with an intercepted SDK transport."""
    requests = []
    response = {
        "model": judge.JEV,
        "usage": {"input_tokens": 10, "output_tokens": 2, "cost": 0.0002},
        "answers": {
            "grade": {
                "type": "score",
                "score": 1.6,
                "probabilities": {"0": 0.4, "1": 0.05, "2": 0.1, "3": 0.45},
            },
            "same_grounds": {"type": "noul", "noul": 0.51},
            "same_section": {"type": "noul", "noul": 0.49},
        },
    }
    options = {"status": 200, "timeout": False}

    def answer(request):
        requests.append(request)
        if options["timeout"]:
            raise httpx.ReadTimeout("offline timeout", request=request)
        return httpx.Response(options["status"], json=response)

    transport = httpx.Client(transport=httpx.MockTransport(answer))
    monkeypatch.setattr(
        judge,
        "client",
        OpenRouter(
            api_key="offline", client=transport, server_url="https://unused.invalid"
        ),
    )
    monkeypatch.setattr(judge, "ORIGIN", "https://judge.invalid")
    yield requests, response, options
    transport.close()


def test_lab_questions_origin_timeout_and_most_likely_letter(stub_judge):
    requests, response, options = stub_judge
    state = {
        "handler": {
            "decision": "approve",
            "reason": "Covered",
            "reference": "Section 3",
        },
        "agent": {
            "decision": "approve",
            "rationale": "Covered",
            "section": "Section 3",
        },
    }
    result = judge.grade(state)
    assert (result.grade, result.letter) == (1.6, "A")
    assert (result.same_grounds, result.same_section, result.judge_cost) == (
        0.51,
        0.49,
        0.0002,
    )
    [request] = requests
    assert str(request.url) == "https://judge.invalid/api/alpha/decisions"
    assert request.extensions["timeout"]["read"] == 10
    body = json.loads(request.content)
    assert body["state"] == state
    assert set(body["questions"]) == {"grade", "same_grounds", "same_section"}
    assert body["questions"]["grade"]["criteria"][0].startswith("F:")
    assert body["questions"]["grade"]["criteria"][-1].startswith("A:")


@pytest.mark.parametrize("failure", ["status", "timeout"])
def test_failed_judge_is_not_retried_or_falsely_graded(stub_judge, failure):
    requests, response, options = stub_judge
    options[failure] = 500 if failure == "status" else True
    result = judge.grade({})
    assert result.grade is None
    assert result.letter is None
    assert result.judge_cost is None
    assert len(requests) == 1


def test_missing_probabilities_are_ungraded_but_retain_reported_cost(stub_judge):
    requests, response, options = stub_judge
    del response["answers"]["grade"]["probabilities"]
    result = judge.grade({})
    assert result.grade is None
    assert result.judge_cost == 0.0002
