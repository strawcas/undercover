"""The console, where the agent's entry points print the harness's report lines."""

import logging
import sys


def show_reports() -> None:
    """Print the harness's report lines as it works."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout)
    logging.getLogger("agent").setLevel(logging.INFO)
