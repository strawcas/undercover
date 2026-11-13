"""Isolated evaluation starts shared by backend grading scenarios."""

from backend.tests.run_requests import configuration


def evaluation(client, set_name="evaluation", tiny=True, round_id=None):
    return client.post(
        "/api/runs",
        json=configuration(
            kind="evaluation", set_name=set_name, tiny=tiny, round_id=round_id
        ),
    ).json()
