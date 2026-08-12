"""Transactional SQLite storage for stage-7 checkpoints."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from yamicha.contracts import (
    ExternalTime,
    InitializationKind,
    PersistenceIdentity,
    PersistenceOpenResult,
    PersistenceSnapshot,
    PreviousExit,
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
        if identity.configuration_version != configuration_version:
            raise PersistenceConsistencyError(
                "stored configuration version does not match this composition"
            )
        if identity.schema_version != self.SCHEMA_VERSION:
            raise PersistenceConsistencyError("stored identity schema is unsupported")
        return identity

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
