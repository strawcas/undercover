"""Process the waiting claims queue from the terminal."""

import sys

from backend.batch import main
from backend.config import load_environment

if __name__ == "__main__":
    load_environment()
    raise SystemExit(main(argv=sys.argv[1:], service=True))
