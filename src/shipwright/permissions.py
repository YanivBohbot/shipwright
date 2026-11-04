"""Filesystem permissions: org policies are read-only; subagents cannot touch memory."""

from __future__ import annotations

from deepagents import FilesystemPermission

PERMISSIONS: list[FilesystemPermission] = [
    FilesystemPermission(operations=["write"], paths=["/policies/**"], mode="deny"),
]

SUBAGENT_PERMISSIONS: list[FilesystemPermission] = [
    FilesystemPermission(operations=["write"], paths=["/policies/**"], mode="deny"),
    FilesystemPermission(operations=["write"], paths=["/memories/**"], mode="deny"),
]
