"""Import the platform team's staged Runtime layout in an isolated environment."""

import shutil
import sys
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from starlette.testclient import TestClient


async def stand_in_checkpoint_pool(_managed: bool) -> None:
    """Exercise the staged protocol without connecting to a database."""


def verify() -> None:
    """Stage only the directories AgentCore packages and import its entrypoint."""
    root = Path(__file__).resolve().parents[2]
    ignored = shutil.ignore_patterns("tests", "__pycache__")
    with TemporaryDirectory(prefix="undercover-runtime-") as stage:
        target = Path(stage)
        shutil.copytree(root / "runtime", target, dirs_exist_ok=True, ignore=ignored)
        shutil.copytree(root / "agent", target / "agent", ignore=ignored)
        sys.path.insert(0, stage)
        if find_spec("backend") or find_spec("context"):
            raise RuntimeError(
                "The staged Runtime must import without the app or context package"
            )
        import_module("main")
        copilot = import_module("copilot")
        settings = import_module("agent.copilot.entrypoint").settings
        with (
            patch.object(settings, "initialize", stand_in_checkpoint_pool),
            TestClient(copilot.app) as client,
        ):
            result = client.post("/invocations", json={"operation": "health"})
        if result.status_code != 200 or result.json() != {"healthy": True}:
            raise RuntimeError("The staged Copilot did not answer its health check")


if __name__ == "__main__":
    verify()
