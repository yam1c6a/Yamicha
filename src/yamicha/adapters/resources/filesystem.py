"""A UTF-8 text reader confined to one configured root directory."""

from __future__ import annotations

from pathlib import Path

from yamicha.contracts import CapabilityResultStatus, ReadOnlyToolResult


class BoundedTextFileReader:
    def __init__(self, root: str | Path, *, max_characters: int = 16_384) -> None:
        if max_characters <= 0:
            raise ValueError("maximum character count must be positive")
        self._root = Path(root).resolve()
        self._max_characters = max_characters

    @property
    def root(self) -> Path:
        return self._root

    def read(self, target: str) -> ReadOnlyToolResult:
        if not target.strip():
            return self._failure("target is empty")
        candidate = (self._root / target).resolve()
        if candidate == self._root or self._root not in candidate.parents:
            return self._failure("target is outside the configured read boundary")
        try:
            content = candidate.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError, PermissionError, UnicodeError) as error:
            return self._failure(type(error).__name__)
        except OSError as error:
            return ReadOnlyToolResult(
                status=CapabilityResultStatus.UNKNOWN,
                content=None,
                observed_scope=target,
                remaining_scope=None,
                detail=type(error).__name__,
                uncertainty="the external read outcome could not be confirmed",
            )
        if len(content) > self._max_characters:
            return ReadOnlyToolResult(
                status=CapabilityResultStatus.PARTIAL_SUCCESS,
                content=content[: self._max_characters],
                observed_scope=f"{target}:characters:0-{self._max_characters}",
                remaining_scope=f"{target}:characters:{self._max_characters}-end",
                detail="configured read limit was reached",
            )
        return ReadOnlyToolResult(
            status=CapabilityResultStatus.SUCCESS,
            content=content,
            observed_scope=target,
            remaining_scope=None,
            detail="the complete UTF-8 text resource was read",
        )

    @staticmethod
    def _failure(detail: str) -> ReadOnlyToolResult:
        return ReadOnlyToolResult(
            status=CapabilityResultStatus.FAILURE,
            content=None,
            observed_scope="no content",
            remaining_scope=None,
            detail=detail,
        )
