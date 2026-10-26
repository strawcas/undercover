"""Process the waiting claims queue from the terminal."""

import sys

from backend.batch import main

if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv[1:]))
