"""In-memory tables backed by CSV files, with serialized all-or-nothing saves."""

from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

import pandas as pd

from .backends import Backend, Conflict, StoreError
from .schema import TABLE_COLUMNS, csv_path

T = TypeVar("T")
Tables = dict[str, pd.DataFrame]


def empty_tables() -> Tables:
    return {
        name: pd.DataFrame({column: pd.Series(dtype=str) for column in columns})
        for name, columns in TABLE_COLUMNS.items()
    }


def parse_table(name: str, raw: bytes | None) -> pd.DataFrame:
    columns = TABLE_COLUMNS[name]
    if raw is None or not raw.strip():
        return empty_tables()[name]
    frame = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
    for column in columns:
        if column not in frame.columns:
            frame[column] = ""
    # Columns someone added by hand are kept rather than silently dropped.
    extras = [column for column in frame.columns if column not in columns]
    return frame[columns + extras]


def serialize_table(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False, lineterminator="\n").encode("utf-8")


@dataclass
class Transaction:
    tables: Tables
    files: dict[str, bytes] = field(default_factory=dict)

    def add_file(self, path: str, content: bytes) -> None:
        self.files[path] = content


class Store:
    def __init__(self, backend: Backend, refresh_seconds: float = 20, max_attempts: int = 4):
        self.backend = backend
        self.refresh_seconds = refresh_seconds
        self.max_attempts = max_attempts
        self._write_lock = threading.RLock()
        self._tables: Tables | None = None
        self._head: str | None = None
        self._checked_at = 0.0

    def _load(self, head: str | None) -> None:
        tables = empty_tables()
        if head is not None:
            for name in TABLE_COLUMNS:
                raw = self.backend.read(csv_path(name), head)
                try:
                    tables[name] = parse_table(name, raw)
                except (ValueError, pd.errors.ParserError) as exc:
                    # Keep whatever we had. Never save over a file we could not read.
                    raise StoreError(f"{csv_path(name)} could not be read as CSV.") from exc
        self._tables, self._head = tables, head

    def refresh(self, force: bool = False) -> None:
        stale = time.monotonic() - self._checked_at >= self.refresh_seconds
        # Tables missing from memory mean a code update added one since these were loaded.
        missing = self._tables is not None and bool(set(TABLE_COLUMNS) - set(self._tables))
        if not force and self._tables is not None and not stale and not missing:
            return
        with self._write_lock:
            head = self.backend.head()
            if self._tables is None or head != self._head or missing:
                self._load(head)
            self._checked_at = time.monotonic()

    def snapshot(self) -> Tables:
        """Current tables. Callers must treat these as read-only."""
        self.refresh()
        assert self._tables is not None
        return self._tables

    def transaction(self, message: str, change: Callable[[Transaction], T]) -> T:
        """Apply `change` to fresh tables and commit it as one save.

        Saves from everyone using this app wait their turn on the lock. If something
        outside the app moved the branch, the change is rebuilt on the newer data, so
        all of its validation runs again.
        """
        with self._write_lock:
            for _ in range(self.max_attempts):
                self.refresh(force=True)
                assert self._tables is not None
                working = {name: frame.copy() for name, frame in self._tables.items()}
                tx = Transaction(tables=working)
                result = change(tx)

                files = dict(tx.files)
                changed = {}
                for name, frame in working.items():
                    if not frame.equals(self._tables[name]):
                        files[csv_path(name)] = serialize_table(frame)
                        changed[name] = frame
                if not files:
                    return result

                try:
                    new_head = self.backend.commit(self._head, files, message)
                except Conflict:
                    continue

                self._tables = {**self._tables, **changed}
                self._head = new_head
                self._checked_at = time.monotonic()
                return result
        raise StoreError("Too many people saved at once and your change did not save. Try again.")

    def history(self, limit: int = 50) -> list[dict[str, str]]:
        return self.backend.history(limit)

    def read_file(self, path: str) -> bytes | None:
        self.refresh()
        if self._head is None:
            return None
        return self.backend.read(path, self._head)
