"""What the two workflow checks share: reading ``.github/workflows/*.yml`` with
the line of each key, and the finding they annotate.

Parsing is ``yaml.compose`` rather than ``yaml.safe_load`` so each finding can
carry the line of the key at fault. Standard library plus PyYAML.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from yaml.nodes import Node

WORKFLOWS_DIR = ".github/workflows"


@dataclass(frozen=True)
class Finding:
    """One rule breach, with the position to annotate."""

    rule: str
    path: str
    line: int
    message: str


def load_yaml() -> ModuleType | None:
    """Return the yaml module, or None when PyYAML is not importable."""
    try:
        import yaml  # noqa: PLC0415 - optional at import time on purpose
    except Exception:  # pragma: no cover - depends on the runner image
        return None
    return yaml


def items(node: Node | None) -> list[tuple[str, Node, int]]:
    """(key text, value node, key line) for a mapping node, else []."""
    from yaml.nodes import MappingNode, ScalarNode

    if not isinstance(node, MappingNode):
        return []
    return [(key.value, value, key.start_mark.line + 1)
            for key, value in node.value if isinstance(key, ScalarNode)]


def find(node: Node | None, name: str) -> tuple[Node | None, int]:
    """The (value node, key line) of a mapping key, else (None, 1)."""
    for key, value, line in items(node):
        if key == name:
            return value, line
    return None, 1


def workflow_files(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    return [
        os.path.join(directory, name)
        for name in sorted(os.listdir(directory))
        if name.endswith((".yml", ".yaml")) and os.path.isfile(os.path.join(directory, name))
    ]


def scan(root: str, workflows_dir: str, check: Callable[[str, str], list[Finding]]) -> list[Finding]:
    """Every finding ``check(relative path, text)`` returns, file by file; an
    unreadable file is a finding, never a silent pass."""
    findings: list[Finding] = []
    for path in workflow_files(workflows_dir):
        relative = os.path.relpath(path, root)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as exc:
            findings.append(Finding("workflow-unparsable", relative, 1, f"Unreadable: {exc}"))
            continue
        findings.extend(check(relative, text))
    return findings


def escape(message: str) -> str:
    """Escape a workflow-command message so it stays one annotation."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
