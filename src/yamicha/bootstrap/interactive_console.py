"""Interactive console that runs the latest implemented dialogue path."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO
from uuid import uuid4

from yamicha.adapters.channels import ConsoleChannel
from yamicha.body.runtime import RuntimeStatus
from yamicha.contracts import (
    ConversationMemoryOperationKind,
    ConversationMemoryOperationStatus,
    ExternalTime,
    InputDisposition,
    ProtectionMode,
    RelationshipChangeStatus,
    RelationshipInformationStatus,
    RelationshipOperation,
)

from .stage6 import Stage6System
from .stage7 import Stage7System
from .stage8 import Stage8System
from .stage9 import Stage9System
from .stage10 import Stage10System
from .stage12 import Stage12System
from .stage13 import Stage13System
from .stage14 import Stage14System
from .stage15 import Stage15System, make_stage15_system


class InteractiveConsole:
    def __init__(
        self,
        *,
        input_stream: TextIO,
        output_stream: TextIO,
        system: (
            Stage6System
            | Stage7System
            | Stage8System
            | Stage9System
            | Stage10System
            | Stage12System
            | Stage13System
            | Stage14System
            | Stage15System
        ),
        input_id_factory: Callable[[], str] | None = None,
        external_time_factory: Callable[[], ExternalTime] | None = None,
        source_id: str = "local-console",
    ) -> None:
        if not source_id.strip():
            raise ValueError("console source ID must not be empty")
        self._input = input_stream
        self._output = output_stream
        self._system = system
        self._channel = ConsoleChannel(output_stream)
        self._input_id_factory = input_id_factory or (lambda: str(uuid4()))
        self._external_time_factory = external_time_factory or (
            lambda: ExternalTime(datetime.now(UTC))
        )
        self._source_id = source_id

    def run(self) -> int:
        stage_label = getattr(self._system, "stage_label", 6)
        self._output.write(f"Yamicha 対話コンソール（段階{stage_label}）\n")
        if isinstance(self._system, Stage8System):
            protection_label = (
                "保護中（通常の入力・出力・永続化を停止）"
                if self._system.protection_boundary.mode
                is ProtectionMode.PROTECTED
                else "通常"
            )
            self._output.write(f"状態: {protection_label}\n")
        self._output.write("終了するには /quit を入力してください。\n")
        if isinstance(self._system, Stage12System):
            self._output.write("新しい会話を始めるには /new を入力してください。\n")
        if isinstance(self._system, Stage14System):
            self._output.write("記憶を確認するには /memory を入力してください。\n")
        if isinstance(self._system, Stage15System):
            self._output.write(
                "関係情報は /relationship、登録は「関係：希望：内容」、"
                "確認は「関係確認」で行えます。\n"
            )
        try:
            while True:
                self._output.write("you> ")
                self._output.flush()
                line = self._input.readline()
                if line == "":
                    break
                text = line.rstrip("\r\n")
                if text.strip() == "/quit":
                    break
                if not text.strip():
                    continue
                if text.strip() == "/new" and isinstance(
                    self._system,
                    Stage12System,
                ):
                    self._system.start_new_dialogue_context(
                        self._external_time_factory()
                    )
                    self._output.write("新しい会話を開始しました。\n")
                    self._output.flush()
                    continue
                if text.strip() in {"/memory", "/memory all"} and isinstance(
                    self._system,
                    Stage14System,
                ):
                    self._show_memories(include_inactive=text.strip().endswith(" all"))
                    continue
                if text.strip() in {"/relationship", "/relationship all"} and isinstance(
                    self._system, Stage15System,
                ):
                    self._show_relationship_information(
                        include_inactive=text.strip().endswith(" all")
                    )
                    continue
                raw = self._channel.make_text_input(
                    input_id=self._required_input_id(),
                    received_at=self._external_time_factory(),
                    text=text,
                    source_id=self._source_id,
                )
                outcome = self._system.receive_text(raw)
                if outcome.dialogue_output is not None:
                    self._channel.show_dialogue_output(outcome.dialogue_output)
                elif outcome.disposition is not InputDisposition.ACCEPTED:
                    self._channel.show_outcome(outcome)
                if isinstance(self._system, Stage14System):
                    self._show_governance_result(outcome)
                if isinstance(self._system, Stage15System):
                    self._show_relationship_result(outcome)
                self._output.flush()
        finally:
            self._stop_started_system()
        self._output.write("終了しました。\n")
        self._output.flush()
        return 0

    def _required_input_id(self) -> str:
        input_id = self._input_id_factory()
        if not input_id.strip():
            raise ValueError("console input ID must not be empty")
        return input_id

    def _show_memories(self, *, include_inactive: bool) -> None:
        if not self._information_display_allowed():
            return
        items = self._system.list_conversation_memories(
            self._source_id,
            include_inactive=include_inactive,
        )
        if not items:
            self._output.write("保存中の記憶はありません。\n")
        for index, item in enumerate(items, start=1):
            state = "有効" if item.active else "無効"
            self._output.write(
                f"{index}. [{state}/v{item.version}] {item.meaning}\n"
            )
        self._output.flush()

    def _show_governance_result(self, outcome) -> None:
        record = getattr(outcome, "conversation_memory_governance_record", None)
        if (
            record is not None
            or getattr(outcome, "conversation_memory_pending_confirmation", None) is not None
        ) and not self._information_display_allowed():
            return
        if record is None:
            if getattr(
                outcome,
                "conversation_memory_pending_confirmation",
                None,
            ) is not None:
                self._output.write(
                    "物理削除の確認待ちです。「はい」または「いいえ」と"
                    "入力してください。\n"
                )
            return
        if record.status is ConversationMemoryOperationStatus.CONFIRMATION_REQUIRED:
            if record.operation is ConversationMemoryOperationKind.PHYSICAL_DELETE:
                pending = getattr(
                    outcome,
                    "conversation_memory_pending_confirmation",
                    None,
                )
                count = 0 if pending is None else len(pending.request.target_items)
                self._output.write(
                    f"{count}件の物理削除は元に戻せません。実行する場合は「はい」、"
                    "取り消す場合は「いいえ」と入力してください。\n"
                )
            else:
                self._output.write("対象を一つに絞って指定してください。\n")
        elif record.status is ConversationMemoryOperationStatus.NOT_FOUND:
            self._output.write("対象となる記憶は見つかりませんでした。\n")
        elif record.status is ConversationMemoryOperationStatus.CANCELLED:
            self._output.write("記憶の物理削除を取り消しました。\n")
        elif record.status is ConversationMemoryOperationStatus.PARTIAL:
            self._output.write(
                "記憶内容は通常利用から除外しましたが、古い保存世代の削除を"
                "確認できませんでした。\n"
            )
        elif record.operation is ConversationMemoryOperationKind.CORRECTION:
            self._output.write("記憶を訂正しました。\n")
        elif record.operation is ConversationMemoryOperationKind.PHYSICAL_DELETE:
            self._output.write("記憶内容を物理削除しました。\n")
        elif record.operation is ConversationMemoryOperationKind.REEVALUATION:
            self._output.write("記憶を再評価しました。\n")
        elif record.operation is ConversationMemoryOperationKind.DEACTIVATION:
            self._output.write("記憶を非活性化しました。\n")
        elif record.operation is ConversationMemoryOperationKind.LOGICAL_FORGET:
            self._output.write("記憶を論理的に忘れました。\n")

    def _show_relationship_information(self, *, include_inactive: bool) -> None:
        if not self._information_display_allowed():
            return
        at = self._external_time_factory()
        items = self._system.list_relationship_information(
            self._source_id, include_inactive=include_inactive,
        )
        if not include_inactive:
            items = tuple(item for item in items if item.is_effective_at(at))
        if not items:
            self._output.write("有効な関係情報はありません。\n")
        labels = {
            "preference": "希望", "agreement": "合意", "consideration": "配慮",
            "ongoing": "継続事項", "distance": "距離",
        }
        for item in items:
            state = (
                "撤回済み" if item.status is RelationshipInformationStatus.WITHDRAWN
                else "有効" if item.is_effective_at(at) else "期限外"
            )
            self._output.write(
                f"[{item.item_id} v{item.version}/{state}/{labels[item.kind.value]}] "
                f"{item.content}\n"
            )
        pending = self._system.relationship.pending_relationship_confirmation_for(self._source_id)
        if pending is not None:
            self._output.write(f"確認待ち: {pending.request.content}\n")
        self._output.write("訂正は「関係訂正：ID：内容」、撤回は「関係撤回：ID」です。\n")
        self._output.flush()

    def _information_display_allowed(self) -> bool:
        if self._system.protection_boundary.authorize_information_display(
            source_id=self._source_id,
        ):
            return True
        self._output.write("保護状態または出力権限により内容を表示できません。\n")
        self._output.flush()
        return False

    def _show_relationship_result(self, outcome) -> None:
        record = getattr(outcome, "relationship_change_record", None)
        if record is None:
            return
        if not self._information_display_allowed():
            return
        if record.status is RelationshipChangeStatus.CONFIRMATION_REQUIRED:
            pending = outcome.relationship_pending_confirmation
            if pending is not None:
                scope = "、".join(pending.request.scope_keywords) or "全会話"
                valid_until = (
                    "指定なし" if pending.request.valid_until is None
                    else pending.request.valid_until.value.isoformat()
                )
                self._output.write(
                    f"関係情報の確認待ち: {pending.request.content}\n"
                    f"適用する話題: {scope} / 有効期限: {valid_until}\n"
                    "この内容を会話へ反映する場合は「関係確認」、"
                    "取り消す場合は「関係取消」と入力してください。\n"
                )
        elif record.status is RelationshipChangeStatus.COMPLETED:
            if record.operation is RelationshipOperation.WITHDRAW:
                self._output.write("関係情報を撤回しました。\n")
            else:
                self._output.write("確認した関係情報を保存しました。\n")
        elif record.status is RelationshipChangeStatus.CANCELLED:
            self._output.write("関係情報の変更を取り消しました。\n")
        elif record.status is RelationshipChangeStatus.NOT_FOUND:
            self._output.write("対象の関係情報または確認待ちが見つかりませんでした。\n")
        elif record.status is RelationshipChangeStatus.STALE:
            self._output.write("対象の版または有効期限が変わったため、変更できませんでした。\n")
        else:
            self._output.write(f"関係情報の変更は反映されませんでした: {record.reason}\n")

    def _stop_started_system(self) -> None:
        if isinstance(self._system, (Stage7System, Stage8System)):
            self._system.shutdown()
            return
        if self._system.runtime.status not in {
            RuntimeStatus.NOT_STARTED,
            RuntimeStatus.STOPPED,
        }:
            self._system.stop()


def run_interactive_console(
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    persistence_path: str | Path = Path(".yamicha/yamicha.sqlite3"),
) -> int:
    source_id = "local-console"
    system = make_stage15_system(
        persistence_path=persistence_path,
        known_counterpart_id=source_id,
        capability_authority_id=source_id,
    )
    return InteractiveConsole(
        input_stream=input_stream,
        output_stream=output_stream,
        system=system,
        source_id=source_id,
    ).run()
