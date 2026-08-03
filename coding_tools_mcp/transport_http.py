from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


MAX_HTTP_SESSIONS = 128
HTTP_SESSION_TTL_SECONDS = 60 * 60
EPHEMERAL_HTTP_SESSION_TTL_SECONDS = 60
HTTP_SESSION_MODE_CHOICES = ("stateful", "ephemeral")


def _close_runtime(runtime: Any) -> None:
    close = getattr(runtime, "close", None)
    if callable(close):
        close()


@dataclass
class HTTPSessionRecord:
    runtime: Any
    last_seen: float
    in_flight: int = 0


@dataclass(frozen=True)
class HTTPSessionOptions:
    max_sessions: int = MAX_HTTP_SESSIONS
    idle_ttl_seconds: float = HTTP_SESSION_TTL_SECONDS
    evict_idle_on_capacity: bool = False


class HTTPSessionManager:
    """Own independent Runtime instances for Streamable HTTP sessions."""

    def __init__(
        self,
        factory: Callable[[], Any],
        *,
        max_sessions: int = MAX_HTTP_SESSIONS,
        idle_ttl_seconds: float = HTTP_SESSION_TTL_SECONDS,
        evict_idle_on_capacity: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        if idle_ttl_seconds <= 0:
            raise ValueError("idle_ttl_seconds must be greater than 0")
        self._factory = factory
        self._max_sessions = max_sessions
        self._idle_ttl_seconds = idle_ttl_seconds
        self._evict_idle_on_capacity = evict_idle_on_capacity
        self._clock = clock
        self._sessions: dict[str, HTTPSessionRecord] = {}
        self._lock = threading.Lock()
        self._creating = 0
        self._closed = False
        self._evicted = 0
        self._expired = 0
        self._rejected = 0

    def create(self, *, active: bool = False) -> Any:
        self.prune()
        evicted: list[HTTPSessionRecord] = []
        with self._lock:
            if self._closed:
                raise RuntimeError("HTTP session manager is closed")
            while len(self._sessions) + self._creating >= self._max_sessions:
                if not self._evict_idle_on_capacity:
                    self._rejected += 1
                    raise RuntimeError("maximum HTTP session count reached")
                idle = [
                    (session_id, record)
                    for session_id, record in self._sessions.items()
                    if record.in_flight == 0
                ]
                if not idle:
                    self._rejected += 1
                    raise RuntimeError("maximum HTTP session count reached")
                session_id, record = min(idle, key=lambda item: item[1].last_seen)
                self._sessions.pop(session_id)
                self._evicted += 1
                evicted.append(record)
            self._creating += 1
        for record in evicted:
            _close_runtime(record.runtime)
        runtime: Any | None = None
        installed = False
        try:
            runtime = self._factory()
            record = HTTPSessionRecord(
                runtime=runtime,
                last_seen=self._clock(),
                in_flight=1 if active else 0,
            )
            with self._lock:
                if self._closed:
                    raise RuntimeError("HTTP session manager is closed")
                if runtime.http_session_id in self._sessions:
                    raise RuntimeError("duplicate HTTP session identifier")
                self._sessions[runtime.http_session_id] = record
                installed = True
            return runtime
        finally:
            with self._lock:
                self._creating -= 1
            if runtime is not None and not installed:
                _close_runtime(runtime)

    def get(self, session_id: str) -> Any | None:
        self.prune()
        with self._lock:
            if self._closed:
                return None
            record = self._sessions.get(session_id)
            if record is None:
                return None
            record.last_seen = self._clock()
            return record.runtime

    def acquire(self, session_id: str) -> Any | None:
        """Mark a session active and return its runtime."""

        self.prune()
        with self._lock:
            if self._closed:
                return None
            record = self._sessions.get(session_id)
            if record is None:
                return None
            record.in_flight += 1
            record.last_seen = self._clock()
            return record.runtime

    def release(self, session_id: str) -> bool:
        """Release one active request and restart the idle TTL."""

        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return False
            if record.in_flight > 0:
                record.in_flight -= 1
            record.last_seen = self._clock()
            return True

    def delete(self, session_id: str) -> bool:
        with self._lock:
            record = self._sessions.pop(session_id, None)
        if record is None:
            return False
        _close_runtime(record.runtime)
        return True

    def prune(self) -> None:
        cutoff = self._clock() - self._idle_ttl_seconds
        with self._lock:
            expired = [
                session_id
                for session_id, record in self._sessions.items()
                if record.in_flight == 0 and record.last_seen < cutoff
            ]
            records = [self._sessions.pop(session_id) for session_id in expired]
            self._expired += len(records)
        for record in records:
            _close_runtime(record.runtime)

    def stats(self) -> dict[str, int | float | bool]:
        with self._lock:
            active = sum(1 for record in self._sessions.values() if record.in_flight > 0)
            return {
                "sessions": len(self._sessions),
                "active": active,
                "idle": len(self._sessions) - active,
                "creating": self._creating,
                "max_sessions": self._max_sessions,
                "idle_ttl_seconds": self._idle_ttl_seconds,
                "evict_idle_on_capacity": self._evict_idle_on_capacity,
                "evicted": self._evicted,
                "expired": self._expired,
                "rejected": self._rejected,
            }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            records = list(self._sessions.values())
            self._sessions.clear()
        for record in records:
            _close_runtime(record.runtime)
