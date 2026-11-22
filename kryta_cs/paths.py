"""Where things are: the Kryta checkout, the vendored toolkit, the editor's own files.

Nothing here writes. `bootstrap_rurik()` puts Rurik's toolkit on sys.path and gives it
a vault directory of its own, because a few of its modules ask for one at import time
or when they refuse a path; the editor never uses that vault for anything else.
"""

from __future__ import annotations

import json
import os
import sys

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.dirname(PACKAGE_DIR)                       # asura-cs
RURIK_DIR = os.path.join(TOOL_DIR, "third_party", "rurik")
RURIK_MAPDATA = os.path.join(RURIK_DIR, "toolkit", "mapdata")
RURIK_TOOLKIT = os.path.join(RURIK_DIR, "toolkit")
RURIK_CLIENTSCAN = os.path.join(RURIK_TOOLKIT, "clientscan")
RURIK_AUTHSRV = os.path.join(RURIK_TOOLKIT, "authsrv")


def find_repo_root(start: str | None = None) -> str | None:
    """The Kryta checkout: the first parent holding AGENTS.md and data/sql/base/db_world."""
    here = os.path.abspath(start or TOOL_DIR)
    while True:
        if (os.path.isfile(os.path.join(here, "AGENTS.md"))
                and os.path.isdir(os.path.join(here, "data", "sql", "base", "db_world"))):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


REPO_ROOT = find_repo_root()


def user_dir() -> str:
    """~/.kryta_cs: the editor's preferences and its private scratch space."""
    path = os.path.join(os.path.expanduser("~"), ".kryta_cs")
    os.makedirs(path, exist_ok=True)
    return path


def projects_dir() -> str:
    """Where map projects live by default: <repo>/data/maps/custom, else ~/.kryta_cs/maps."""
    if REPO_ROOT:
        path = os.path.join(REPO_ROOT, "data", "maps", "custom")
    else:
        path = os.path.join(user_dir(), "maps")
    os.makedirs(path, exist_ok=True)
    return path


_BOOTSTRAPPED = False


def bootstrap_rurik() -> None:
    """Make `import deploy`, `import areatable`, ... resolve to the vendored toolkit."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    vault = os.path.join(user_dir(), "rurik-vault")
    os.makedirs(vault, exist_ok=True)
    os.environ.setdefault("RURIK_VAULT", vault)
    for path in (RURIK_TOOLKIT, RURIK_MAPDATA, RURIK_CLIENTSCAN, RURIK_AUTHSRV):
        if path not in sys.path:
            sys.path.insert(0, path)
    _BOOTSTRAPPED = True


# ------------------------------------------------------------------ preferences

PREFS_FILE = "settings.json"
DEFAULT_PREFS = {
    "archive": "",          # the Gw.dat the client runs from (and the server reads)
    "recent": [],           # recently opened project files
    "theme": "classic",
}


def load_prefs() -> dict:
    path = os.path.join(user_dir(), PREFS_FILE)
    prefs = dict(DEFAULT_PREFS)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            prefs.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return prefs


def save_prefs(prefs: dict) -> None:
    path = os.path.join(user_dir(), PREFS_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(prefs, fh, indent=2)
    os.replace(tmp, path)
