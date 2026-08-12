"""Core-owned restoration of stage-7 lifecycle records."""

from __future__ import annotations

from yamicha.contracts import LifecycleRecord
from yamicha.life.stage6 import Stage6Core


class Stage7Core(Stage6Core):
    def restore_lifecycle_records(
        self,
        records: tuple[LifecycleRecord, ...],
    ) -> None:
        if self._records or self._record_ids or self._entry_ids:
            raise RuntimeError("Core records can only be restored into a fresh owner")
        record_ids = {record.record_id for record in records}
        entry_ids = {
            entry.entry_id for record in records for entry in record.entries
        }
        if len(record_ids) != len(records):
            raise ValueError("restored lifecycle record IDs must be unique")
        if len(entry_ids) != sum(len(record.entries) for record in records):
            raise ValueError("restored lifecycle entry IDs must be unique")
        self._records.extend(records)
        self._record_ids.update(record_ids)
        self._entry_ids.update(entry_ids)
