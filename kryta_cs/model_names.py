"""Human names for donor prop models, keyed by file id.

Gw.dat carries no names for prop models, so the Models tab used to show only an
index and a file id. `model_names.json` beside this module holds names guessed
by eye from renders of each model (tools/render_models.py). It is plain JSON:
edit a name there and the editor shows it on its next start.
"""

from __future__ import annotations

import json
import os

NAMES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_names.json")

_names: dict[int, str] | None = None


def _load() -> dict[int, str]:
    global _names
    if _names is None:
        _names = {}
        try:
            with open(NAMES_FILE, "r", encoding="utf-8") as fh:
                raw = json.load(fh).get("names", {})
            for key, name in raw.items():
                try:
                    _names[int(str(key), 0)] = str(name)
                except ValueError:
                    continue
        except (OSError, ValueError):
            pass
    return _names


def name_for(file_id) -> str:
    """The model's name, or "" when it has none."""
    return _load().get(int(file_id), "")
