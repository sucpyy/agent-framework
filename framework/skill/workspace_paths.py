"""Resolve tool file paths from the project workspace, independent of process CWD."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_workspace_root() -> Path:
    configured = os.environ.get("AGENT_WORKSPACE_ROOT", "").strip()
    if not configured:
        return PROJECT_ROOT
    path = Path(configured).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def resolve_workspace_path(raw_path: str | os.PathLike[str] | None, *, default: str | None = None) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        if default is None:
            raise ValueError("file_path 不能为空")
        value = default
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = get_workspace_root() / path
    return path.resolve()


def display_workspace_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(get_workspace_root()).as_posix()
    except ValueError:
        return str(path.resolve())
