from __future__ import annotations

import sys
import unittest
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.body.runtime import (  # noqa: E402
    ClockDiscontinuityError,
    RuntimeStatus,
)
from yamicha.bootstrap import make_stage2_system  # noqa: E402
from yamicha.contracts import (  # noqa: E402
    ClockObservation,
    CycleStatus,
    DecisionBasis,
    DecisionDirection,
    ElapsedTime,
    ExecutionOpportunity,
    ExecutionOpportunityKind,
    ExternalTime,
    InternalTime,
    MonotonicTime,
    OperatingState,
)


class SequenceClock:
    def __init__(self, *observations: ClockObservation) -> None:
        self._observations = list(observations)

    def observe(self) -> ClockObservation:
        if not self._observations:
            raise AssertionError("test clock has no observation left")
        return self._observations.pop(0)


def observation(second: int, monotonic: float) -> ClockObservation:
    return ClockObservation(
        external=ExternalTime(datetime(2026, 8, 12, 12, 0, second, tzinfo=UTC)),
        monotonic=MonotonicTime(monotonic),
    )


class Stage2LifecycleTest(unittest.TestCase):
    def test_external_input_is_not_required_for_a_complete_cycle(self) -> None:
        system = make_stage2_system(
            clock=SequenceClock(observation(0, 100.0)),
            id_factory=lambda: "opportunity-001",
            correlation_id_factory=lambda: "cycle-001",
        )

        outcome = system.run_cycle()

        self.assertEqual(outcome.status, CycleStatus.COMPLETED)
        self.assertEqual(outcome.opportunity.kind, ExecutionOpportunityKind.STARTUP)
        self.assertEqual(outcome.decision.direction, DecisionDirection.NO_ACTION)
        self.assertEqual(outcome.decision.basis, DecisionBasis.STATE_SNAPSHOT)
        self.assertEqual(outcome.final_state.operating_state, OperatingState.WAITING)
        self.assertEqual(system.runtime.status, RuntimeStatus.WAITING)
        self.assertEqual(outcome.external_effect_count, 0)
        self.assertEqual(outcome.memory_update_count, 0)

    def test_periodic_cycle_advances_internal_time_by_monotonic_elapsed(self) -> None:
        identifiers = iter(("opportunity-001", "opportunity-002"))
        correlations = iter(("cycle-001", "cycle-002"))
        system = make_stage2_system(
            clock=SequenceClock(
                observation(0, 100.0),
                observation(5, 105.0),
            ),
            id_factory=lambda: next(identifiers),
            correlation_id_factory=lambda: next(correlations),
        )

        first = system.run_cycle()
        second = system.run_cycle()

        self.assertEqual(
            first.observed_state.internal_time.elapsed_since_start,
            timedelta(0),
        )
        self.assertEqual(
            second.opportunity.elapsed_since_previous,
            ElapsedTime(timedelta(seconds=5)),
        )
        self.assertEqual(
            second.observed_state.internal_time.elapsed_since_start,
            timedelta(seconds=5),
        )
        self.assertEqual(second.opportunity.kind, ExecutionOpportunityKind.PERIODIC)

    def test_time_concepts_are_distinct_types(self) -> None:
        external = ExternalTime(datetime(2026, 8, 12, tzinfo=UTC))
        elapsed = ElapsedTime(timedelta(seconds=3))
        internal = InternalTime.initial(external).advance(elapsed, external)

        self.assertIsInstance(external, ExternalTime)
        self.assertIsInstance(elapsed, ElapsedTime)
        self.assertIsInstance(internal, InternalTime)
        self.assertNotEqual(type(external), type(elapsed))
        self.assertNotEqual(type(elapsed), type(internal))

    def test_execution_opportunity_has_no_desire_or_motivation_field(self) -> None:
        field_names = {field.name for field in fields(ExecutionOpportunity)}

        self.assertNotIn("desire", field_names)
        self.assertNotIn("motivation", field_names)
        self.assertNotIn("reason", field_names)

    def test_no_action_reason_comes_from_state_not_opportunity(self) -> None:
        system = make_stage2_system(
            clock=SequenceClock(observation(0, 100.0)),
            id_factory=lambda: "opportunity-001",
            correlation_id_factory=lambda: "cycle-001",
        )

        outcome = system.run_cycle()

        self.assertEqual(outcome.proposal.basis, DecisionBasis.STATE_SNAPSHOT)
        self.assertNotIn("opportunity", outcome.proposal.reason.lower())
        self.assertNotIn(
            outcome.opportunity.opportunity_id,
            outcome.proposal.reason,
        )

    def test_clock_discontinuity_is_not_silently_accepted(self) -> None:
        system = make_stage2_system(
            clock=SequenceClock(
                observation(0, 100.0),
                observation(30, 101.0),
            ),
            id_factory=iter(("opportunity-001", "opportunity-002")).__next__,
            correlation_id_factory=iter(("cycle-001", "cycle-002")).__next__,
        )
        system.run_cycle()

        with self.assertRaises(ClockDiscontinuityError):
            system.run_cycle()
        self.assertEqual(system.runtime.status, RuntimeStatus.WAITING)

    def test_start_wait_and_stop_are_distinct_transitions(self) -> None:
        system = make_stage2_system(
            clock=SequenceClock(observation(0, 100.0)),
            id_factory=lambda: "opportunity-001",
            correlation_id_factory=lambda: "cycle-001",
        )

        system.start()
        self.assertEqual(system.runtime.status, RuntimeStatus.ACTIVE)
        outcome = system.run_cycle()
        self.assertEqual(outcome.final_state.operating_state, OperatingState.WAITING)
        system.stop()

        self.assertEqual(system.runtime.status, RuntimeStatus.STOPPED)
        self.assertEqual(system.state.operating_state, OperatingState.STOPPED)

    def test_technical_stop_before_first_cycle_is_not_sleep(self) -> None:
        system = make_stage2_system(
            clock=SequenceClock(observation(0, 100.0)),
            id_factory=lambda: "opportunity-001",
            correlation_id_factory=lambda: "cycle-001",
        )

        system.start()
        system.stop()

        self.assertEqual(system.runtime.status, RuntimeStatus.STOPPED)
        self.assertEqual(system.state.operating_state, OperatingState.STOPPED)
        self.assertNotEqual(system.state.operating_state.value, "sleeping")


if __name__ == "__main__":
    unittest.main()
