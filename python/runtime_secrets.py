"""Load board-local runtime secrets without putting them in the repository."""

from __future__ import annotations

import os
from pathlib import Path


SECRET_FILE_NAME = ".secrets.env"


def _read_api_key(path: Path) -> str | None:
    """Read OPENAI_API_KEY from a simple KEY=value file."""

    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if separator and name.strip() == "OPENAI_API_KEY":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            return value or None
    return None


def load_runtime_secrets() -> Path | None:
    """Load the API key from a local file when App Lab hides shell exports.

    An existing non-empty environment variable always takes precedence.  The
    secret file is deliberately limited to the app root and ``python``
    directory so it is easy to keep local and out of Git.
    """

    if os.getenv("OPENAI_API_KEY", "").strip():
        return None

    python_dir = Path(__file__).resolve().parent
    candidates = (python_dir.parent / SECRET_FILE_NAME, python_dir / SECRET_FILE_NAME)
    for path in candidates:
        api_key = _read_api_key(path)
        if api_key:
            os.environ["OPENAI_API_KEY"] = api_key
            print(f"OPENAI_API_KEY loaded from {path.name}.")
            return path
    return None
