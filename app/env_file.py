from __future__ import annotations

import re
import shutil
from pathlib import Path

from dotenv import dotenv_values

_KEY_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_NEEDS_QUOTES_RE = re.compile(r"[\s#'\"$\\]")


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {key: value or "" for key, value in dotenv_values(path).items()}


def _render(key: str, value: str) -> str:
    if _NEEDS_QUOTES_RE.search(value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{key}="{escaped}"'
    return f"{key}={value}"


def update_env(path: Path, changes: dict[str, str]) -> None:
    """
    Change values while keeping comments, order and every other line. The last line of a
    key wins in dotenv, so duplicates are all rewritten. Written in place rather than by
    rename: in Docker this file is a bind mount, and a mount cannot be replaced.
    """
    if any("\n" in value or "\r" in value for value in changes.values()):
        raise ValueError("a value cannot span lines")  # it would inject further settings

    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(changes)
    output: list[str] = []
    for line in lines:
        match = _KEY_LINE_RE.match(line)
        if match and match.group(1) in changes:
            output.append(_render(match.group(1), changes[match.group(1)]))
            remaining.pop(match.group(1), None)
        else:
            output.append(line)
    output.extend(_render(key, value) for key, value in remaining.items())

    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.bak"))
    # newline: on Windows the default would write CRLF, and this file moves to Linux servers.
    path.write_text("\n".join(output) + "\n", encoding="utf-8", newline="\n")
