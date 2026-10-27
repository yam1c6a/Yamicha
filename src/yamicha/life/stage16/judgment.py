"""Judgment proposes finite, explicit experience changes and applies their scope."""

from __future__ import annotations

import re
from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import DecisionDirection, JudgmentContext, SensoryEvent
from yamicha.contracts.experience import (
    ExperienceChangeRequest, ExperienceEvidence, ExperienceEvidenceKind, ExperienceItem,
    ExperienceOperation, ExperiencePendingConfirmation, ExperienceReaction, ExperienceValue,
)
from yamicha.life.stage15 import Stage15Judgment


EXPERIENCE_DIALOGUE_CONFIRMATION = "confirmed_experience:clarify_dialogue_intent"


class Stage16Judgment(Stage15Judgment):
    _REACTIONS = {
        "長すぎた": ExperienceReaction.TOO_LONG,
        "説明が長すぎた": ExperienceReaction.TOO_LONG,
        "説明が足りなかった": ExperienceReaction.TOO_SHORT,
        "短すぎた": ExperienceReaction.TOO_SHORT,
        "確認してから答えてほしかった": ExperienceReaction.WANTED_CONFIRMATION,
        "確認せず答えてほしかった": ExperienceReaction.DIRECT_RESPONSE_PREFERRED,
        "そのまま答えてほしかった": ExperienceReaction.DIRECT_RESPONSE_PREFERRED,
    }

    def __init__(self, *, experience_request_id_factory: Callable[[], str] | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._experience_request_id_factory = experience_request_id_factory or (lambda: str(uuid4()))
        self._experience_requests: dict[str, ExperienceChangeRequest] = {}

    @classmethod
    def is_experience_command(cls, text: str) -> bool:
        return text.strip().startswith(("経験：", "経験:", "経験確認", "経験取消", "経験見直し", "経験不採用"))

    @classmethod
    def is_management_command(cls, text: str) -> bool:
        text = text.strip()
        return (
            cls.is_experience_command(text) or cls.is_relationship_command(text)
            or cls._is_forget_text(text)
            or any(pattern.fullmatch(text) is not None for pattern in (
                *cls._PREFIX_PATTERNS, *cls._SUFFIX_PATTERNS,
                *cls._CORRECTION_PATTERNS, *cls._PHYSICAL_DELETE_PATTERNS,
                *cls._DEACTIVATION_PATTERNS, *cls._REEVALUATION_PATTERNS,
            ))
        )

    def propose_experience_change(
        self, event: SensoryEvent, context_id: str,
        evidence: tuple[ExperienceEvidence, ...], items: tuple[ExperienceItem, ...],
        pending: ExperiencePendingConfirmation | None,
    ) -> ExperienceChangeRequest | None:
        text = event.meaning.normalized_text.strip()
        if not self.is_experience_command(text):
            return None
        request_id = self._experience_request_id_factory()
        if not request_id.strip() or request_id in self._experience_requests:
            raise ValueError("experience request ID must be unique and nonempty")
        base = dict(request_id=request_id, lifecycle_id=event.correlation_id,
            counterpart_id=event.source_id, context_id=context_id,
            source_reference=event.event_id, requested_at=event.received_at)
        try:
            values = self._parse_experience(text, event, context_id, evidence, items, pending)
            request = ExperienceChangeRequest(**base, **values)
        except ValueError:
            request = ExperienceChangeRequest(**base, operation=ExperienceOperation.INVALID,
                reason="経験操作の形式・実観測・反応または限定範囲を確認できませんでした")
        self._experience_requests[request_id] = request
        return request

    def issued_experience_request(self, request: ExperienceChangeRequest) -> bool:
        return self._experience_requests.get(request.request_id) == request

    @classmethod
    def _parse_experience(cls, text: str, event: SensoryEvent, context_id: str,
        evidence: tuple[ExperienceEvidence, ...], items: tuple[ExperienceItem, ...],
        pending: ExperiencePendingConfirmation | None,
    ) -> dict[str, object]:
        for command, operation in (("経験確認", ExperienceOperation.CONFIRM), ("経験取消", ExperienceOperation.CANCEL)):
            match = re.fullmatch(command + r"(?:[：:]\s*(\S+))?", text)
            if match:
                return dict(operation=operation, pending_id=match.group(1) or (None if pending is None else pending.pending_id),
                    reason="相手が経験候補の確認待ちへ明示的に応答した")
        sections = [part.strip() for part in text.split("|")]
        if len(sections) > 2:
            raise ValueError("too many experience scope sections")
        scope: tuple[str, ...] | None = None
        if len(sections) == 2:
            if not sections[1].startswith("話題="):
                raise ValueError("unsupported experience scope metadata")
            scope = tuple(word.strip() for word in sections[1][3:].split(","))
            if not scope:
                raise ValueError("empty experience scope")
        command = sections[0]
        eligible = tuple(e for e in evidence if e.counterpart_id == event.source_id and e.context_id == context_id and e.observed_at.value <= event.received_at.value)
        match = re.fullmatch(r"経験不採用[：:]\s*([^：:\s]+)", command)
        if match:
            if scope is not None:
                raise ValueError("rejection cannot change scope")
            item = next((item for item in items if item.item_id == match.group(1) and item.counterpart_id == event.source_id), None)
            return dict(operation=ExperienceOperation.REJECT, target_item_id=match.group(1),
                expected_version=None if item is None else item.version,
                evidence_id=None if item is None else item.evidence_id,
                reaction=ExperienceReaction.NO_LONGER_APPLICABLE,
                reason="相手が指定した経験は今後適用しないと明示したため別入力で確認する")
        match = re.fullmatch(r"経験見直し[：:]\s*([^：:\s]+)[：:]\s*(.+)", command)
        if match:
            item = next((item for item in items if item.item_id == match.group(1) and item.counterpart_id == event.source_id), None)
            if item is None:
                raise ValueError("experience item was not found")
            reaction_text = match.group(2).strip()
            candidates = tuple(e for e in eligible if (e.capability_operation, e.capability_target) == (item.capability_operation, item.capability_target))
            observation = candidates[-1] if candidates else None
            operation = ExperienceOperation.REASSESS
            target = dict(target_item_id=item.item_id, expected_version=item.version)
            if scope is None:
                scope = item.scope_keywords
        else:
            match = re.fullmatch(r"経験[：:]\s*直前[：:]\s*(.+)", command)
            if match:
                reaction_text = match.group(1).strip()
                candidates = tuple(e for e in eligible if e.kind is ExperienceEvidenceKind.DIALOGUE_OUTPUT)
                observation = candidates[-1] if candidates else None
            else:
                match = re.fullmatch(r"経験[：:]\s*能力[：:]\s*([^：:\s]+)[：:]\s*(.+)", command)
                if match is None:
                    raise ValueError("unsupported experience command")
                observation = next((e for e in eligible if e.kind is ExperienceEvidenceKind.CAPABILITY_RESULT and e.source_reference == match.group(1)), None)
                reaction_text = match.group(2).strip()
            operation = ExperienceOperation.ADD
            target = {}
        reaction = cls._REACTIONS.get(reaction_text)
        if reaction is None or observation is None:
            raise ValueError("explicit reaction requires a registered actual observation")
        return dict(operation=operation, reaction=reaction, evidence_id=observation.evidence_id,
            scope_keywords=scope or (), reason=f"実観測への相手の明示反応「{reaction_text}」を別入力で確認して有限の対話方針へ反映する", **target)

    def _select(self, context: JudgmentContext) -> tuple[DecisionDirection, str, tuple[str, ...], tuple[str, ...]]:
        selected, reason, uncertainties, conditions = super()._select(context)
        if selected in (DecisionDirection.HOLD, DecisionDirection.REFUSE, DecisionDirection.WAIT):
            return selected, reason, uncertainties, conditions
        text = context.event.meaning.normalized_text
        if self.is_experience_command(text):
            return DecisionDirection.RESPOND, "explicit experience command requests a bounded internal proposal or separate confirmation", (), ()
        if self.is_management_command(text):
            return selected, reason, uncertainties, conditions
        window = context.memory.experience_information
        if window is not None and window.selections:
            references = ", ".join(f"{s.item.item_id}:v{s.item.version}" for s in window.selections)
            reason += f"; confirmed bounded experience considered: {references}"
            if (selected is DecisionDirection.RESPOND
                    and context.memory.experience_confirmation_reference is None
                    and any(s.item.value is ExperienceValue.ASK_FIRST for s in window.selections)):
                selected = DecisionDirection.CONFIRM
                reason += "; confirmed experience requests clarification before an otherwise answerable response"
                conditions = (*conditions, EXPERIENCE_DIALOGUE_CONFIRMATION)
            elif selected is DecisionDirection.RESPOND and context.memory.experience_confirmation_reference is not None:
                reason += "; current input answers the preceding released experience confirmation"
        return selected, reason, uncertainties, conditions
