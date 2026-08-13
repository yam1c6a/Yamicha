"""The first capability: execute one already-integrated read request exactly."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import uuid4

from yamicha.contracts import (
    CapabilityExecutionPermit,
    CapabilityOperation,
    CapabilityResult,
    ExternalTime,
    READ_ONLY_EXPECTED_EFFECT,
    ReadOnlyToolResult,
)
from yamicha.life.ports import CAPABILITY_DEFINITION


class ReadOnlyResourceReader(Protocol):
    def read(self, target: str) -> ReadOnlyToolResult: ...


class ReadOnlyCapability:
    definition = CAPABILITY_DEFINITION

    def __init__(
        self,
        reader: ReadOnlyResourceReader,
        *,
        permit_validator: Callable[[CapabilityExecutionPermit], bool],
        result_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._reader = reader
        self._permit_validator = permit_validator
        self._result_id_factory = result_id_factory or (lambda: str(uuid4()))
        self._results: dict[str, CapabilityResult] = {}

    def execute(
        self,
        permit: CapabilityExecutionPermit,
        completed_at: ExternalTime,
    ) -> CapabilityResult:
        if not self._permit_validator(permit):
            raise ValueError("capability permit was not issued by the active gate")
        request = permit.request
        if request.operation is not CapabilityOperation.READ_TEXT:
            raise ValueError("read-only capability received a different operation")
        if request.expected_effect != READ_ONLY_EXPECTED_EFFECT:
            raise ValueError("read-only capability received a mutable expected effect")
        if request.request_id in self._results:
            raise ValueError("capability request cannot execute more than once")
        tool_result = self._reader.read(request.target)
        result = CapabilityResult(
            result_id=self._required_result_id(),
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            target=request.target,
            operation=request.operation,
            status=tool_result.status,
            content=tool_result.content,
            observed_scope=tool_result.observed_scope,
            remaining_scope=tool_result.remaining_scope,
            detail=tool_result.detail,
            uncertainty=tool_result.uncertainty,
            completed_at=completed_at,
        )
        self._results[request.request_id] = result
        return result

    @property
    def results(self) -> tuple[CapabilityResult, ...]:
        return tuple(self._results.values())

    def _required_result_id(self) -> str:
        result_id = self._result_id_factory()
        if not result_id.strip():
            raise ValueError("capability result ID must not be empty")
        return result_id
