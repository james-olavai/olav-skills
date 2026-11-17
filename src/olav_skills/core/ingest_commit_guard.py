"""Host commit guard: fail-closed required domain + unified legacy NoopLease.

A trusted host writer contract, platform-generic (no enterprise/netops semantics).
The guard is a context manager held across a real COMMIT and its result, never a
boolean precheck. The process-level required latch is independent of ContextVar,
so a lost context, a new thread, a fork, or an exec never silently downgrades a
required execution to a legacy no-op.
"""
from __future__ import annotations

import os
import threading
import contextvars
from contextlib import contextmanager

__all__ = (
    "commit_guard",
    "mark_required_execution",
    "register_commit_authorizer",
    "NoopLease",
    "CommitGuardRequired",
    "CommitGuardBindingMismatch",
)


class CommitGuardRequired(RuntimeError):
    """The required execution has no usable authorizer; fail closed."""


class CommitGuardBindingMismatch(RuntimeError):
    """Wrong process/thread/binding for the required execution."""


class NoopLease:
    """Trusted legacy/setup domain only. Unified check() protocol, no side effects."""

    def check(self):
        return None


# Process-level required latch, set only by the fixed --execute entry. It is
# deliberately NOT a ContextVar: a new thread or a cleared context must not
# downgrade a required process to legacy.
_REQUIRED = False
_BINDING = None
_CREATOR_PID = None
_CREATOR_TID = None
_AUTHORIZER = contextvars.ContextVar("olav_commit_authorizer", default=None)


def _validate_binding(binding):
    # The platform does not interpret enterprise fields; it only requires the
    # binding be immutable/hashable so a later caller cannot mutate the identity
    # this process was registered under.
    if binding is None or isinstance(binding, (list, dict, set, bytearray)):
        raise TypeError("binding must be immutable")
    try:
        hash(binding)
    except TypeError:
        raise TypeError("binding must be hashable") from None
    return binding


def mark_required_execution(binding):
    """Fixed ``--execute`` entry only. Idempotent: a repeat call must carry the
    same binding from the same pid/tid, otherwise it is a binding/owner mismatch."""
    global _REQUIRED, _BINDING, _CREATOR_PID, _CREATOR_TID
    binding = _validate_binding(binding)
    if _REQUIRED:
        if (_BINDING != binding or _CREATOR_PID != os.getpid()
                or _CREATOR_TID != threading.get_ident()):
            raise CommitGuardBindingMismatch
        return
    _REQUIRED = True
    _BINDING = binding
    _CREATOR_PID = os.getpid()
    _CREATOR_TID = threading.get_ident()


def register_commit_authorizer(authorizer):
    """``authorizer``: callable() -> context manager whose __enter__ acquires the
    OFD SH and live-checks, returning a lease with ``.check()``; __exit__ releases.
    Only the required creator pid/tid may register; never rewrites the owner."""
    if not callable(authorizer):
        raise TypeError("authorizer must be callable")
    if not _REQUIRED:
        raise CommitGuardRequired
    if _CREATOR_PID != os.getpid() or _CREATOR_TID != threading.get_ident():
        raise CommitGuardBindingMismatch
    _AUTHORIZER.set(authorizer)


@contextmanager
def commit_guard():
    """No-arg: the binding is this process's immutable ``_BINDING``. Yields the
    live lease returned by the authorizer's __enter__ (never a bool/None)."""
    if not _REQUIRED:
        yield NoopLease()
        return
    auth = _AUTHORIZER.get()
    if auth is None:
        raise CommitGuardRequired
    if _CREATOR_PID != os.getpid() or _CREATOR_TID != threading.get_ident():
        raise CommitGuardBindingMismatch
    ctx = auth()
    if not hasattr(ctx, "__enter__") or not hasattr(ctx, "__exit__"):
        raise CommitGuardRequired("authorizer must return a context manager, not a bool")
    with ctx as sh:
        if sh is None or not callable(getattr(sh, "check", None)):
            raise CommitGuardRequired("authorizer lease must expose check(), not a bool/None")
        yield sh
