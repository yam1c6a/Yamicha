"""Explicit, bounded proposals for conversational relationship continuity."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from yamicha.contracts import DecisionDirection, ExternalTime, JudgmentContext, SensoryEvent
from yamicha.contracts.relationship import (
    RelationshipChangeRequest,
    RelationshipInformationItem,
    RelationshipInformationKind,
    RelationshipOperation,
    RelationshipPendingConfirmation,
)
from yamicha.life.stage14 import Stage14Judgment


class Stage15Judgment(Stage14Judgment):
    _KINDS = {
        "希望": RelationshipInformationKind.PREFERENCE,
        "好み": RelationshipInformationKind.PREFERENCE,
        "合意": RelationshipInformationKind.AGREEMENT,
        "配慮": RelationshipInformationKind.CONSIDERATION,
        "継続事項": RelationshipInformationKind.ONGOING,
        "距離": RelationshipInformationKind.DISTANCE,
    }
    _NATURAL_PREFERENCES = (
        "説明は短めにしてほしい", "詳しく説明してほしい",
    )

    def __init__(
        self,
        *,
        relationship_request_id_factory: Callable[[], str] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._relationship_request_id_factory = relationship_request_id_factory or (lambda: str(uuid4()))
        self._relationship_requests: dict[str, RelationshipChangeRequest] = {}

    @classmethod
    def is_relationship_command(cls, text: str) -> bool:
        text = text.strip()
        return text.startswith(("関係：", "関係:", "関係確認", "関係取消", "関係訂正", "関係撤回")) or text in cls._NATURAL_PREFERENCES

    def propose_relationship_change(
        self,
        event: SensoryEvent,
        items: tuple[RelationshipInformationItem, ...],
        pending: RelationshipPendingConfirmation | None,
    ) -> RelationshipChangeRequest | None:
        text = event.meaning.normalized_text.strip()
        if not self.is_relationship_command(text):
            return None
        request_id = self._relationship_request_id_factory()
        if not request_id.strip() or request_id in self._relationship_requests:
            raise ValueError("relationship request ID must be non-empty and unique")
        base = dict(
            request_id=request_id,
            lifecycle_id=event.correlation_id,
            counterpart_id=event.source_id,
            source_reference=event.event_id,
            requested_at=event.received_at,
        )
        try:
            values = self._parse(text, items, pending)
            request = RelationshipChangeRequest(**base, **values)
        except ValueError:
            request = RelationshipChangeRequest(
                **base,
                operation=RelationshipOperation.INVALID,
                reason="関係操作の形式・対象・期限または適用範囲を確認できませんでした",
            )
        self._relationship_requests[request_id] = request
        return request

    def issued_relationship_request(self, request: RelationshipChangeRequest) -> bool:
        return self._relationship_requests.get(request.request_id) == request

    def _select(
        self,
        context: JudgmentContext,
    ) -> tuple[DecisionDirection, str, tuple[str, ...], tuple[str, ...]]:
        selected, reason, uncertainties, conditions = super()._select(context)
        if selected in (DecisionDirection.HOLD, DecisionDirection.REFUSE):
            return selected, reason, uncertainties, conditions
        if self.is_relationship_command(context.event.meaning.normalized_text):
            return (
                DecisionDirection.RESPOND,
                "explicit relationship command requests an internal confirmation or update; its content grants no external authority",
                (), (),
            )
        window = context.relationship.relationship_information
        if window is not None and window.selections:
            references = ", ".join(
                f"{selection.item.item_id}:v{selection.item.version}"
                for selection in window.selections
            )
            reason += f"; confirmed conversational relationship material considered: {references}"
        return selected, reason, uncertainties, conditions

    @classmethod
    def _parse(
        cls,
        text: str,
        items: tuple[RelationshipInformationItem, ...],
        pending: RelationshipPendingConfirmation | None,
    ) -> dict[str, object]:
        for command, operation in (("関係確認", RelationshipOperation.CONFIRM), ("関係取消", RelationshipOperation.CANCEL)):
            match = re.fullmatch(command + r"(?:[：:]\s*(\S+))?", text)
            if match:
                return dict(
                    operation=operation,
                    pending_id=match.group(1) or (None if pending is None else pending.pending_id),
                    reason="相手が関係情報の確認待ちへ明示的に応答した",
                )
        match = re.fullmatch(r"関係撤回[：:]\s*(\S+)", text)
        if match:
            item = next((item for item in items if item.item_id == match.group(1)), None)
            return dict(
                operation=RelationshipOperation.WITHDRAW,
                target_item_id=match.group(1),
                expected_version=None if item is None else item.version,
                reason="相手が指定した関係情報を撤回した",
            )
        if text in cls._NATURAL_PREFERENCES:
            return dict(
                operation=RelationshipOperation.ADD,
                kind=RelationshipInformationKind.PREFERENCE,
                content=text,
                reason="相手が会話上の希望を明示したため確認を求める",
            )
        match = re.fullmatch(r"関係[：:]\s*([^：:]+)[：:]\s*(.+)", text)
        if match:
            kind = cls._KINDS.get(match.group(1).strip())
            if kind is None:
                raise ValueError("unsupported relationship kind")
            return dict(
                operation=RelationshipOperation.ADD,
                kind=kind,
                reason="相手が会話上の関係情報を明示したため確認を求める",
                **cls._content_options(match.group(2)),
            )
        match = re.fullmatch(r"関係訂正[：:]\s*([^：:\s]+)\s*[：:]\s*(.+)", text)
        if match:
            item = next((item for item in items if item.item_id == match.group(1)), None)
            if item is None:
                return dict(
                    operation=RelationshipOperation.CORRECT,
                    target_item_id=match.group(1),
                    kind=RelationshipInformationKind.PREFERENCE,
                    reason="相手が指定した関係情報の訂正を求めた",
                    **cls._content_options(match.group(2)),
                )
            options = cls._content_options(match.group(2))
            options.setdefault("scope_keywords", item.scope_keywords)
            options.setdefault("valid_until", item.valid_until)
            return dict(
                operation=RelationshipOperation.CORRECT,
                target_item_id=item.item_id,
                expected_version=item.version,
                kind=item.kind,
                reconfirmation_condition=item.reconfirmation_condition,
                reason="相手が関係情報の訂正を明示したため確認を求める",
                **options,
            )
        raise ValueError("unrecognized relationship command")

    @staticmethod
    def _content_options(text: str) -> dict[str, object]:
        parts = [part.strip() for part in text.split("|")]
        values: dict[str, object] = {"content": parts[0]}
        seen: set[str] = set()
        for part in parts[1:]:
            key, separator, value = part.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or key in seen:
                raise ValueError("malformed relationship option")
            seen.add(key)
            if key == "話題":
                values["scope_keywords"] = () if value == "全会話" else tuple(word.strip() for word in value.split(","))
            elif key == "期限":
                values["valid_until"] = None if value == "なし" else ExternalTime(datetime.fromisoformat(value))
            else:
                raise ValueError("unsupported relationship option")
        return values
