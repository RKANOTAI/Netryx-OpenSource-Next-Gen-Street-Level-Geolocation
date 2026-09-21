from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


TERMINAL_STATUSES = {"succeeded", "not_found", "failed", "blocked"}
ACTIVE_STATUSES = {"queued", "running"}


class QueueFullError(RuntimeError):
    """Raised when the durable work queue has reached its configured bound."""


class JobStore:
    """Small durable SQLite queue for one Netryx worker.

    ``payload_json`` is deliberately kept out of public snapshots. It contains
    worker-only data such as paths below the controlled runtime directory.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS geolocation_jobs (
                    job_id TEXT PRIMARY KEY,
                    listing_url TEXT,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    percent INTEGER,
                    message_fr TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(geolocation_jobs)").fetchall()
            }
            if "payload_json" not in columns:
                connection.execute("ALTER TABLE geolocation_jobs ADD COLUMN payload_json TEXT")

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> dict[str, Any]:
        result = json.loads(row["result_json"]) if row["result_json"] else None
        error = json.loads(row["error_json"]) if row["error_json"] else None
        progress = {
            "phase": row["phase"],
            "percent": row["percent"],
            "completed": None,
            "total": None,
            "message_fr": row["message_fr"],
            "updated_at": row["updated_at"],
        }
        return {
            "job_id": row["job_id"],
            "listing_url": row["listing_url"] or None,
            "status": row["status"],
            "progress": progress,
            "result": result,
            "error": error,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create(
        self,
        listing_url: str | None,
        *,
        input_type: str | None = None,
        payload: dict[str, Any] | None = None,
        payload_json: str | None = None,
        max_active: int | None = 4,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically enqueue a job after all of its inputs are ready."""
        if payload is not None and payload_json is not None:
            raise ValueError("provide payload or payload_json, not both")
        if payload_json is not None:
            try:
                decoded = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("payload_json must contain valid JSON") from exc
            if not isinstance(decoded, dict):
                raise ValueError("payload_json must contain a JSON object")
            stored_payload = decoded
        elif payload is not None:
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
            stored_payload = dict(payload)
        else:
            stored_payload = None
        if input_type is not None:
            if stored_payload is None:
                stored_payload = {"input_type": input_type}
            else:
                stored_payload["input_type"] = input_type

        identifier = job_id or f"geo_{uuid.uuid4().hex}"
        timestamp = _now()
        encoded_payload = (
            json.dumps(stored_payload, ensure_ascii=False, separators=(",", ":"))
            if stored_payload is not None
            else None
        )
        # Legacy SQLite schemas require a non-null listing_url. Empty string is
        # an internal compatibility value and is normalized to null in snapshots.
        stored_listing_url = listing_url or ""
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if max_active is not None:
                if max_active <= 0:
                    raise ValueError("max_active must be positive")
                active = connection.execute(
                    "SELECT COUNT(*) FROM geolocation_jobs WHERE status IN ('queued', 'running')"
                ).fetchone()[0]
                if active >= max_active:
                    raise QueueFullError("the Netryx work queue is full")
            connection.execute(
                """
                INSERT INTO geolocation_jobs (
                    job_id, listing_url, status, phase, percent, message_fr,
                    result_json, error_json, created_at, updated_at, payload_json
                ) VALUES (?, ?, 'queued', 'queued', 0, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    identifier,
                    stored_listing_url,
                    "Analyse placée dans la file d’attente.",
                    timestamp,
                    timestamp,
                    encoded_payload,
                ),
            )
        created = self.get(identifier)
        if created is None:  # pragma: no cover - SQLite write/read invariant
            raise RuntimeError("created job could not be read back")
        return created

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM geolocation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._snapshot(row) if row else None

    def get_payload(self, job_id: str) -> dict[str, Any] | None:
        """Read worker-only payload data; never use this for an API response."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM geolocation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None or not row["payload_json"]:
            return None
        payload = json.loads(row["payload_json"])
        return dict(payload) if isinstance(payload, dict) else None

    def count_active(self) -> int:
        with self._connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM geolocation_jobs WHERE status IN ('queued', 'running')"
                ).fetchone()[0]
            )

    def claim_next(self) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM geolocation_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            timestamp = _now()
            connection.execute(
                """
                UPDATE geolocation_jobs
                SET status = 'running', phase = 'fetching_listing', percent = 5,
                    message_fr = ?, updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                ("Récupération de l’annonce…", timestamp, row["job_id"]),
            )
            connection.commit()
        return self.get(row["job_id"])

    def update_progress(
        self,
        job_id: str,
        *,
        status: str = "running",
        phase: str,
        percent: int | None,
        message_fr: str,
    ) -> None:
        if percent is not None and not 0 <= percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE geolocation_jobs
                SET status = ?, phase = ?, percent = ?, message_fr = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, phase, percent, message_fr, _now(), job_id),
            )

    def succeed(self, job_id: str, result: dict[str, Any]) -> None:
        timestamp = _now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE geolocation_jobs
                SET status = 'succeeded', phase = 'complete', percent = 100,
                    message_fr = ?, result_json = ?, error_json = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                ("Analyse terminée.", json.dumps(result, ensure_ascii=False), timestamp, job_id),
            )

    def not_found(self, job_id: str, message_fr: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE geolocation_jobs
                SET status = 'not_found', phase = 'complete', percent = 100,
                    message_fr = ?, result_json = NULL, error_json = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (message_fr, _now(), job_id),
            )

    def fail(
        self,
        job_id: str,
        *,
        code: str,
        message_fr: str,
        retryable: bool,
        status: str = "failed",
    ) -> None:
        if status not in {"failed", "blocked"}:
            raise ValueError("invalid failure status")
        error = {"code": code, "message_fr": message_fr, "retryable": retryable}
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE geolocation_jobs
                SET status = ?, phase = 'failed', percent = NULL,
                    message_fr = ?, result_json = NULL, error_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, message_fr, json.dumps(error, ensure_ascii=False), _now(), job_id),
            )

    def recover_running_jobs(self) -> int:
        """Fail jobs left running by a stopped single-worker process."""
        message = "Le worker a été interrompu avant la fin de cette analyse."
        error = json.dumps(
            {
                "code": "WORKER_RESTARTED",
                "message_fr": message,
                "retryable": True,
            },
            ensure_ascii=False,
        )
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE geolocation_jobs
                SET status = 'failed', phase = 'failed', percent = NULL,
                    message_fr = ?, result_json = NULL, error_json = ?, updated_at = ?
                WHERE status = 'running'
                """,
                (message, error, _now()),
            )
            return int(cursor.rowcount)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
