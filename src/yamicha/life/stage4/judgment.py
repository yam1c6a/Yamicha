"""Rule-based minimal judgment that does not require external intelligence."""

from __future__ import annotations

from yamicha.contracts import (
    DecisionCandidate,
    DecisionDirection,
    JudgmentContext,
    JudgmentResult,
)
from yamicha.life.ports import JUDGMENT_DEFINITION
from yamicha.life.stage2 import Stage2Judgment


class Stage4Judgment(Stage2Judgment):
    definition = JUDGMENT_DEFINITION

    _WAIT_MARKERS = ("待って", "あとで")
    _NO_ACTION_MARKERS = ("何もしない", "反応しない")
    _EXTERNAL_EFFECT_MARKERS = ("送って", "削除して", "公開して", "変更して")

    def evaluate(self, context: JudgmentContext) -> JudgmentResult:
        selected, reason, uncertainties, conditions = self._select(context)
        candidates = tuple(
            DecisionCandidate(
                direction=direction,
                selected=direction is selected,
                acceptance_reasons=(reason,) if direction is selected else (),
                rejection_reasons=(
                    f"{selected.value} has precedence for the current materials",
                )
                if direction is not selected
                else (),
                uncertainties=uncertainties if direction is selected else (),
                confirmation_conditions=(
                    conditions if direction is selected else ()
                ),
            )
            for direction in DecisionDirection
        )
        return JudgmentResult(
            lifecycle_id=context.lifecycle_id,
            candidates=candidates,
            selected_direction=selected,
            uncertainties=uncertainties,
            material_versions=context.material_versions,
            auxiliary_intelligence_used=False,
        )

    def _select(
        self,
        context: JudgmentContext,
    ) -> tuple[DecisionDirection, str, tuple[str, ...], tuple[str, ...]]:
        missing = []
        if not context.state.available:
            missing.append("state")
        if not context.memory.available:
            missing.append("memory")
        if not context.relationship.available:
            missing.append("relationship")
        if not context.relationship.counterpart_known:
            missing.append("known counterpart")
        if not context.boundary.input_validated:
            missing.append("validated boundary")
        if missing:
            names = ", ".join(missing)
            uncertainty = f"required judgment material is missing: {names}"
            return (
                DecisionDirection.HOLD,
                "important material is insufficient, so no intent is inferred",
                (uncertainty,),
                (),
            )

        if context.relationship.boundary_violation:
            return (
                DecisionDirection.REFUSE,
                "relationship material reports a boundary violation",
                context.relationship.boundary_reasons,
                (),
            )

        text = context.event.meaning.normalized_text
        if any(marker in text for marker in self._WAIT_MARKERS):
            return (
                DecisionDirection.WAIT,
                "the received text explicitly asks to wait",
                (),
                (),
            )
        if any(marker in text for marker in self._NO_ACTION_MARKERS):
            return (
                DecisionDirection.NO_ACTION,
                "the received text explicitly asks for no action",
                (),
                (),
            )
        if any(marker in text for marker in self._EXTERNAL_EFFECT_MARKERS):
            return (
                DecisionDirection.CONFIRM,
                "the received text may require an external effect",
                ("target, authority, and effect are not yet confirmed",),
                ("confirm target, authority, and intended external effect",),
            )
        if context.memory.confirmed_experience_references:
            references = ", ".join(
                context.memory.confirmed_experience_references
            )
            return (
                DecisionDirection.RESPOND,
                (
                    "confirmed experience material was considered before "
                    f"responding: {references}"
                ),
                (),
                (),
            )
        return (
            DecisionDirection.RESPOND,
            "materials permit an internal decision to respond",
            (),
            (),
        )
