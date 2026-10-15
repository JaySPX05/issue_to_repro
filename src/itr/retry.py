"""Bounded retries for transient network failures (D62).

A live eval run lost two cases to blips outside the agent: GitHub closed the connection (`RemoteDisconnected`)
and the sandbox API read timed out (`ApiTimeoutError`). A demo that ends in ERROR because of a dropped packet
is a bad demo, so every network edge retries a few times with a short wait. Only errors that LOOK transient
(timeouts, dropped connections, 429/5xx) are retried; a 404, a missing file or a bad request fails at once,
so a real error is never hidden or repeated."""
from __future__ import annotations

import time
from typing import Callable

MARKERS = ("timeout", "timed out", "remotedisconnected", "remote end closed", "connection reset",
           "connection aborted", "connectionerror", "connection refused", "temporarily", "try again",
           "bad gateway", "service unavailable", "gateway time", " 429", " 500", " 502", " 503", " 504",
           "eof occurred", "broken pipe")
DELAYS = (2.0, 6.0)        # waits between the (len + 1) attempts


def is_transient(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in MARKERS)


def retry_call(fn: Callable, *args, delays: tuple[float, ...] = DELAYS, sleep: Callable[[float], None] | None = None,
               on_retry: Callable[[int, BaseException], None] | None = None, **kwargs):
    for i in range(len(delays) + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:                          # noqa: BLE001 - classified below, re-raised if not transient
            if i == len(delays) or not is_transient(exc):
                raise
            if on_retry:
                on_retry(i + 1, exc)
            (sleep or time.sleep)(delays[i])           # looked up late so tests can replace time.sleep


class RetrySandbox:
    """Wraps any Sandbox so each call survives a transient API failure. Other attributes pass through."""

    def __init__(self, inner, on_retry: Callable[[str, int, BaseException], None] | None = None,
                 sleep: Callable[[float], None] | None = None):
        self._inner, self._on_retry, self._sleep = inner, on_retry, sleep

    def _call(self, name: str, *args, **kwargs):
        cb = (lambda n, e: self._on_retry(name, n, e)) if self._on_retry else None
        return retry_call(getattr(self._inner, name), *args, sleep=self._sleep, on_retry=cb, **kwargs)

    def create(self, *a, **k):    return self._call("create", *a, **k)
    def exec(self, *a, **k):      return self._call("exec", *a, **k)
    def write_file(self, *a, **k): return self._call("write_file", *a, **k)
    def read_file(self, *a, **k): return self._call("read_file", *a, **k)
    def snapshot(self, *a, **k):  return self._call("snapshot", *a, **k)
    def destroy(self, *a, **k):   return self._inner.destroy(*a, **k)     # cleanup: never retried

    def __getattr__(self, item):
        return getattr(self._inner, item)
