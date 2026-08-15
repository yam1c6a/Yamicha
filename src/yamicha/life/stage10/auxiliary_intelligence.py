"""Auxiliary-intelligence organ for bounded local-model candidates."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Protocol
from uuid import uuid4

from yamicha.contracts import (
    AuxiliaryIntelligenceResult,
    ExternalIntelligenceResponse,
    ExternalTime,
    IntegratedIntelligenceRequest,
    IntelligenceCandidate,
    IntelligencePurpose,
    IntelligenceResultStatus,
)
from yamicha.life.ports import AUXILIARY_INTELLIGENCE_DEFINITION


class IntelligenceTransport(Protocol):
    def generate(
        self,
        request: IntegratedIntelligenceRequest,
    ) -> ExternalIntelligenceResponse: ...


class Stage10AuxiliaryIntelligence:
    definition = AUXILIARY_INTELLIGENCE_DEFINITION
    _EXTERNAL_EFFECT_CLAIMS = (
        "実行しました",
        "完了しました",
        "送信しました",
        "削除しました",
        "作成しました",
        "変更しました",
        "i executed",
        "i completed",
        "i sent",
        "i deleted",
        "i created",
        "i updated",
    )

    def __init__(
        self,
        transport: IntelligenceTransport,
        *,
        request_validator: Callable[[IntegratedIntelligenceRequest], bool],
        result_id_factory: Callable[[], str] | None = None,
        candidate_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._transport = transport
        self._request_validator = request_validator
        self._result_id_factory = result_id_factory or (lambda: str(uuid4()))
        self._candidate_id_factory = candidate_id_factory or (lambda: str(uuid4()))
        self._results: dict[str, AuxiliaryIntelligenceResult] = {}

    def generate_candidate(
        self,
        request: IntegratedIntelligenceRequest,
        completed_at: ExternalTime,
    ) -> AuxiliaryIntelligenceResult:
        if not self._request_validator(request):
            raise ValueError("intelligence request was not integrated by Core")
        if request.request_id in self._results:
            raise ValueError("intelligence request cannot execute more than once")
        response = self._transport.generate(request)
        status = response.status
        candidate = None
        detail = response.detail
        if status is IntelligenceResultStatus.SUCCESS:
            status, candidate, detail = self._candidate_from_response(
                request,
                response,
            )
        result = AuxiliaryIntelligenceResult(
            result_id=self._required_id(self._result_id_factory, "result"),
            request_id=request.request_id,
            lifecycle_id=request.proposal.lifecycle_id,
            purpose=request.proposal.purpose,
            status=status,
            model=response.model,
            candidate=candidate,
            detail=detail,
            completed_at=completed_at,
        )
        self._results[result.result_id] = result
        return result

    def issued(self, result: AuxiliaryIntelligenceResult) -> bool:
        return self._results.get(result.result_id) == result

    @property
    def results(self) -> tuple[AuxiliaryIntelligenceResult, ...]:
        return tuple(self._results.values())

    def _candidate_from_response(
        self,
        request: IntegratedIntelligenceRequest,
        response: ExternalIntelligenceResponse,
    ) -> tuple[IntelligenceResultStatus, IntelligenceCandidate | None, str]:
        assert response.content is not None
        if response.model != request.proposal.model:
            return (
                IntelligenceResultStatus.INVALID_OUTPUT,
                None,
                "external intelligence response used an unexpected model",
            )
        try:
            decoded = json.loads(response.content)
        except json.JSONDecodeError:
            return (
                IntelligenceResultStatus.INVALID_OUTPUT,
                None,
                "external intelligence content is not valid JSON",
            )
        if request.proposal.purpose is IntelligencePurpose.MEMORY_INTERPRETATION_CANDIDATE:
            text = self._memory_interpretation_text(decoded)
        elif (
            isinstance(decoded, dict)
            and set(decoded) == {"reply"}
            and isinstance(decoded["reply"], str)
            and decoded["reply"].strip()
        ):
            text = decoded["reply"].strip()
        else:
            return (
                IntelligenceResultStatus.INVALID_OUTPUT,
                None,
                "external intelligence content does not match the reply schema",
            )
        if text is None:
            return (
                IntelligenceResultStatus.INVALID_OUTPUT,
                None,
                "external intelligence content does not match the memory interpretation schema",
            )
        constraints = request.proposal.constraints
        if len(text) > constraints.max_output_characters or any(
            ord(character) < 32 and character not in {"\n", "\t"}
            for character in text
        ):
            return (
                IntelligenceResultStatus.CONSTRAINT_VIOLATION,
                None,
                "external intelligence candidate violates output constraints",
            )
        lowered = text.casefold()
        if (
            not constraints.external_effect_claims_allowed
            and any(claim in lowered for claim in self._EXTERNAL_EFFECT_CLAIMS)
        ):
            return (
                IntelligenceResultStatus.CONSTRAINT_VIOLATION,
                None,
                "external intelligence candidate claims an unconfirmed effect",
            )
        candidate = IntelligenceCandidate(
            candidate_id=self._required_id(self._candidate_id_factory, "candidate"),
            request_id=request.request_id,
            text=text,
            model=response.model,
            provenance=f"ollama-local:{response.model}",
        )
        return (
            IntelligenceResultStatus.SUCCESS,
            candidate,
            "bounded external intelligence candidate was received",
        )

    @staticmethod
    def _memory_interpretation_text(decoded: object) -> str | None:
        required = {
            "action",
            "subject",
            "meaning",
            "target_text",
            "certainty",
            "needs_confirmation",
            "evidence_quote",
        }
        if not isinstance(decoded, dict) or set(decoded) != required:
            return None
        if (
            not isinstance(decoded["action"], str)
            or decoded["action"] not in {"remember", "forget", "none"}
            or not isinstance(decoded["subject"], str)
            or decoded["subject"] not in {"self", "third_party", "unknown"}
            or not isinstance(decoded["certainty"], str)
            or decoded["certainty"] not in {"confirmed", "inferred", "unknown"}
            or not isinstance(decoded["needs_confirmation"], bool)
            or not all(
                value is None or isinstance(value, str)
                for value in (
                    decoded["meaning"],
                    decoded["target_text"],
                    decoded["evidence_quote"],
                )
            )
        ):
            return None
        action = decoded["action"]
        meaning = decoded["meaning"]
        target = decoded["target_text"]
        evidence = decoded["evidence_quote"]
        if any(
            isinstance(value, str) and not value.strip()
            for value in (meaning, target, evidence)
        ):
            return None
        if action == "remember" and (
            not isinstance(meaning, str)
            or not meaning.strip()
            or target is not None
            or not isinstance(evidence, str)
            or not evidence.strip()
        ):
            return None
        if action == "forget" and (
            not isinstance(target, str)
            or not target.strip()
            or meaning is not None
            or not isinstance(evidence, str)
            or not evidence.strip()
        ):
            return None
        if action == "none" and (meaning is not None or target is not None):
            return None
        return json.dumps(
            decoded,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _required_id(factory: Callable[[], str], label: str) -> str:
        value = factory()
        if not value.strip():
            raise ValueError(f"intelligence {label} ID must not be empty")
        return value
