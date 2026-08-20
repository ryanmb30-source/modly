"""Shared path-containment helpers.

Three endpoints independently resolved caller-supplied paths against a root and
disagreed about whether to check containment at all, which produced two
arbitrary-read bugs. Route every such resolution through here instead.
"""
from pathlib import Path

from fastapi import HTTPException


def resolve_within(root: Path, candidate: str) -> Path:
    """Resolve ``candidate`` under ``root`` and refuse anything that escapes it.

    ``Path.is_relative_to`` is used rather than a string prefix compare: a
    ``str.startswith`` check treats ``/workspace-evil`` as living inside
    ``/workspace``.

    Note that on Windows a drive-absolute operand replaces the base entirely
    (``Path("F:/ws") / "C:/Windows/win.ini"`` is ``C:/Windows/win.ini``), so the
    check has to run on the *resolved* result, not on the raw input.
    """
    root_resolved = Path(root).resolve()
    full = (root_resolved / candidate).resolve()
    if not full.is_relative_to(root_resolved):
        raise HTTPException(status_code=400, detail="Invalid path")
    return full
