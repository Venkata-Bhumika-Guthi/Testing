#!/usr/bin/env python3
from __future__ import annotations

import collections
import functools
import logging
import statistics
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any, Callable, Deque, Dict, Iterable, List, Optional, Tuple, Type
)

# ──────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────

log = logging.getLogger("circuitbreaker")


# ──────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────

class State(str, Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


class Outcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    IGNORED = "ignored"     # exception type not tracked
    REJECTED = "rejected"   # circuit was OPEN; call never attempted


# ──────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────

@dataclass
class CircuitBreakerConfig:
    """
    All thresholds and timeouts for a CircuitBreaker instance.

    Parameters
    ----------
    failure_threshold   : Number of failures in the rolling window before OPEN.
    failure_rate_threshold : 0.0–1.0. If set, circuit opens when failure RATE
                            exceeds this fraction (requires min_calls).
    min_calls           : Minimum calls before rate-based tripping is evaluated.
    recovery_timeout    : Seconds to wait in OPEN before probing (HALF_OPEN).
    success_threshold   : Consecutive successes in HALF_OPEN before CLOSED.
    half_open_max_calls : Max concurrent calls allowed in HALF_OPEN state.
    window_size         : Rolling window size (call count) for metrics.
    call_timeout        : Optional per-call timeout in seconds (wraps threading).
    ignored_exceptions  : Exception types that do NOT count as failures.
    expected_exceptions : Exception types that DO count as failures
                          (empty = all non-ignored exceptions count).
    bulkhead_max        : Max concurrent calls allowed at any time (0 = unlimited).
    """

    failure_threshold:       int                           = 5
    failure_rate_threshold:  Optional[float]               = None
    min_calls:               int                           = 10
    recovery_timeout:        float                         = 30.0
    success_threshold:       int                           = 2
    half_open_max_calls:     int                           = 1
    window_size:             int                           = 20
    call_timeout:            Optional[float]               = None
    ignored_exceptions:      Tuple[Type[Exception], ...]  = ()
    expected_exceptions:     Tuple[Type[Exception], ...]  = ()
    bulkhead_max:            int                           = 0

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be ≥ 1")
        if self.recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be > 0")
        if self.success_threshold < 1:
            raise ValueError("success_threshold must be ≥ 1")
        if self.failure_rate_threshold is not None:
            if not (0.0 < self.failure_rate_threshold <= 1.0):
                raise ValueError("failure_rate_threshold must be in (0, 1]")


# ──────────────────────────────────────────────────────────────
# Metrics (rolling window)
# ──────────────────────────────────────────────────────────────

@dataclass
class CallRecord:
    outcome:    Outcome
    duration_s: float
    timestamp:  float


@dataclass
class Snapshot:
    """Point-in-time metrics snapshot (immutable)."""
    state:            State
    total_calls:      int
    successes:        int
    failures:         int
    ignored:          int
    rejected:         int
    failure_rate:     float        # 0.0 – 1.0 over the rolling window
    avg_latency_ms:   float
    p99_latency_ms:   float
    consecutive_successes: int     # relevant in HALF_OPEN
    consecutive_failures:  int
    last_failure_at:  Optional[float]
    last_opened_at:   Optional[float]
    opened_count:     int          # total times circuit has tripped

    def __str__(self) -> str:
        return (
            f"[{self.state.value.upper():9s}] "
            f"calls={self.total_calls} "
            f"fail_rate={self.failure_rate:.1%} "
            f"avg={self.avg_latency_ms:.1f}ms "
            f"p99={self.p99_latency_ms:.1f}ms "
            f"opened={self.opened_count}×"
        )


class _RollingWindow:
    """Fixed-size rolling window of CallRecords."""

    def __init__(self, size: int) -> None:
        self._size    = size
        self._records: Deque[CallRecord] = collections.deque(maxlen=size)

    def record(self, outcome: Outcome, duration_s: float) -> None:
        self._records.append(
            CallRecord(outcome=outcome, duration_s=duration_s, timestamp=time.monotonic())
        )

    def snapshot(self) -> Dict[str, Any]:
        records = list(self._records)
        total   = len(records)
        if total == 0:
            return dict(
                total=0, successes=0, failures=0, ignored=0, rejected=0,
                failure_rate=0.0, latencies=[],
            )
        successes = sum(1 for r in records if r.outcome == Outcome.SUCCESS)
        failures  = sum(1 for r in records if r.outcome == Outcome.FAILURE)
        ignored   = sum(1 for r in records if r.outcome == Outcome.IGNORED)
        rejected  = sum(1 for r in records if r.outcome == Outcome.REJECTED)
        # Failure rate computed only over calls that actually ran
        attempted = successes + failures
        rate      = failures / attempted if attempted else 0.0
        latencies = [r.duration_s * 1000 for r in records
                     if r.outcome in (Outcome.SUCCESS, Outcome.FAILURE)]
        return dict(
            total=total, successes=successes, failures=failures,
            ignored=ignored, rejected=rejected, failure_rate=rate,
            latencies=latencies,
        )

    def clear(self) -> None:
        self._records.clear()


# ──────────────────────────────────────────────────────────────
# Audit log entry
# ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AuditEntry:
    timestamp:  float
    from_state: State
    to_state:   State
    reason:     str

    def __str__(self) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return (
            f"{ts}  {self.from_state.value:9s} → {self.to_state.value:9s}  {self.reason}"
        )


# ──────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────

class CircuitBreakerError(Exception):
    """Base class for all circuit breaker errors."""


class OpenCircuitError(CircuitBreakerError):
    """Raised when a call is rejected because the circuit is OPEN."""

    def __init__(self, name: str, retry_after: float) -> None:
        self.name        = name
        self.retry_after = retry_after
        super().__init__(
            f"Circuit '{name}' is OPEN. Retry after {retry_after:.1f}s."
        )


class BulkheadFullError(CircuitBreakerError):
    """Raised when the bulkhead (concurrency limit) is saturated."""

    def __init__(self, name: str, limit: int) -> None:
        self.name  = name
        self.limit = limit
        super().__init__(
            f"Circuit '{name}' bulkhead full ({limit} concurrent calls)."
        )


class CallTimeoutError(CircuitBreakerError):
    """Raised when a call exceeds the configured timeout."""

    def __init__(self, name: str, timeout: float) -> None:
        self.name    = name
        self.timeout = timeout
        super().__init__(f"Circuit '{name}' call timed out after {timeout}s.")


# ──────────────────────────────────────────────────────────────
# Core Circuit Breaker
# ──────────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Thread-safe circuit breaker.

    All state transitions are protected by a reentrant lock.
    Metrics are collected in a bounded rolling window.
    State changes emit structured log messages and fire optional hooks.
    """

    def __init__(
        self,
        name:   str,
        config: Optional[CircuitBreakerConfig] = None,
        *,
        on_open:      Optional[Callable[["CircuitBreaker"], None]] = None,
        on_close:     Optional[Callable[["CircuitBreaker"], None]] = None,
        on_half_open: Optional[Callable[["CircuitBreaker"], None]] = None,
        on_success:   Optional[Callable[["CircuitBreaker", float], None]] = None,
        on_failure:   Optional[Callable[["CircuitBreaker", Exception, float], None]] = None,
    ) -> None:
        if not name:
            raise ValueError("Circuit breaker name must be non-empty.")

        self.name   = name
        self.config = config or CircuitBreakerConfig()

        # ── state ─────────────────────────────────────────────
        self._state:                 State          = State.CLOSED
        self._opened_at:             Optional[float] = None
        self._last_failure_at:       Optional[float] = None
        self._last_opened_at:        Optional[float] = None
        self._consecutive_successes: int             = 0
        self._consecutive_failures:  int             = 0
        self._opened_count:          int             = 0

        # ── concurrency ───────────────────────────────────────
        self._lock               = threading.RLock()
        self._bulkhead_semaphore = (
            threading.Semaphore(self.config.bulkhead_max)
            if self.config.bulkhead_max > 0 else None
        )
        self._half_open_semaphore = threading.Semaphore(
            self.config.half_open_max_calls
        )

        # ── observability ─────────────────────────────────────
        self._window     = _RollingWindow(self.config.window_size)
        self._audit_log: List[AuditEntry] = []

        # ── hooks ─────────────────────────────────────────────
        self._on_open      = on_open
        self._on_close     = on_close
        self._on_half_open = on_half_open
        self._on_success   = on_success
        self._on_failure   = on_failure

        log.info("CircuitBreaker(%s) created config=%r", name, self.config)

    # ── state machine ─────────────────────────────────────────

    @property
    def state(self) -> State:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    def _maybe_transition_to_half_open(self) -> None:
        """Called under lock. Transition OPEN → HALF_OPEN if timeout elapsed."""
        if self._state == State.OPEN:
            elapsed = time.monotonic() - (self._opened_at or 0.0)
            if elapsed >= self.config.recovery_timeout:
                self._transition(State.HALF_OPEN, "recovery timeout elapsed")

    def _transition(self, new_state: State, reason: str) -> None:
        """Must be called under self._lock."""
        old_state = self._state
        if old_state == new_state:
            return

        self._state = new_state
        entry = AuditEntry(
            timestamp=time.time(),
            from_state=old_state,
            to_state=new_state,
            reason=reason,
        )
        self._audit_log.append(entry)
        log.warning("CircuitBreaker(%s) %s", self.name, entry)

        if new_state == State.OPEN:
            self._opened_at    = time.monotonic()
            self._last_opened_at = time.time()
            self._opened_count += 1
            self._consecutive_successes = 0
            self._half_open_semaphore   = threading.Semaphore(
                self.config.half_open_max_calls
            )
            if self._on_open:
                try:
                    self._on_open(self)
                except Exception:
                    log.exception("on_open hook raised for circuit %s", self.name)

        elif new_state == State.CLOSED:
            self._consecutive_failures  = 0
            self._consecutive_successes = 0
            self._window.clear()
            if self._on_close:
                try:
                    self._on_close(self)
                except Exception:
                    log.exception("on_close hook raised for circuit %s", self.name)

        elif new_state == State.HALF_OPEN:
            self._consecutive_successes = 0
            if self._on_half_open:
                try:
                    self._on_half_open(self)
                except Exception:
                    log.exception("on_half_open hook raised for circuit %s", self.name)

    def _should_open(self) -> Tuple[bool, str]:
        """
        Evaluate whether CLOSED → OPEN should trigger.
        Returns (should_open, reason).
        """
        cfg = self.config

        # Hard count threshold
        if self._consecutive_failures >= cfg.failure_threshold:
            return True, f"consecutive failures={self._consecutive_failures} ≥ {cfg.failure_threshold}"

        # Rate-based threshold
        if cfg.failure_rate_threshold is not None:
            snap = self._window.snapshot()
            attempted = snap["successes"] + snap["failures"]
            if attempted >= cfg.min_calls and snap["failure_rate"] >= cfg.failure_rate_threshold:
                return (
                    True,
                    f"failure_rate={snap['failure_rate']:.1%} ≥ "
                    f"{cfg.failure_rate_threshold:.1%} over {attempted} calls",
                )

        return False, ""

    # ── call interception ─────────────────────────────────────

    def _classify_exception(self, exc: Exception) -> Outcome:
        """Decide if an exception is a tracked failure or an ignored one."""
        if self.config.ignored_exceptions and isinstance(exc, self.config.ignored_exceptions):
            return Outcome.IGNORED
        if self.config.expected_exceptions:
            return Outcome.FAILURE if isinstance(exc, self.config.expected_exceptions) else Outcome.IGNORED
        return Outcome.FAILURE   # default: all exceptions are failures

    def _before_call(self) -> None:
        """
        Gate logic executed before every call.
        Raises OpenCircuitError or BulkheadFullError when appropriate.
        Must NOT be called under self._lock (to avoid deadlock with semaphores).
        """
        # Bulkhead check (no lock needed — semaphore is atomic)
        if self._bulkhead_semaphore is not None:
            acquired = self._bulkhead_semaphore.acquire(blocking=False)
            if not acquired:
                self._window.record(Outcome.REJECTED, 0.0)
                raise BulkheadFullError(self.name, self.config.bulkhead_max)

        with self._lock:
            self._maybe_transition_to_half_open()
            state = self._state

            if state == State.OPEN:
                elapsed     = time.monotonic() - (self._opened_at or 0.0)
                retry_after = max(0.0, self.config.recovery_timeout - elapsed)
                self._window.record(Outcome.REJECTED, 0.0)
                if self._bulkhead_semaphore:
                    self._bulkhead_semaphore.release()
                raise OpenCircuitError(self.name, retry_after)

            if state == State.HALF_OPEN:
                acquired = self._half_open_semaphore.acquire(blocking=False)
                if not acquired:
                    elapsed     = time.monotonic() - (self._opened_at or 0.0)
                    retry_after = max(0.0, self.config.recovery_timeout - elapsed)
                    self._window.record(Outcome.REJECTED, 0.0)
                    if self._bulkhead_semaphore:
                        self._bulkhead_semaphore.release()
                    raise OpenCircuitError(self.name, retry_after)

    def _after_success(self, duration_s: float, in_half_open: bool) -> None:
        """Called under self._lock after a successful call."""
        self._window.record(Outcome.SUCCESS, duration_s)
        self._consecutive_failures  = 0
        self._consecutive_successes += 1

        if self._on_success:
            try:
                self._on_success(self, duration_s)
            except Exception:
                log.exception("on_success hook raised for circuit %s", self.name)

        if in_half_open:
            if self._consecutive_successes >= self.config.success_threshold:
                self._transition(
                    State.CLOSED,
                    f"recovered: {self._consecutive_successes} consecutive successes",
                )
            if self._state == State.HALF_OPEN:
                self._half_open_semaphore.release()

    def _after_failure(self, exc: Exception, duration_s: float, in_half_open: bool) -> None:
        """Called under self._lock after a failed call."""
        outcome = self._classify_exception(exc)
        self._window.record(outcome, duration_s)

        if outcome == Outcome.FAILURE:
            self._consecutive_failures  += 1
            self._consecutive_successes  = 0
            self._last_failure_at        = time.time()

            if self._on_failure:
                try:
                    self._on_failure(self, exc, duration_s)
                except Exception:
                    log.exception("on_failure hook raised for circuit %s", self.name)

            if in_half_open:
                self._transition(State.OPEN, f"probe failed: {type(exc).__name__}")
                return

            should, reason = self._should_open()
            if should:
                self._transition(State.OPEN, reason)
        else:
            log.debug(
                "CircuitBreaker(%s) ignoring %s (outcome=%s)",
                self.name, type(exc).__name__, outcome.value,
            )
            if in_half_open:
                self._half_open_semaphore.release()

    # ── public call interfaces ────────────────────────────────

    def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """
        Execute *func* protected by the circuit breaker.

        Raises
        ------
        OpenCircuitError  — circuit is OPEN; call was not attempted.
        BulkheadFullError — concurrency limit reached.
        CallTimeoutError  — call exceeded configured timeout.
        <original exc>    — any exception from func itself.
        """
        in_half_open = False
        start        = time.monotonic()

        self._before_call()

        with self._lock:
            in_half_open = (self._state == State.HALF_OPEN)

        try:
            if self.config.call_timeout:
                result = self._call_with_timeout(func, args, kwargs)
            else:
                result = func(*args, **kwargs)

            duration = time.monotonic() - start
            with self._lock:
                self._after_success(duration, in_half_open)
            log.debug(
                "CircuitBreaker(%s) success duration=%.3fs", self.name, duration
            )
            return result

        except (OpenCircuitError, BulkheadFullError, CallTimeoutError):
            raise

        except Exception as exc:
            duration = time.monotonic() - start
            with self._lock:
                self._after_failure(exc, duration, in_half_open)
            log.debug(
                "CircuitBreaker(%s) failure %s duration=%.3fs",
                self.name, type(exc).__name__, duration,
            )
            raise

        finally:
            if self._bulkhead_semaphore:
                self._bulkhead_semaphore.release()

    def _call_with_timeout(self, func: Callable, args: tuple, kwargs: dict) -> Any:
        """Run func in a daemon thread; raise CallTimeoutError if it hangs."""
        result:    List[Any]       = []
        exc_box:   List[Exception] = []

        def _run():
            try:
                result.append(func(*args, **kwargs))
            except Exception as e:
                exc_box.append(e)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=self.config.call_timeout)

        if t.is_alive():
            raise CallTimeoutError(self.name, self.config.call_timeout)  # type: ignore[arg-type]
        if exc_box:
            raise exc_box[0]
        return result[0]

    def __enter__(self) -> "CircuitBreaker":
        self._before_call()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        in_half_open = (self._state == State.HALF_OPEN)
        duration     = 0.0   # context managers don't track duration (use call() for that)

        if exc_type is None:
            with self._lock:
                self._after_success(duration, in_half_open)
        elif exc_type not in (OpenCircuitError, BulkheadFullError, CallTimeoutError):
            with self._lock:
                self._after_failure(exc_val, duration, in_half_open)

        if self._bulkhead_semaphore:
            self._bulkhead_semaphore.release()

        return False   # never suppress exceptions

    # ── decorator ─────────────────────────────────────────────

    @property
    def protect(self) -> Callable:
        """
        Decorator interface.

        @cb.protect
        def my_func(): ...
        """
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return self.call(func, *args, **kwargs)
            return wrapper
        return decorator

    # ── observability ─────────────────────────────────────────

    def snapshot(self) -> Snapshot:
        """Return a point-in-time metrics snapshot."""
        with self._lock:
            self._maybe_transition_to_half_open()
            w     = self._window.snapshot()
            lats  = w["latencies"]
            return Snapshot(
                state                 = self._state,
                total_calls           = w["total"],
                successes             = w["successes"],
                failures              = w["failures"],
                ignored               = w["ignored"],
                rejected              = w["rejected"],
                failure_rate          = w["failure_rate"],
                avg_latency_ms        = statistics.mean(lats) if lats else 0.0,
                p99_latency_ms        = (
                    sorted(lats)[int(len(lats) * 0.99)] if len(lats) >= 2 else (lats[0] if lats else 0.0)
                ),
                consecutive_successes = self._consecutive_successes,
                consecutive_failures  = self._consecutive_failures,
                last_failure_at       = self._last_failure_at,
                last_opened_at        = self._last_opened_at,
                opened_count          = self._opened_count,
            )

    @property
    def audit_log(self) -> List[AuditEntry]:
        """Return an immutable copy of the state-transition audit log."""
        with self._lock:
            return list(self._audit_log)

    def reset(self) -> None:
        """Force circuit to CLOSED and clear all counters."""
        with self._lock:
            self._transition(State.CLOSED, "manual reset")
            self._window.clear()
            self._consecutive_failures  = 0
            self._consecutive_successes = 0
            self._opened_at             = None
        log.info("CircuitBreaker(%s) manually reset.", self.name)

    def trip(self) -> None:
        """Force circuit to OPEN (useful for maintenance windows)."""
        with self._lock:
            self._transition(State.OPEN, "manually tripped")
        log.info("CircuitBreaker(%s) manually tripped.", self.name)

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self.name!r} state={self._state.value} "
            f"failures={self._consecutive_failures} "
            f"opened={self._opened_count}×)"
        )


# ──────────────────────────────────────────────────────────────
# Registry  (global named-circuit lookup)
# ──────────────────────────────────────────────────────────────

class CircuitBreakerRegistry:
    """
    Optional global registry.  Useful for health-check endpoints
    that need to iterate over all breakers.
    """

    def __init__(self) -> None:
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def register(self, cb: CircuitBreaker) -> CircuitBreaker:
        with self._lock:
            if cb.name in self._breakers:
                raise ValueError(f"Circuit '{cb.name}' is already registered.")
            self._breakers[cb.name] = cb
        return cb

    def get(self, name: str) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                raise KeyError(f"No circuit named '{name}' in registry.")
            return self._breakers[name]

    def all_snapshots(self) -> Dict[str, Snapshot]:
        with self._lock:
            names = list(self._breakers.keys())
        return {name: self._breakers[name].snapshot() for name in names}

    def healthy(self) -> bool:
        """True if ALL circuits are CLOSED or HALF_OPEN."""
        return all(
            s.state != State.OPEN
            for s in self.all_snapshots().values()
        )

    def __iter__(self) -> Iterable[CircuitBreaker]:
        with self._lock:
            return iter(list(self._breakers.values()))

    def __len__(self) -> int:
        with self._lock:
            return len(self._breakers)

    def __repr__(self) -> str:
        with self._lock:
            return f"CircuitBreakerRegistry({list(self._breakers.keys())})"


# Default global registry (opt-in)
registry = CircuitBreakerRegistry()


# ──────────────────────────────────────────────────────────────
# Demo
# ──────────────────────────────────────────────────────────────

def _demo() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    RST  = "\033[0m";  BOLD = "\033[1m"
    GRN  = "\033[92m"; RED  = "\033[91m"
    YLW  = "\033[93m"; CYN  = "\033[96m"
    GRY  = "\033[90m"; WHT  = "\033[97m"
    MAG  = "\033[95m"

    def c(code, txt): return f"{code}{txt}{RST}"
    def header(txt):
        print(c(CYN, f"\n  ▶ {txt}"))

    print(c(BOLD, "\n══════════════════════════════════════════════"))
    print(c(BOLD, "   circuitbreaker.py  —  demo"))
    print(c(BOLD, "══════════════════════════════════════════════"))

    # ── 1. Basic trip / recover cycle ─────────────────────────
    header("Basic trip → open → half_open → close cycle")

    call_count = [0]

    def flaky_service():
        call_count[0] += 1
        if call_count[0] <= 6:
            raise ConnectionError("Service unavailable")
        return f"ok (call #{call_count[0]})"

    cb = CircuitBreaker(
        "demo",
        config=CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=0.3,  # short for demo
            success_threshold=2,
        ),
        on_open      = lambda b: print(c(RED,  f"    🔴 hook: {b.name} opened")),
        on_half_open = lambda b: print(c(YLW,  f"    🟡 hook: {b.name} probing")),
        on_close     = lambda b: print(c(GRN,  f"    🟢 hook: {b.name} closed")),
    )

    for i in range(14):
        time.sleep(0.05)
        if i == 8:
            # simulate recovery
            call_count[0] = 10
            print(c(GRY, "      [upstream recovered]"))
        try:
            res = cb.call(flaky_service)
            snap = cb.snapshot()
            print(f"    req {i+1:02d}  {c(GRN, '✓ ALLOW')}  {res:<18}  {c(GRY, str(snap))}")
        except OpenCircuitError as e:
            snap = cb.snapshot()
            print(f"    req {i+1:02d}  {c(RED, '✗ OPEN ')}  retry={e.retry_after:.2f}s     {c(GRY, str(snap))}")
        except ConnectionError as e:
            snap = cb.snapshot()
            print(f"    req {i+1:02d}  {c(YLW, '✗ FAIL ')}  {str(e):<18}  {c(GRY, str(snap))}")

    # ── 2. Failure-rate threshold ──────────────────────────────
    header("Failure-rate based tripping (50% threshold, min 6 calls)")

    rate_cb = CircuitBreaker(
        "rate-demo",
        config=CircuitBreakerConfig(
            failure_threshold=999,         # disable count-based
            failure_rate_threshold=0.5,
            min_calls=6,
            recovery_timeout=999.0,
        ),
    )
    outcomes = [True, False, True, False, True, False, False, False]
    for i, should_succeed in enumerate(outcomes):
        try:
            rate_cb.call(lambda s=should_succeed: (_ for _ in ()).throw(IOError("err")) if not s else "ok")
            print(f"    req {i+1}  {c(GRN, 'ok  ')}  state={rate_cb.state.value}")
        except OpenCircuitError:
            print(f"    req {i+1}  {c(RED, 'OPEN')}  circuit tripped by rate threshold")
        except IOError:
            print(f"    req {i+1}  {c(YLW, 'fail')}  state={rate_cb.state.value}")

    # ── 3. Timeout ─────────────────────────────────────────────
    header("Per-call timeout (0.1s limit on a slow function)")

    timeout_cb = CircuitBreaker(
        "timeout-demo",
        config=CircuitBreakerConfig(failure_threshold=2, recovery_timeout=999.0, call_timeout=0.1),
    )

    def slow():
        time.sleep(0.5)
        return "done"

    for i in range(3):
        try:
            timeout_cb.call(slow)
        except CallTimeoutError as e:
            print(f"    req {i+1}  {c(RED, 'TIMEOUT')}  {e}")
        except OpenCircuitError as e:
            print(f"    req {i+1}  {c(RED, 'OPEN   ')}  {e}")

    # ── 4. Decorator interface ─────────────────────────────────
    header("@cb.protect decorator")

    deco_cb    = CircuitBreaker("deco", config=CircuitBreakerConfig(failure_threshold=2, recovery_timeout=999.0))
    deco_calls = [0]

    @deco_cb.protect
    def fetch_user(uid: int) -> dict:
        deco_calls[0] += 1
        if deco_calls[0] <= 3:
            raise RuntimeError(f"DB timeout for uid={uid}")
        return {"id": uid, "name": "Alice"}

    for i in range(5):
        try:
            result = fetch_user(i)
            print(f"    call {i+1}  {c(GRN, '✓')}  {result}")
        except OpenCircuitError as e:
            print(f"    call {i+1}  {c(RED, '✗ OPEN')}  {e}")
        except RuntimeError as e:
            print(f"    call {i+1}  {c(YLW, '✗ ERR ')}  {e}")

    # ── 5. Registry + health ───────────────────────────────────
    header("Registry and health check")

    reg = CircuitBreakerRegistry()
    for name in ("auth", "billing", "notifications"):
        reg.register(CircuitBreaker(name, config=CircuitBreakerConfig(failure_threshold=3, recovery_timeout=999.0)))

    reg.get("billing").trip()

    for name, snap in reg.all_snapshots().items():
        icon = c(GRN, "●") if snap.state != State.OPEN else c(RED, "●")
        print(f"    {icon}  {name:<16} {snap.state.value}")

    health = c(GRN, "HEALTHY") if reg.healthy() else c(RED, "DEGRADED")
    print(f"\n    system health: {health}")

    # ── 6. Audit log ──────────────────────────────────────────
    header("Audit log (state transitions)")
    for entry in cb.audit_log:
        from_c = {State.CLOSED: GRN, State.OPEN: RED, State.HALF_OPEN: YLW}
        to_c   = from_c
        print(
            f"    {c(GRY, time.strftime('%H:%M:%S', time.localtime(entry.timestamp)))} "
            f" {c(from_c[entry.from_state], entry.from_state.value):20s}"
            f" → {c(to_c[entry.to_state], entry.to_state.value):20s}"
            f"  {c(GRY, entry.reason)}"
        )

    print(c(BOLD, "\n  All demos complete.\n"))


if __name__ == "__main__":
    _demo()
