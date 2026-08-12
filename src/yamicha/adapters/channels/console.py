"""Minimal console UI for text input, status, and technical errors."""

from __future__ import annotations

from typing import TextIO

from yamicha.contracts import (
    DialogueOutput,
    ExternalTime,
    InputDisposition,
    InputProcessingOutcome,
    OutputReleaseStatus,
    RawTextInput,
    SourceVerification,
)


class ConsoleChannel:
    def __init__(self, output: TextIO) -> None:
        self._output = output

    def make_text_input(
        self,
        *,
        input_id: str,
        received_at: ExternalTime,
        text: str,
        source_id: str = "local-console",
    ) -> RawTextInput:
        return RawTextInput(
            input_id=input_id,
            received_at=received_at,
            source_id=source_id,
            content=text,
            source_verification=SourceVerification.VERIFIED,
        )

    def show_status(self, status: str) -> None:
        self._output.write(f"status: {status}\n")

    def show_outcome(self, outcome: InputProcessingOutcome) -> None:
        if outcome.disposition is InputDisposition.ACCEPTED:
            self._output.write(
                f"accepted: {outcome.correlation_id} (judgment pending)\n"
            )
        elif outcome.disposition is InputDisposition.DUPLICATE:
            self._output.write(f"duplicate: {outcome.correlation_id}\n")
        else:
            reason = outcome.rejection.reason if outcome.rejection else "unknown"
            self._output.write(
                f"error: {outcome.disposition.value}: {reason}\n"
            )

    def show_dialogue_output(self, output: DialogueOutput) -> None:
        if output.status is OutputReleaseStatus.RELEASED:
            self._output.write(f"yamicha: {output.text}\n")
        elif output.status is OutputReleaseStatus.BLOCKED:
            self._output.write(f"output blocked: {output.reason}\n")
