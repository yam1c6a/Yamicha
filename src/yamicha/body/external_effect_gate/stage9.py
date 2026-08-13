"""Stage-9 authorization gate for one integrated read-only capability."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import uuid4

from yamicha.body.persistence import SQLitePersistenceStore
from yamicha.contracts import (
    CapabilityExecutionPermit,
    CapabilityOperation,
    CapabilityPermissionObservation,
    CapabilityResult,
    CapabilityUseProposal,
    ExternalTime,
    IntegratedCapabilityRequest,
    READ_ONLY_EXPECTED_EFFECT,
)

from .port import EXTERNAL_EFFECT_GATE_DEFINITION


@dataclass(frozen=True, slots=True)
class CapabilityGateOutcome:
    permit: CapabilityExecutionPermit | None
    duplicate: bool
    reason: str


class RegisteredCapabilityPermissionObserver:
    """Observe only permissions fixed in the active composition."""

    observer_id = "registered-capability-permission-observer"

    def __init__(
        self,
        permissions: Mapping[str, tuple[str, ...]],
        *,
        observation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._permissions = {
            authority: frozenset(targets)
            for authority, targets in permissions.items()
        }
        self._observation_id_factory = (
            observation_id_factory or (lambda: str(uuid4()))
        )
        self._observations: dict[str, CapabilityPermissionObservation] = {}

    def observe(
        self,
        request: IntegratedCapabilityRequest,
        observed_at: ExternalTime,
    ) -> CapabilityPermissionObservation:
        observation_id = self._observation_id_factory()
        if not observation_id.strip():
            raise ValueError("permission observation ID must not be empty")
        allowed_targets = self._permissions.get(request.authority_id, frozenset())
        observation = CapabilityPermissionObservation(
            observation_id=observation_id,
            observer=self.observer_id,
            authority_id=request.authority_id,
            target=request.target,
            operation=request.operation,
            granted=(
                request.operation is CapabilityOperation.READ_TEXT
                and request.target in allowed_targets
            ),
            observed_at=observed_at,
        )
        self._observations[observation.observation_id] = observation
        return observation

    def issued(self, observation: CapabilityPermissionObservation) -> bool:
        return self._observations.get(observation.observation_id) == observation


class Stage9ExternalEffectGate:
    definition = EXTERNAL_EFFECT_GATE_DEFINITION

    def __init__(
        self,
        *,
        store: SQLitePersistenceStore,
        core_request_validator: Callable[[IntegratedCapabilityRequest], bool],
        judgment_proposal_validator: Callable[[CapabilityUseProposal], bool],
        permission_validator: Callable[[CapabilityPermissionObservation], bool],
        normal_operation_allowed: Callable[[], bool],
        permit_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._core_request_validator = core_request_validator
        self._judgment_proposal_validator = judgment_proposal_validator
        self._permission_validator = permission_validator
        self._normal_operation_allowed = normal_operation_allowed
        self._permit_id_factory = permit_id_factory or (lambda: str(uuid4()))
        self._permits: dict[str, CapabilityExecutionPermit] = {}

    def authorize(
        self,
        request: IntegratedCapabilityRequest,
        permission: CapabilityPermissionObservation,
        authorized_at: ExternalTime,
    ) -> CapabilityGateOutcome:
        rejection = self._validate(request, permission)
        if rejection is not None:
            return CapabilityGateOutcome(None, False, rejection)
        fingerprint = self.request_fingerprint(request)
        reserved = self._store.reserve_capability_execution(
            idempotency_key=request.idempotency_key,
            request_id=request.request_id,
            request_fingerprint=fingerprint,
            reserved_at=authorized_at,
        )
        if not reserved:
            return CapabilityGateOutcome(
                None,
                True,
                "idempotency key was already reserved; execution was not repeated",
            )
        permit_id = self._permit_id_factory()
        if not permit_id.strip():
            raise ValueError("capability permit ID must not be empty")
        permit = CapabilityExecutionPermit(
            permit_id=permit_id,
            request=request,
            permission=permission,
            authorized_at=authorized_at,
        )
        self._permits[permit.permit_id] = permit
        return CapabilityGateOutcome(
            permit,
            False,
            "integrated read-only request was authorized",
        )

    def issued(self, permit: CapabilityExecutionPermit) -> bool:
        return self._permits.get(permit.permit_id) == permit

    def record_result(self, result: CapabilityResult) -> None:
        self._store.complete_capability_execution(result)

    def _validate(
        self,
        request: IntegratedCapabilityRequest,
        permission: CapabilityPermissionObservation,
    ) -> str | None:
        if not self._normal_operation_allowed():
            return "normal capability execution is blocked by protection"
        if not self._core_request_validator(request):
            return "request was not issued by Core"
        if not self._judgment_proposal_validator(request.proposal):
            return "proposal was not issued by Judgment"
        if request.operation is not CapabilityOperation.READ_TEXT:
            return "operation is not the registered read-only operation"
        if request.expected_effect != READ_ONLY_EXPECTED_EFFECT:
            return "expected effect is not read-only"
        if not request.proposal.verification_required:
            return "result verification requirement is missing"
        if not self._permission_validator(permission):
            return "permission observation was not issued by its registered observer"
        if (
            not permission.granted
            or permission.authority_id != request.authority_id
            or permission.target != request.target
            or permission.operation is not request.operation
        ):
            return "permission does not match the requested target and operation"
        return None

    @staticmethod
    def request_fingerprint(request: IntegratedCapabilityRequest) -> str:
        payload = json.dumps(
            {
                "request_id": request.request_id,
                "proposal_id": request.proposal.proposal_id,
                "core_finalization_id": request.core_finalization_id,
                "target": request.target,
                "operation": request.operation.value,
                "authority_id": request.authority_id,
                "expected_effect": request.expected_effect,
                "idempotency_key": request.idempotency_key,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
