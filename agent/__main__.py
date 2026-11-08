"""Work one claim with the UnderCover Agent: uv run -m agent CLM-1001"""

import argparse
import sys

from dotenv import load_dotenv

from agent import api
from agent.console import show_reports
from agent.harness import checkpointer, claim_graph, model, work_claim


def main(args: list[str]) -> None:
    """Work one claim; dispatches start from the dashboard."""
    parser = argparse.ArgumentParser(
        prog="uv run -m agent", description="Work one claim."
    )
    parser.add_argument("claim_id", help="work this one claim")
    options = parser.parse_args(args)
    with checkpointer() as saver:
        graph = claim_graph(model(), saver)
        try:
            work_claim(options.claim_id, graph)
        except api.Refused as refusal:
            parser.exit(1, f"{options.claim_id}  not started: {refusal}\n")


if __name__ == "__main__":
    load_dotenv(override=True)
    show_reports()
    main(sys.argv[1:])
