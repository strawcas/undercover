"""Isolated evaluation starts shared by backend grading scenarios."""

from backend import evaluations
from backend import runs as run_records
from backend.database import engine, insert_once, rounds
from backend.models import RunRequest
from backend.tests.run_requests import configuration, fixture_run


def evaluation(client, set_name="evaluation", tiny=True, round_id=None):
    if round_id is not None:
        # Exercise score eligibility independently of round admission.
        with engine().begin() as db:
            insert_once(
                db, rounds, {"round_id": round_id, "status": "finished", "record": {}}
            )
            request = RunRequest(
                **configuration(
                    kind="evaluation", set_name=set_name, tiny=tiny, round_id=round_id
                )
            )
            run = run_records.start_run(db, request)
            evaluations.create_copies(db, run.run_id, request)
            return run_records.get_run(db, run.run_id).model_dump()
    return fixture_run(configuration(kind="evaluation", set_name=set_name, tiny=tiny))
