"""
Where to find standards/ and engine/.

In the repository the backend sits beside them:

    nawi-build/
      backend/     <- here
      standards/
      engine/

On a Hugging Face Space everything is uploaded flat, so they sit inside:

    /home/user/app/
      app.py       <- here
      standards/
      engine/

Both layouts are checked rather than assumed, so the same code runs in
either place with nothing to edit at deploy time.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _find(name: str) -> str:
    """Return the first existing candidate for a sibling or child directory."""
    for candidate in (os.path.join(HERE, name),            # flat (Space)
                      os.path.join(HERE, "..", name)):     # beside (repo)
        if os.path.isdir(candidate):
            return os.path.abspath(candidate)
    raise FileNotFoundError(
        f"cannot find '{name}/'. It must sit either beside {HERE} "
        f"(the repository layout) or inside it (the Space layout). "
        f"Upload standards/ and engine/ alongside the backend files."
    )


STANDARDS_DIR = _find("standards")
ENGINE_DIR = _find("engine")

if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)


def seed_file() -> str:
    """The synthetic dataset, used only by test_local.py. Not on the Space."""
    for candidate in (os.path.join(HERE, "seed", "out", "evaluations.json"),
                      os.path.join(HERE, "..", "seed", "out", "evaluations.json")):
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return ""
