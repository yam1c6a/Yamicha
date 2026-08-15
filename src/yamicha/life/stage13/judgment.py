"""Judgment-owned explicit conversation-memory candidate generation."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from uuid import uuid4

from yamicha.contracts import (
    AuxiliaryIntelligenceProposal,
    AuxiliaryIntelligenceResult,
    ContentTrust,
    ConversationMemoryCandidate,
    ConversationMemoryForgetRequest,
    ConversationMemoryInterpretation,
    ConversationMemoryInterpretationAction,
    ConversationMemorySubject,
    ConversationMemoryWindow,
    DialogueContextWindow,
    DialogueSpeaker,
    DialogueTurn,
    ExternalTime,
    InformationCertainty,
    IntelligenceAdoption,
    IntelligenceAdoptionStatus,
    IntelligenceConstraints,
    IntelligencePurpose,
    SensoryEvent,
)
from yamicha.life.stage10 import Stage10Judgment


class Stage13Judgment(Stage10Judgment):
    _PREFIX_PATTERNS = (
        re.compile(r"^(?:覚えて(?:おいて)?|記憶して)(?:ください)?[：:、,\s]+(.+)$"),
    )
    _SUFFIX_PATTERNS = (
        re.compile(r"^(.+?)だと覚えておいて(?:ください)?[。.!！]?$"),
        re.compile(r"^(.+?)と覚えておいて(?:ください)?[。.!！]?$"),
        re.compile(r"^(.+?)を覚えておいて(?:ください)?[。.!！]?$"),
        re.compile(r"^(.+?)を記憶して(?:ください)?[。.!！]?$"),
    )
    _SELF_PREFERENCE_PATTERNS = (
        re.compile(
            r"^(?:私は|わたしは|僕は|ぼくは|俺は)"
            r".+?(?:が|は)(?:好き|大好き|苦手|嫌い)(?:です)?[。.!！]?$"
        ),
    )
    _FOLLOW_UP_QUESTION_PATTERNS = (
        re.compile(
            r"^どんな種類の(?P<topic>.+?)(?:が|を)お好み(?:ですか)?[？?。.!！]?$"
        ),
        re.compile(
            r"^どんな(?P<topic>.+?)(?:が|を)好き(?:ですか)?[？?。.!！]?$"
        ),
    )
    _BARE_FORGET_PATTERNS = (
        re.compile(
            r"^(?:忘れて|それ(?:を)?忘れて|これ(?:を)?忘れて)"
            r"(?:ください)?[。.!！]?$"
        ),
    )
    _TARGETED_FORGET_PATTERNS = (
        re.compile(r"^(.+?)(?:のこと)?(?:を)?忘れて(?:ください)?[。.!！]?$"),
    )
    _INTERPRETATION_GENERIC_TERMS = (
        "大好き",
        "好き",
        "苦手",
        "嫌い",
        "特に",
        "とくに",
        "です",
        "だよ",
        "かな",
        "かも",
    )

    def __init__(
        self,
        *,
        conversation_memory_candidate_id_factory: Callable[[], str] | None = None,
        conversation_memory_forget_request_id_factory: Callable[[], str] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._conversation_memory_candidate_id_factory = (
            conversation_memory_candidate_id_factory or (lambda: str(uuid4()))
        )
        self._conversation_memory_candidate_ids: set[str] = set()
        self._conversation_memory_forget_request_id_factory = (
            conversation_memory_forget_request_id_factory or (lambda: str(uuid4()))
        )
        self._conversation_memory_forget_request_ids: set[str] = set()

    def propose_conversation_memory(
        self,
        event: SensoryEvent,
        context_turns: tuple[DialogueTurn, ...],
        interpretation: ConversationMemoryInterpretation | None = None,
    ) -> ConversationMemoryCandidate | None:
        if self._is_forget_text(event.meaning.normalized_text):
            return None
        retained = self._retention_meaning(event.meaning.normalized_text)
        provenance_turns: tuple[DialogueTurn, ...]
        certainty = InformationCertainty.CONFIRMED
        if retained is None:
            contextual = self._contextual_retention(event, context_turns)
            if contextual is not None:
                meaning, retention_reason, provenance_turns, certainty = contextual
            elif self._eligible_remember_interpretation(event, interpretation):
                assert interpretation is not None
                assert interpretation.meaning is not None
                meaning = interpretation.meaning
                retention_reason = (
                    "the bounded LLM interpretation proposed a self-memory candidate"
                )
                certainty = interpretation.certainty
                provenance_turns = tuple(
                    turn
                    for turn in context_turns
                    if turn.lifecycle_id == event.correlation_id
                )
            else:
                return None
        else:
            meaning, retention_reason = retained
            provenance_turns = tuple(
                turn
                for turn in context_turns
                if turn.lifecycle_id == event.correlation_id
            )
        human_turns = tuple(
            turn
            for turn in provenance_turns
            if turn.lifecycle_id == event.correlation_id
            and turn.speaker is DialogueSpeaker.HUMAN
        )
        if len(human_turns) != 1 or human_turns[0].source_reference != event.event_id:
            raise ValueError("conversation-memory provenance does not match the event")
        if not provenance_turns:
            raise ValueError("conversation-memory exchange provenance is invalid")
        candidate_id = self._conversation_memory_candidate_id_factory()
        if (
            not candidate_id.strip()
            or candidate_id in self._conversation_memory_candidate_ids
        ):
            raise ValueError("conversation-memory candidate ID must be unique")
        self._conversation_memory_candidate_ids.add(candidate_id)
        return ConversationMemoryCandidate(
            candidate_id=candidate_id,
            lifecycle_id=event.correlation_id,
            counterpart_id=event.source_id,
            version=1,
            meaning=meaning,
            retention_reason=retention_reason,
            source_turn_ids=tuple(turn.turn_id for turn in provenance_turns),
            source_references=self._candidate_source_references(
                provenance_turns,
                interpretation,
            ),
            created_at=event.received_at,
            certainty=certainty,
            content_trust=ContentTrust.UNTRUSTED,
            reevaluation_condition="counterpart correction or forgetting request",
        )

    def propose_conversation_memory_forgetting(
        self,
        event: SensoryEvent,
        window: ConversationMemoryWindow | None,
        interpretation: ConversationMemoryInterpretation | None = None,
    ) -> ConversationMemoryForgetRequest | None:
        text = event.meaning.normalized_text.strip()
        forgetting = self._is_forget_text(text)
        interpretation_forgetting = self._eligible_forget_interpretation(
            event,
            window,
            interpretation,
        )
        if (
            (not forgetting and not interpretation_forgetting)
            or window is None
            or not window.selections
        ):
            return None
        request_id = self._conversation_memory_forget_request_id_factory()
        if (
            not request_id.strip()
            or request_id in self._conversation_memory_forget_request_ids
        ):
            raise ValueError("conversation-memory forget request ID must be unique")
        self._conversation_memory_forget_request_ids.add(request_id)
        return ConversationMemoryForgetRequest(
            request_id=request_id,
            lifecycle_id=event.correlation_id,
            counterpart_id=event.source_id,
            target_items=tuple(
                (selection.memory_item_id, selection.memory_item_version)
                for selection in window.selections
            ),
            source_reference=event.event_id,
            reason=(
                "the counterpart requested logical forgetting"
                if forgetting
                else "the bounded LLM interpretation proposed logical forgetting"
            ),
            requested_at=event.received_at,
        )

    @classmethod
    def _is_forget_text(cls, text: str) -> bool:
        stripped = text.strip()
        return any(
            pattern.fullmatch(stripped)
            for pattern in (*cls._BARE_FORGET_PATTERNS, *cls._TARGETED_FORGET_PATTERNS)
        )

    def propose_memory_interpretation(
        self,
        *,
        lifecycle_id: str,
        model: str,
        input_text: str,
        input_source_reference: str,
        constraints: IntelligenceConstraints,
        proposed_at: ExternalTime,
        dialogue_context: DialogueContextWindow | None,
        conversation_memory: ConversationMemoryWindow | None,
    ) -> AuxiliaryIntelligenceProposal:
        interpretation_constraints = replace(
            constraints,
            output_format=(
                "json:{action,subject,meaning,target_text,certainty,"
                "needs_confirmation,evidence_quote}"
            ),
        )
        proposal = AuxiliaryIntelligenceProposal(
            proposal_id=self._required_id(
                self._intelligence_proposal_id_factory,
                "proposal",
            ),
            lifecycle_id=lifecycle_id,
            purpose=IntelligencePurpose.MEMORY_INTERPRETATION_CANDIDATE,
            model=model,
            input_text=input_text,
            input_source_reference=input_source_reference,
            constraints=interpretation_constraints,
            proposed_at=proposed_at,
            dialogue_context=dialogue_context,
            conversation_memory=conversation_memory,
        )
        self._intelligence_proposals[proposal.proposal_id] = proposal
        return proposal

    def interpret_adopted_memory_candidate(
        self,
        *,
        event: SensoryEvent,
        result: AuxiliaryIntelligenceResult,
        adoption: IntelligenceAdoption,
    ) -> ConversationMemoryInterpretation | None:
        candidate = result.candidate
        if (
            result.purpose
            is not IntelligencePurpose.MEMORY_INTERPRETATION_CANDIDATE
            or adoption.status is not IntelligenceAdoptionStatus.ADOPTED
            or candidate is None
            or adoption.candidate_id != candidate.candidate_id
            or result.lifecycle_id != event.correlation_id
        ):
            return None
        try:
            decoded = json.loads(candidate.text)
            action = ConversationMemoryInterpretationAction(decoded["action"])
            subject = ConversationMemorySubject(decoded["subject"])
            certainty = InformationCertainty(decoded["certainty"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        evidence = decoded["evidence_quote"]
        if evidence is not None and evidence not in event.meaning.normalized_text:
            return None
        try:
            return ConversationMemoryInterpretation(
                interpretation_id=candidate.candidate_id,
                lifecycle_id=event.correlation_id,
                action=action,
                subject=subject,
                meaning=decoded["meaning"],
                target_text=decoded["target_text"],
                certainty=certainty,
                needs_confirmation=decoded["needs_confirmation"],
                evidence_quote=evidence,
                source_request_id=result.request_id,
                source_candidate_id=candidate.candidate_id,
                model=candidate.model,
                interpreted_at=result.completed_at,
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def should_request_memory_interpretation(text: str) -> bool:
        stripped = text.strip()
        return bool(stripped) and "?" not in stripped and "？" not in stripped

    @classmethod
    def _eligible_remember_interpretation(
        cls,
        event: SensoryEvent,
        interpretation: ConversationMemoryInterpretation | None,
    ) -> bool:
        if (
            interpretation is None
            or interpretation.action
            is not ConversationMemoryInterpretationAction.REMEMBER
            or interpretation.subject is not ConversationMemorySubject.SELF
            or interpretation.needs_confirmation
            or interpretation.certainty is InformationCertainty.UNKNOWN
            or interpretation.meaning is None
            or interpretation.evidence_quote is None
        ):
            return False
        evidence_fragments = cls._meaning_fragments(
            interpretation.evidence_quote
        )
        meaning_fragments = cls._meaning_fragments(interpretation.meaning)
        return bool(evidence_fragments & meaning_fragments) and (
            interpretation.evidence_quote in event.meaning.normalized_text
        )

    @classmethod
    def _eligible_forget_interpretation(
        cls,
        event: SensoryEvent,
        window: ConversationMemoryWindow | None,
        interpretation: ConversationMemoryInterpretation | None,
    ) -> bool:
        if (
            interpretation is None
            or interpretation.action
            is not ConversationMemoryInterpretationAction.FORGET
            or interpretation.subject is not ConversationMemorySubject.SELF
            or interpretation.needs_confirmation
            or interpretation.certainty is InformationCertainty.UNKNOWN
            or interpretation.target_text is None
            or interpretation.evidence_quote is None
            or interpretation.evidence_quote not in event.meaning.normalized_text
            or window is None
            or len(window.selections) != 1
        ):
            return False
        target_fragments = cls._fragments(
            cls._normalized(interpretation.target_text)
        )
        evidence_fragments = cls._fragments(
            cls._normalized(interpretation.evidence_quote)
        )
        memory_fragments = cls._fragments(
            cls._normalized(window.selections[0].meaning)
        )
        return bool(
            target_fragments & evidence_fragments
            and target_fragments & memory_fragments
        )

    @staticmethod
    def _candidate_source_references(
        provenance_turns: tuple[DialogueTurn, ...],
        interpretation: ConversationMemoryInterpretation | None,
    ) -> tuple[str, ...]:
        references = tuple(turn.source_reference for turn in provenance_turns)
        if interpretation is None:
            return references
        return (*references, interpretation.source_request_id)

    @staticmethod
    def _normalized(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return "".join(character for character in normalized if character.isalnum())

    @staticmethod
    def _fragments(value: str) -> set[str]:
        if len(value) < 2:
            return set()
        return {
            value[index : index + size]
            for size in (2, 3)
            for index in range(len(value) - size + 1)
        }

    @classmethod
    def _meaning_fragments(cls, value: str) -> set[str]:
        normalized = cls._normalized(value)
        for term in cls._INTERPRETATION_GENERIC_TERMS:
            normalized = normalized.replace(cls._normalized(term), "")
        return cls._fragments(normalized)

    @classmethod
    def _contextual_retention(
        cls,
        event: SensoryEvent,
        context_turns: tuple[DialogueTurn, ...],
    ) -> tuple[
        str,
        str,
        tuple[DialogueTurn, ...],
        InformationCertainty,
    ] | None:
        current_index = next(
            (
                index
                for index, turn in enumerate(context_turns)
                if turn.speaker is DialogueSpeaker.HUMAN
                and turn.lifecycle_id == event.correlation_id
                and turn.source_reference == event.event_id
            ),
            None,
        )
        if current_index is None or current_index == 0:
            return None
        question = context_turns[current_index - 1]
        if question.speaker is not DialogueSpeaker.YAMICHA:
            return None
        topic = None
        for pattern in cls._FOLLOW_UP_QUESTION_PATTERNS:
            match = pattern.fullmatch(question.text.strip())
            if match is not None:
                topic = match.group("topic").strip(" \t\r\n、,。.!！")
                break
        if not topic:
            return None
        answer = event.meaning.normalized_text.strip(" \t\r\n、,。.!！")
        if len(answer) > 40 or "？" in answer or "?" in answer:
            return None
        uncertain = bool(re.search(r"(?:かな|かも)$", answer))
        answer = re.sub(r"(?:かな|かも|です)$", "", answer).strip()
        if not answer:
            return None
        current_human = context_turns[current_index]
        return (
            f"好きな{topic}の種類は{answer}",
            "the counterpart answered Yamicha's immediately preceding preference question",
            (question, current_human),
            InformationCertainty.INFERRED if uncertain else InformationCertainty.CONFIRMED,
        )

    @classmethod
    def _retention_meaning(cls, text: str) -> tuple[str, str] | None:
        stripped = text.strip()
        for pattern in (*cls._PREFIX_PATTERNS, *cls._SUFFIX_PATTERNS):
            match = pattern.fullmatch(stripped)
            if match is not None:
                meaning = match.group(1).strip(" \t\r\n、,。.!！")
                if meaning:
                    return (
                        meaning,
                        "the counterpart explicitly requested long-term retention",
                    )
                return None
        for pattern in cls._SELF_PREFERENCE_PATTERNS:
            if pattern.fullmatch(stripped) is not None:
                return (
                    stripped.strip(" \t\r\n、,。.!！"),
                    "the counterpart made an unambiguous first-person preference statement",
                )
        return None
