"""Edit a local .env file in place. Used by commands that create credentials (database roles, the Gmail sign-in) and must put them straight into .env
without ever printing them."""
from __future__ import annotations

import os
from pathlib import Path


def set_env_values(path: Path, values: dict[str, str]) -> None:
    """Set KEY=value lines in a .env file, keeping every other line as it is (and the file readable only by its owner)."""
    lines = path.read_text(encoding="utf8").splitlines() if path.exists() else []
    pending = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in pending and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf8")
    os.chmod(path, 0o600)
