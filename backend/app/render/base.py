"""Render backend interface: an EDL v1 plan in, a finished MP4 + cover out."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

Progress = Callable[[float, str], None]


class RenderError(RuntimeError):
    pass


@dataclass
class RenderResult:
    video: Path
    cover: Path
    backend: str
    seconds: float
    editor_project: Path | None = None
    warnings: list[str] = field(default_factory=list)


class RenderBackend(Protocol):
    name: str

    def available(self) -> bool: ...

    def render(self, plan: dict, out_video: Path, out_cover: Path, work_dir: Path,
               progress: Progress | None = None) -> RenderResult: ...
