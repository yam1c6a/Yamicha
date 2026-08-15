"""Interactive console that runs the latest implemented dialogue path."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO
from uuid import uuid4

from yamicha.adapters.channels import ConsoleChannel
from yamicha.body.runtime import RuntimeStatus
from yamicha.contracts import ExternalTime, InputDisposition, ProtectionMode

from .stage6 import Stage6System
from .stage7 import Stage7System
from .stage8 import Stage8System
from .stage9 import Stage9System
from .stage10 import Stage10System
from .stage12 import Stage12System, make_stage12_system


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
    system = make_stage12_system(
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
