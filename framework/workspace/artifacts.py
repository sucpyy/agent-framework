"""Artifacts — 产生的实体。

文件、代码、报告、生成的资源——Workspace 在执行过程中产生的所有东西。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Artifact:
    """单个产出物。"""
    name: str
    type: str  # "file" | "code" | "report" | "generated"
    path: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class Artifacts:
    """产出物集合——存储和索引 Workspace 产生的所有实体。"""

    def __init__(self):
        self._items: dict[str, Artifact] = {}

    def add(self, artifact: Artifact) -> None:
        self._items[artifact.name] = artifact

    def get(self, name: str) -> Artifact | None:
        return self._items.get(name)

    def list_by_type(self, artifact_type: str) -> list[Artifact]:
        return [a for a in self._items.values() if a.type == artifact_type]

    def list_all(self) -> list[str]:
        return list(self._items.keys())

    def remove(self, name: str) -> None:
        self._items.pop(name, None)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)
