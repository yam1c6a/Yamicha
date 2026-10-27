"""Judgment-owned parsing for explicit stage-14 memory governance."""

from __future__ import annotations

import re
from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import (
    ConversationMemoryGovernanceRequest,
    ConversationMemoryOperationKind,
    ConversationMemoryWindow,
    InformationCertainty,
    SensoryEvent,
)
from yamicha.life.stage13 import Stage13Judgment


class Stage14Judgment(Stage13Judgment):
    _CORRECTION_PATTERNS = (
        re.compile(r"^訂正[：:]\s*(.+?)\s*(?:→|->)\s*(.+?)[。.!！]?$"),
        re.compile(r"^(.+?)じゃなくて(.+?)[。.!！]?$"),
    )
    _PHYSICAL_DELETE_PATTERNS = (
        re.compile(
            r"^(.+?)(?:のこと)?(?:を)?完全に(?:削除して|忘れて)"
            r"(?:ください)?[。.!！]?$"
        ),
        re.compile(r"^完全に(?:削除|忘却)[：:]\s*(.+?)[。.!！]?$"),
    )
    _DEACTIVATION_PATTERNS = (
        re.compile(r"^(.+?)(?:を)?(?:無効|非活性)にして(?:ください)?[。.!！]?$"),
    )
    _REEVALUATION_PATTERNS = (
        re.compile(r"^(.+?)(?:を)?確認済みにして(?:ください)?[。.!！]?$"),
    )

    def __init__(
        self,
        *,
        conversation_memory_governance_request_id_factory: Callable[[], str] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._governance_request_id_factory = (
            conversation_memory_governance_request_id_factory
            or (lambda: str(uuid4()))
        )
        self._governance_request_ids: set[str] = set()

    def propose_memory_governance(
        self,
        event: SensoryEvent,
        window: ConversationMemoryWindow | None,
    ) -> ConversationMemoryGovernanceRequest | None:
        text = event.meaning.normalized_text.strip()
        operation: ConversationMemoryOperationKind | None = None
        replacement: str | None = None
        replacement_certainty: InformationCertainty | None = None
        reason = ""
        for pattern in self._CORRECTION_PATTERNS:
            match = pattern.fullmatch(text)
            if match is not None:
                operation = ConversationMemoryOperationKind.CORRECTION
                replacement = self._replacement_meaning(
                    match.group(1).strip(),
                    match.group(2).strip(),
                    window,
                )
                reason = "the counterpart explicitly corrected remembered content"
                break
        if operation is None:
            for pattern in self._PHYSICAL_DELETE_PATTERNS:
                if pattern.fullmatch(text) is not None:
                    operation = ConversationMemoryOperationKind.PHYSICAL_DELETE
                    reason = "the counterpart requested physical deletion"
                    break
        if operation is None:
            for pattern in self._DEACTIVATION_PATTERNS:
                if pattern.fullmatch(text) is not None:
                    operation = ConversationMemoryOperationKind.DEACTIVATION
                    reason = "the counterpart explicitly requested memory deactivation"
                    break
        if operation is None:
            for pattern in self._REEVALUATION_PATTERNS:
                if pattern.fullmatch(text) is not None:
                    operation = ConversationMemoryOperationKind.REEVALUATION
                    replacement_certainty = InformationCertainty.CONFIRMED
                    reason = "the counterpart explicitly confirmed remembered content"
                    break
        if operation is None:
            return None
        request_id = self._governance_request_id_factory()
        if not request_id.strip() or request_id in self._governance_request_ids:
            raise ValueError("conversation-memory governance request ID must be unique")
        self._governance_request_ids.add(request_id)
        selections = () if window is None else window.selections
        return ConversationMemoryGovernanceRequest(
            request_id=request_id,
            lifecycle_id=event.correlation_id,
            counterpart_id=event.source_id,
            operation=operation,
            target_items=tuple(
                (selection.memory_item_id, selection.memory_item_version)
                for selection in selections
            ),
            source_reference=event.event_id,
            reason=reason,
            requested_at=event.received_at,
            replacement_meaning=replacement,
            replacement_certainty=replacement_certainty,
        )

    def propose_unmatched_logical_forgetting(
        self,
        event: SensoryEvent,
    ) -> ConversationMemoryGovernanceRequest | None:
        if not self._is_forget_text(event.meaning.normalized_text):
            return None
        request_id = self._governance_request_id_factory()
        if not request_id.strip() or request_id in self._governance_request_ids:
            raise ValueError("conversation-memory governance request ID must be unique")
        self._governance_request_ids.add(request_id)
        return ConversationMemoryGovernanceRequest(
            request_id=request_id,
            lifecycle_id=event.correlation_id,
            counterpart_id=event.source_id,
            operation=ConversationMemoryOperationKind.LOGICAL_FORGET,
            target_items=(),
            source_reference=event.event_id,
            reason="the counterpart requested logical forgetting",
            requested_at=event.received_at,
        )

    @staticmethod
    def is_confirmation(text: str) -> bool:
        return text.strip(" \t\r\n、,。.!！") in {
            "はい",
            "確認しました",
            "完全に削除して",
            "削除を承認します",
        }

    @staticmethod
    def is_cancellation(text: str) -> bool:
        return text.strip(" \t\r\n、,。.!！") in {
            "いいえ",
            "やめて",
            "キャンセル",
        }

    @staticmethod
    def _replacement_meaning(
        target: str,
        replacement: str,
        window: ConversationMemoryWindow | None,
    ) -> str:
        if window is None or len(window.selections) != 1:
            return replacement
        previous = window.selections[0].meaning
        if target not in previous:
            return replacement
        prefix, suffix = previous.split(target, 1)
        replacement_subject = replacement
        if suffix and replacement.endswith(suffix):
            replacement_subject = replacement[: -len(suffix)]
        value = f"{prefix}{replacement_subject}{suffix}".strip()
        return value or replacement
