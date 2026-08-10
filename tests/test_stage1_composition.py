from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.bootstrap import make_stage1_composition  # noqa: E402
from yamicha.contracts import (  # noqa: E402
    MessageEnvelope,
    ResponsibilityCategory,
    ResponsibilityId,
    ResponsibilityPort,
    UnimplementedResponsibilityError,
)


class Stage1CompositionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.composition = make_stage1_composition()

    def test_all_nine_organs_and_boundaries_exist_exactly_once(self) -> None:
        identifiers = [
            port.definition.identifier
            for port in self.composition.responsibilities
        ]
        organs = [
            port
            for port in self.composition.responsibilities
            if port.definition.category is ResponsibilityCategory.ORGAN
        ]

        self.assertEqual(set(identifiers), set(ResponsibilityId))
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(len(organs), 9)
        self.assertTrue(self.composition.subject_is_collective)
        self.assertTrue(
            all(
                isinstance(port, ResponsibilityPort)
                for port in self.composition.responsibilities
            )
        )

    def test_authority_is_not_assigned_to_body_or_auxiliary_intelligence(self) -> None:
        non_deciders = (
            self.composition.runtime,
            self.composition.protection_boundary,
            self.composition.external_effect_gate,
            self.composition.auxiliary_intelligence,
        )
        for port in non_deciders:
            with self.subTest(port=port.definition.identifier):
                authority = port.definition.authority
                self.assertFalse(authority.represents_subject)
                self.assertFalse(authority.may_propose_direction)
                self.assertFalse(authority.may_finalize_direction)

    def test_judgment_proposes_and_only_core_finalizes(self) -> None:
        proposers = [
            port.definition.identifier
            for port in self.composition.responsibilities
            if port.definition.authority.may_propose_direction
        ]
        finalizers = [
            port.definition.identifier
            for port in self.composition.responsibilities
            if port.definition.authority.may_finalize_direction
        ]

        self.assertEqual(proposers, [ResponsibilityId.JUDGMENT])
        self.assertEqual(finalizers, [ResponsibilityId.CORE])
        self.assertFalse(
            self.composition.core.definition.authority.may_own_other_responsibility_processing
        )

    def test_stubs_do_not_silently_implement_future_behavior(self) -> None:
        timestamp = datetime(2026, 8, 10, tzinfo=UTC)
        message = MessageEnvelope(
            message_id="message-001",
            correlation_id="cycle-001",
            occurred_at=timestamp,
            received_at=timestamp,
            source="test",
            type="test.event",
            payload={},
            schema_version="1",
        )
        for port in self.composition.responsibilities:
            with self.subTest(port=port.definition.identifier):
                with self.assertRaises(UnimplementedResponsibilityError):
                    port.handle(message)
