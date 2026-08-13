"""Transactional SQLite storage for stage-7 checkpoints."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from yamicha.contracts import (
    CapabilityExecutionRecord,
    CapabilityResult,
    CapabilityResultStatus,
    ExternalTime,
    InitializationKind,
    PersistenceIdentity,
    PersistenceOpenResult,
    PersistenceSnapshot,
    PreviousExit,
    ProtectionAuditKind,
    ProtectionAuditRecord,
    ProtectionDecision,
    ProtectionMode,
)

from .codec import decode_snapshot, encode_snapshot


class PersistenceError(RuntimeError):
    """Base error for an explicit persistence failure."""


class PersistenceMissingError(PersistenceError):
    pass


class PersistenceCorruptionError(PersistenceError):
    pass


class PersistenceConsistencyError(PersistenceError):
    pass


class PersistenceCommitError(PersistenceError):
    pass


class SQLitePersistenceStore:
    SCHEMA_VERSION = 1
    _REQUIRED_TABLES = {"metadata", "identity", "sessions", "checkpoints"}

    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self._connection = connection
        self._closed = False

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        configuration_version: str,
        subject_id_factory: Callable[[], str] | None = None,
        session_id_factory: Callable[[], str] | None = None,
        now_factory: Callable[[], ExternalTime] | None = None,
        require_existing: bool = False,
        upgrade_from_configuration_versions: tuple[str, ...] = (),
    ) -> tuple[SQLitePersistenceStore, PersistenceOpenResult]:
        database_path = Path(path)
        if require_existing and not database_path.is_file():
            raise PersistenceMissingError(
                f"required persistence database does not exist: {database_path}"
            )
        database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(database_path, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError as error:
            raise PersistenceCorruptionError(
                f"cannot open persistence database: {database_path}"
            ) from error
        store = cls(database_path, connection)
        try:
            result = store._initialize_and_open_session(
                configuration_version=configuration_version,
                subject_id_factory=subject_id_factory or (lambda: str(uuid4())),
                session_id_factory=session_id_factory or (lambda: str(uuid4())),
                now_factory=now_factory
                or (lambda: ExternalTime(datetime.now(UTC))),
                upgrade_from_configuration_versions=(
                    upgrade_from_configuration_versions
                ),
            )
        except Exception:
            store.close()
            raise
        return store, result

    @property
    def latest_sequence(self) -> int:
        self._ensure_open()
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM checkpoints"
        ).fetchone()
        return int(row["sequence"])

    def commit_snapshot(self, snapshot: PersistenceSnapshot) -> None:
        self._ensure_open()
        payload = encode_snapshot(snapshot)
        checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM checkpoints"
            ).fetchone()
            expected_sequence = int(row["sequence"]) + 1
            if snapshot.sequence != expected_sequence:
                raise PersistenceConsistencyError(
                    "snapshot sequence does not follow the committed checkpoint"
                )
            self._connection.execute(
                """
                INSERT INTO checkpoints(
                    sequence, snapshot_id, created_at, subject_id,
                    configuration_version, payload, checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.sequence,
                    snapshot.snapshot_id,
                    snapshot.created_at.value.isoformat(),
                    snapshot.subject_id,
                    snapshot.configuration_version,
                    payload,
                    checksum,
                ),
            )
            self._connection.execute(
                "UPDATE metadata SET value = '1' WHERE key = 'has_committed_checkpoint'"
            )
            self._connection.commit()
        except PersistenceConsistencyError:
            self._connection.rollback()
            raise
        except (sqlite3.DatabaseError, OSError) as error:
            self._connection.rollback()
            raise PersistenceCommitError("checkpoint transaction failed") from error

    def mark_normal_shutdown(self, session_id: str, ended_at: ExternalTime) -> None:
        self._ensure_open()
        try:
            cursor = self._connection.execute(
                """
                UPDATE sessions
                SET status = 'normal', ended_at = ?
                WHERE session_id = ? AND status = 'running'
                """,
                (ended_at.value.isoformat(), session_id),
            )
        except sqlite3.DatabaseError as error:
            raise PersistenceCommitError("normal shutdown marker failed") from error
        if cursor.rowcount != 1:
            raise PersistenceConsistencyError("persistence session is not running")

    def initialize_protection_storage(
        self,
        *,
        definition_version: str,
        initialized_at: ExternalTime,
    ) -> None:
        self._ensure_open()
        if not definition_version.strip():
            raise ValueError("protection definition version must not be empty")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS protection_control(
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                mode TEXT NOT NULL CHECK(mode IN ('normal', 'protected')),
                definition_version TEXT NOT NULL,
                activation_id TEXT,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL,
                control_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS protection_execution_reservations(
                observation_id TEXT PRIMARY KEY,
                reservation_id TEXT NOT NULL UNIQUE,
                operation_id TEXT NOT NULL,
                reserved_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS protection_audit(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id TEXT NOT NULL UNIQUE,
                occurred_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                actor TEXT NOT NULL,
                target TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                correlation_id TEXT,
                previous_hash TEXT NOT NULL,
                record_hash TEXT NOT NULL UNIQUE
            );
            """
        )
        row = self._connection.execute(
            "SELECT definition_version FROM protection_control WHERE singleton = 1"
        ).fetchone()
        if row is None:
            control_hash = self._protection_control_hash(
                ProtectionMode.NORMAL,
                definition_version,
                None,
                1,
            )
            self._connection.execute(
                """
                INSERT INTO protection_control(
                    singleton, mode, definition_version, activation_id,
                    updated_at, version, control_hash
                ) VALUES (1, 'normal', ?, NULL, ?, 1, ?)
                """,
                (
                    definition_version,
                    initialized_at.value.isoformat(),
                    control_hash,
                ),
            )
        elif row["definition_version"] != definition_version:
            raise PersistenceConsistencyError(
                "stored fixed protection definition version does not match"
            )

    def protection_control_state(self) -> tuple[ProtectionMode, str, str | None, int]:
        self._ensure_open()
        row = self._connection.execute(
            """
            SELECT mode, definition_version, activation_id, version, control_hash
            FROM protection_control WHERE singleton = 1
            """
        ).fetchone()
        if row is None:
            raise PersistenceConsistencyError("protection control is not initialized")
        mode = ProtectionMode(row["mode"])
        definition_version = str(row["definition_version"])
        activation_id = (
            None if row["activation_id"] is None else str(row["activation_id"])
        )
        version = int(row["version"])
        if row["control_hash"] != self._protection_control_hash(
            mode,
            definition_version,
            activation_id,
            version,
        ):
            raise PersistenceCorruptionError("protection control hash does not match")
        return (mode, definition_version, activation_id, version)

    def reserve_protection_execution(
        self,
        *,
        observation_id: str,
        reservation_id: str,
        operation_id: str,
        reserved_at: ExternalTime,
    ) -> bool:
        self._ensure_open()
        try:
            self._connection.execute(
                """
                INSERT INTO protection_execution_reservations(
                    observation_id, reservation_id, operation_id, reserved_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    observation_id,
                    reservation_id,
                    operation_id,
                    reserved_at.value.isoformat(),
                ),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def has_protection_reservation(
        self,
        *,
        observation_id: str,
        reservation_id: str,
        operation_id: str,
    ) -> bool:
        self._ensure_open()
        row = self._connection.execute(
            """
            SELECT 1 FROM protection_execution_reservations
            WHERE observation_id = ? AND reservation_id = ? AND operation_id = ?
            """,
            (observation_id, reservation_id, operation_id),
        ).fetchone()
        return row is not None

    def append_protection_audit(self, record: ProtectionAuditRecord) -> None:
        self._ensure_open()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._insert_protection_audit(record)
            self._connection.commit()
        except sqlite3.DatabaseError as error:
            self._connection.rollback()
            raise PersistenceCommitError("protection audit write failed") from error

    def activate_protection_atomic(
        self,
        *,
        observation_id: str,
        reservation_id: str,
        operation_id: str,
        activation_id: str,
        activated_at: ExternalTime,
        audit: ProtectionAuditRecord,
    ) -> None:
        self._ensure_open()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            reservation = self._connection.execute(
                """
                SELECT operation_id FROM protection_execution_reservations
                WHERE observation_id = ? AND reservation_id = ?
                """,
                (observation_id, reservation_id),
            ).fetchone()
            if reservation is None or reservation["operation_id"] != operation_id:
                raise PersistenceConsistencyError(
                    "fixed protection execution has no matching reservation"
                )
            control = self._connection.execute(
                """
                SELECT definition_version, version FROM protection_control
                WHERE singleton = 1 AND mode = 'normal'
                """
            ).fetchone()
            if control is None:
                raise PersistenceConsistencyError(
                    "protection transition requires normal mode"
                )
            next_version = int(control["version"]) + 1
            control_hash = self._protection_control_hash(
                ProtectionMode.PROTECTED,
                str(control["definition_version"]),
                activation_id,
                next_version,
            )
            cursor = self._connection.execute(
                """
                UPDATE protection_control
                SET mode = 'protected', activation_id = ?, updated_at = ?,
                    version = ?, control_hash = ?
                WHERE singleton = 1 AND mode = 'normal' AND version = ?
                """,
                (
                    activation_id,
                    activated_at.value.isoformat(),
                    next_version,
                    control_hash,
                    int(control["version"]),
                ),
            )
            if cursor.rowcount != 1:
                raise PersistenceConsistencyError(
                    "protection transition requires normal mode"
                )
            self._insert_protection_audit(audit)
            self._connection.commit()
        except PersistenceConsistencyError:
            self._connection.rollback()
            raise
        except sqlite3.DatabaseError as error:
            self._connection.rollback()
            raise PersistenceCommitError("atomic protection transition failed") from error

    def release_protection_atomic(
        self,
        *,
        activation_id: str,
        released_at: ExternalTime,
        audit: ProtectionAuditRecord,
    ) -> None:
        self._ensure_open()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            control = self._connection.execute(
                """
                SELECT definition_version, version FROM protection_control
                WHERE singleton = 1 AND mode = 'protected' AND activation_id = ?
                """,
                (activation_id,),
            ).fetchone()
            if control is None:
                raise PersistenceConsistencyError(
                    "release does not match the active protection transition"
                )
            next_version = int(control["version"]) + 1
            control_hash = self._protection_control_hash(
                ProtectionMode.NORMAL,
                str(control["definition_version"]),
                None,
                next_version,
            )
            cursor = self._connection.execute(
                """
                UPDATE protection_control
                SET mode = 'normal', activation_id = NULL, updated_at = ?,
                    version = ?, control_hash = ?
                WHERE singleton = 1 AND mode = 'protected' AND activation_id = ?
                    AND version = ?
                """,
                (
                    released_at.value.isoformat(),
                    next_version,
                    control_hash,
                    activation_id,
                    int(control["version"]),
                ),
            )
            if cursor.rowcount != 1:
                raise PersistenceConsistencyError(
                    "release does not match the active protection transition"
                )
            self._insert_protection_audit(audit)
            self._connection.commit()
        except PersistenceConsistencyError:
            self._connection.rollback()
            raise
        except sqlite3.DatabaseError as error:
            self._connection.rollback()
            raise PersistenceCommitError("atomic protection release failed") from error

    def protection_audit_records(self) -> tuple[ProtectionAuditRecord, ...]:
        self._ensure_open()
        rows = self._connection.execute(
            "SELECT * FROM protection_audit ORDER BY sequence"
        ).fetchall()
        previous_hash = "genesis"
        records: list[ProtectionAuditRecord] = []
        for row in rows:
            record = ProtectionAuditRecord(
                record_id=str(row["record_id"]),
                occurred_at=ExternalTime(datetime.fromisoformat(row["occurred_at"])),
                kind=ProtectionAuditKind(row["kind"]),
                actor=str(row["actor"]),
                target=str(row["target"]),
                decision=ProtectionDecision(row["decision"]),
                reason=str(row["reason"]),
                correlation_id=(
                    None
                    if row["correlation_id"] is None
                    else str(row["correlation_id"])
                ),
            )
            expected = self._audit_hash(record, previous_hash)
            if row["previous_hash"] != previous_hash or row["record_hash"] != expected:
                raise PersistenceCorruptionError("protection audit chain is broken")
            records.append(record)
            previous_hash = expected
        return tuple(records)

    def initialize_capability_storage(self) -> None:
        self._ensure_open()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS capability_executions(
                idempotency_key TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('reserved', 'completed')),
                result_status TEXT,
                reserved_at TEXT NOT NULL,
                completed_at TEXT,
                record_hash TEXT NOT NULL
            )
            """
        )

    def reserve_capability_execution(
        self,
        *,
        idempotency_key: str,
        request_id: str,
        request_fingerprint: str,
        reserved_at: ExternalTime,
    ) -> bool:
        self._ensure_open()
        record = CapabilityExecutionRecord(
            idempotency_key=idempotency_key,
            request_id=request_id,
            request_fingerprint=request_fingerprint,
            status="reserved",
            result_status=None,
            reserved_at=reserved_at,
            completed_at=None,
        )
        try:
            self._connection.execute(
                """
                INSERT INTO capability_executions(
                    idempotency_key, request_id, request_fingerprint, status,
                    result_status, reserved_at, completed_at, record_hash
                ) VALUES (?, ?, ?, 'reserved', NULL, ?, NULL, ?)
                """,
                (
                    record.idempotency_key,
                    record.request_id,
                    record.request_fingerprint,
                    record.reserved_at.value.isoformat(),
                    self._capability_record_hash(record),
                ),
            )
        except sqlite3.IntegrityError:
            return False
        except sqlite3.DatabaseError as error:
            raise PersistenceCommitError(
                "capability execution reservation failed"
            ) from error
        return True

    def complete_capability_execution(self, result: CapabilityResult) -> None:
        self._ensure_open()
        row = self._connection.execute(
            """
            SELECT request_fingerprint, reserved_at
            FROM capability_executions
            WHERE idempotency_key = ? AND request_id = ? AND status = 'reserved'
            """,
            (result.idempotency_key, result.request_id),
        ).fetchone()
        if row is None:
            raise PersistenceConsistencyError(
                "capability result has no matching execution reservation"
            )
        record = CapabilityExecutionRecord(
            idempotency_key=result.idempotency_key,
            request_id=result.request_id,
            request_fingerprint=str(row["request_fingerprint"]),
            status="completed",
            result_status=result.status,
            reserved_at=ExternalTime(datetime.fromisoformat(row["reserved_at"])),
            completed_at=result.completed_at,
        )
        try:
            cursor = self._connection.execute(
                """
                UPDATE capability_executions
                SET status = 'completed', result_status = ?, completed_at = ?,
                    record_hash = ?
                WHERE idempotency_key = ? AND request_id = ? AND status = 'reserved'
                """,
                (
                    result.status.value,
                    result.completed_at.value.isoformat(),
                    self._capability_record_hash(record),
                    result.idempotency_key,
                    result.request_id,
                ),
            )
        except sqlite3.DatabaseError as error:
            raise PersistenceCommitError(
                "capability execution result write failed"
            ) from error
        if cursor.rowcount != 1:
            raise PersistenceConsistencyError(
                "capability execution reservation changed before completion"
            )

    def capability_execution_records(
        self,
    ) -> tuple[CapabilityExecutionRecord, ...]:
        self._ensure_open()
        rows = self._connection.execute(
            "SELECT * FROM capability_executions ORDER BY rowid"
        ).fetchall()
        records: list[CapabilityExecutionRecord] = []
        for row in rows:
            result_status = (
                None
                if row["result_status"] is None
                else CapabilityResultStatus(row["result_status"])
            )
            record = CapabilityExecutionRecord(
                idempotency_key=str(row["idempotency_key"]),
                request_id=str(row["request_id"]),
                request_fingerprint=str(row["request_fingerprint"]),
                status=str(row["status"]),
                result_status=result_status,
                reserved_at=ExternalTime(datetime.fromisoformat(row["reserved_at"])),
                completed_at=(
                    None
                    if row["completed_at"] is None
                    else ExternalTime(datetime.fromisoformat(row["completed_at"]))
                ),
            )
            if row["record_hash"] != self._capability_record_hash(record):
                raise PersistenceCorruptionError(
                    "capability execution record hash does not match"
                )
            records.append(record)
        return tuple(records)

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def _initialize_and_open_session(
        self,
        *,
        configuration_version: str,
        subject_id_factory: Callable[[], str],
        session_id_factory: Callable[[], str],
        now_factory: Callable[[], ExternalTime],
        upgrade_from_configuration_versions: tuple[str, ...],
    ) -> PersistenceOpenResult:
        if not configuration_version.strip():
            raise ValueError("configuration version must not be empty")
        try:
            check = self._connection.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                raise PersistenceCorruptionError("SQLite integrity check failed")
            tables = {
                row["name"]
                for row in self._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
                if not str(row["name"]).startswith("sqlite_")
            }
            new_schema = not tables
            if new_schema:
                self._create_schema()
            elif not self._REQUIRED_TABLES.issubset(tables):
                raise PersistenceConsistencyError(
                    "existing database does not contain the stage-7 schema"
                )
            version_row = self._connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            if version_row is None or int(version_row["value"]) != self.SCHEMA_VERSION:
                raise PersistenceConsistencyError(
                    "persistence schema version is unsupported"
                )
            identity = self._read_or_create_identity(
                configuration_version,
                subject_id_factory,
                now_factory,
                allow_create=new_schema,
                upgrade_from_configuration_versions=(
                    upgrade_from_configuration_versions
                ),
            )
            previous_exit = self._read_previous_exit()
            snapshot = self._read_latest_snapshot(identity)
            session_id = session_id_factory()
            if not session_id.strip():
                raise ValueError("session ID factory returned an empty identifier")
            now = now_factory()
            self._connection.execute(
                """
                INSERT INTO sessions(session_id, started_at, ended_at, status)
                VALUES (?, ?, NULL, 'running')
                """,
                (session_id, now.value.isoformat()),
            )
            return PersistenceOpenResult(
                identity=identity,
                initialization=(
                    InitializationKind.RESTORED
                    if snapshot is not None
                    else InitializationKind.INITIALIZED
                ),
                previous_exit=previous_exit,
                session_id=session_id,
                snapshot=snapshot,
            )
        except (PersistenceError, ValueError, KeyError, TypeError):
            raise
        except (sqlite3.DatabaseError, OSError) as error:
            raise PersistenceCorruptionError(
                "persistence database cannot be validated"
            ) from error

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE metadata(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE identity(
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                subject_id TEXT NOT NULL UNIQUE,
                configuration_version TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE sessions(
                session_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL CHECK(status IN ('running', 'normal'))
            );
            CREATE TABLE checkpoints(
                sequence INTEGER PRIMARY KEY,
                snapshot_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                configuration_version TEXT NOT NULL,
                payload TEXT NOT NULL,
                checksum TEXT NOT NULL
            );
            INSERT INTO metadata(key, value) VALUES ('schema_version', '1');
            INSERT INTO metadata(key, value)
            VALUES ('has_committed_checkpoint', '0');
            """
        )

    def _read_or_create_identity(
        self,
        configuration_version: str,
        subject_id_factory: Callable[[], str],
        now_factory: Callable[[], ExternalTime],
        *,
        allow_create: bool,
        upgrade_from_configuration_versions: tuple[str, ...],
    ) -> PersistenceIdentity:
        row = self._connection.execute(
            """
            SELECT subject_id, configuration_version, schema_version, created_at
            FROM identity WHERE singleton = 1
            """
        ).fetchone()
        if row is None:
            if not allow_create:
                raise PersistenceConsistencyError(
                    "existing persistence database has no life identity"
                )
            subject_id = subject_id_factory()
            if not subject_id.strip():
                raise ValueError("subject ID factory returned an empty identifier")
            created_at = now_factory()
            identity = PersistenceIdentity(
                subject_id=subject_id,
                configuration_version=configuration_version,
                schema_version=self.SCHEMA_VERSION,
                created_at=created_at,
            )
            self._connection.execute(
                """
                INSERT INTO identity(
                    singleton, subject_id, configuration_version,
                    schema_version, created_at
                ) VALUES (1, ?, ?, ?, ?)
                """,
                (
                    identity.subject_id,
                    identity.configuration_version,
                    identity.schema_version,
                    identity.created_at.value.isoformat(),
                ),
            )
            return identity
        identity = PersistenceIdentity(
            subject_id=str(row["subject_id"]),
            configuration_version=str(row["configuration_version"]),
            schema_version=int(row["schema_version"]),
            created_at=ExternalTime(datetime.fromisoformat(row["created_at"])),
        )
        if identity.schema_version != self.SCHEMA_VERSION:
            raise PersistenceConsistencyError("stored identity schema is unsupported")
        if identity.configuration_version != configuration_version:
            if identity.configuration_version not in upgrade_from_configuration_versions:
                raise PersistenceConsistencyError(
                    "stored configuration version does not match this composition"
                )
            identity = self._upgrade_configuration(
                identity,
                configuration_version,
                now_factory(),
            )
        return identity

    def _upgrade_configuration(
        self,
        identity: PersistenceIdentity,
        configuration_version: str,
        upgraded_at: ExternalTime,
    ) -> PersistenceIdentity:
        snapshot = self._read_latest_snapshot(identity)
        upgraded_identity = replace(
            identity,
            configuration_version=configuration_version,
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "UPDATE identity SET configuration_version = ? WHERE singleton = 1",
                (configuration_version,),
            )
            if snapshot is not None:
                upgraded = replace(
                    snapshot,
                    snapshot_id=f"configuration-upgrade-{uuid4()}",
                    sequence=snapshot.sequence + 1,
                    created_at=upgraded_at,
                    configuration_version=configuration_version,
                )
                payload = encode_snapshot(upgraded)
                checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                self._connection.execute(
                    """
                    INSERT INTO checkpoints(
                        sequence, snapshot_id, created_at, subject_id,
                        configuration_version, payload, checksum
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        upgraded.sequence,
                        upgraded.snapshot_id,
                        upgraded.created_at.value.isoformat(),
                        upgraded.subject_id,
                        upgraded.configuration_version,
                        payload,
                        checksum,
                    ),
                )
            self._connection.commit()
        except sqlite3.DatabaseError as error:
            self._connection.rollback()
            raise PersistenceCommitError("configuration upgrade failed") from error
        return upgraded_identity

    def _read_previous_exit(self) -> PreviousExit:
        row = self._connection.execute(
            "SELECT status FROM sessions ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return PreviousExit.NONE
        if row["status"] == "normal":
            return PreviousExit.NORMAL
        if row["status"] == "running":
            return PreviousExit.ABNORMAL
        raise PersistenceConsistencyError("stored session status is invalid")

    def _read_latest_snapshot(
        self,
        identity: PersistenceIdentity,
    ) -> PersistenceSnapshot | None:
        row = self._connection.execute(
            """
            SELECT sequence, snapshot_id, subject_id, configuration_version,
                   payload, checksum
            FROM checkpoints ORDER BY sequence DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            marker = self._connection.execute(
                """
                SELECT value FROM metadata
                WHERE key = 'has_committed_checkpoint'
                """
            ).fetchone()
            if marker is None or marker["value"] not in {"0", "1"}:
                raise PersistenceConsistencyError(
                    "checkpoint presence marker is missing or invalid"
                )
            if marker["value"] == "1":
                raise PersistenceConsistencyError(
                    "committed checkpoint is missing from persistence database"
                )
            return None
        payload = str(row["payload"])
        actual_checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if actual_checksum != row["checksum"]:
            raise PersistenceCorruptionError("checkpoint checksum does not match")
        try:
            snapshot = decode_snapshot(payload)
        except (ValueError, KeyError, TypeError) as error:
            raise PersistenceConsistencyError("checkpoint payload is invalid") from error
        if (
            snapshot.sequence != int(row["sequence"])
            or snapshot.snapshot_id != row["snapshot_id"]
            or snapshot.subject_id != row["subject_id"]
            or snapshot.configuration_version != row["configuration_version"]
        ):
            raise PersistenceConsistencyError(
                "checkpoint index and payload identity do not match"
            )
        if (
            snapshot.subject_id != identity.subject_id
            or snapshot.configuration_version != identity.configuration_version
        ):
            raise PersistenceConsistencyError(
                "checkpoint does not belong to the stored life identity"
            )
        return snapshot

    def _ensure_open(self) -> None:
        if self._closed:
            raise PersistenceConsistencyError("persistence store is closed")

    def _insert_protection_audit(self, record: ProtectionAuditRecord) -> None:
        row = self._connection.execute(
            "SELECT record_hash FROM protection_audit ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = "genesis" if row is None else str(row["record_hash"])
        record_hash = self._audit_hash(record, previous_hash)
        self._connection.execute(
            """
            INSERT INTO protection_audit(
                record_id, occurred_at, kind, actor, target, decision, reason,
                correlation_id, previous_hash, record_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.record_id,
                record.occurred_at.value.isoformat(),
                record.kind.value,
                record.actor,
                record.target,
                record.decision.value,
                record.reason,
                record.correlation_id,
                previous_hash,
                record_hash,
            ),
        )

    @staticmethod
    def _audit_hash(record: ProtectionAuditRecord, previous_hash: str) -> str:
        payload = json.dumps(
            {
                "record_id": record.record_id,
                "occurred_at": record.occurred_at.value.isoformat(),
                "kind": record.kind.value,
                "actor": record.actor,
                "target": record.target,
                "decision": record.decision.value,
                "reason": record.reason,
                "correlation_id": record.correlation_id,
                "previous_hash": previous_hash,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _protection_control_hash(
        mode: ProtectionMode,
        definition_version: str,
        activation_id: str | None,
        version: int,
    ) -> str:
        payload = json.dumps(
            {
                "mode": mode.value,
                "definition_version": definition_version,
                "activation_id": activation_id,
                "version": version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _capability_record_hash(record: CapabilityExecutionRecord) -> str:
        payload = json.dumps(
            {
                "idempotency_key": record.idempotency_key,
                "request_id": record.request_id,
                "request_fingerprint": record.request_fingerprint,
                "status": record.status,
                "result_status": (
                    None
                    if record.result_status is None
                    else record.result_status.value
                ),
                "reserved_at": record.reserved_at.value.isoformat(),
                "completed_at": (
                    None
                    if record.completed_at is None
                    else record.completed_at.value.isoformat()
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
