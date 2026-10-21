"""Generic query-result metadata contract (OW055).

Platform-generic by construction: this module has no network-domain or
enterprise semantics and imports nothing but the standard library. Domain
packages (``olav_netops``) may import it; it must never import them.

The point of the contract is that every answer states what was *asked for*,
what the server would allow, what was actually applied, how many rows came
back and in which unit, and whether anything was cut by a measured cap — so a
request limit of 1 can never be read back as a server cap of 50, and a
returned aggregation row can never be read as a returned device.

Only a successfully executed result owns physical sources; schema discovery
and failures own none.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

VERSION = "ow055.1"

#: Why something was cut. ``requested_limit`` and ``server_cap`` are different
#: facts about the same short page; a business ``LIMIT`` inside caller SQL is
#: deliberately absent — it is never guessed.
ROW_CAUSES = frozenset({
    "requested_limit",
    "default_limit",
    "server_cap",
    "page_window",
    "sample_limit",
    # Serialized-size stops are named by the unit that was actually measured:
    # a character budget is not a UTF-8 byte budget, and a UTF-8 byte stop is
    # not a character stop.
    "response_char_budget",
    "response_byte_budget",
    "cell_char_cap",
})


def _compact(values: Mapping[str, Any]) -> dict:
    return {key: value for key, value in values.items() if value is not None}


def limits(
    *,
    requested: Optional[Mapping[str, Any]] = None,
    defaults: Optional[Mapping[str, Any]] = None,
    server_caps: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Report requested / default / server-cap / effective limit per field.

    ``requested`` keeps the model's own number even when it exceeds the cap;
    ``effective`` is what the tool applied; ``decided_by`` names the layer that
    decided it (``requested`` / ``default`` / ``server_cap``).
    """
    req = dict(requested or {})
    default = dict(defaults or {})
    caps = dict(server_caps or {})
    effective: dict = {}
    decided: dict = {}
    clamped = False
    for field, cap in caps.items():
        want = req.get(field)
        fallback = default.get(field)
        if want is None:
            if fallback is None:
                value = cap
                decided[field] = "server_cap"
            else:
                value = min(int(fallback), int(cap))
                if int(fallback) > int(cap):
                    clamped = True
                    decided[field] = "server_cap"
                else:
                    decided[field] = "default"
        else:
            value = min(int(want), int(cap))
            if int(want) > int(cap):
                clamped = True
                decided[field] = "server_cap"
            else:
                decided[field] = "requested"
        effective[field] = value
    return {
        "requested": _compact(req),
        "defaults": _compact(default),
        "server_caps": _compact(caps),
        "effective": effective,
        "decided_by": decided,
        "clamped": clamped,
    }


def count(*, value: int, unit: str, scope: str, exact: bool = True) -> dict:
    """An exact count for one named scope and unit."""
    return {"count": int(value), "unit": unit, "scope": scope, "exact": bool(exact)}


def unknown(*, unit: str, scope: str, reason: str) -> dict:
    """A quantity that was not measured. Never zero, never inferred."""
    return {
        "status": "unknown",
        "count": None,
        "unit": unit,
        "scope": scope,
        "reason": reason,
    }


def returned(*, value: int, unit: str) -> dict:
    """How many items this response actually carries, and in which unit."""
    return {"count": int(value), "unit": unit}


def row_truncation(
    *,
    truncated: bool,
    cause: Optional[str] = None,
    measured: bool = True,
    returned: Optional[int] = None,
    total: Optional[int] = None,
) -> dict:
    """Row/item truncation: only a measured cut names a cause."""
    if truncated and cause is not None and cause not in ROW_CAUSES:
        raise ValueError(f"unsupported truncation cause: {cause}")
    block = {
        "truncated": bool(truncated),
        "cause": cause if truncated else None,
        "measured": bool(measured),
    }
    if returned is not None:
        block["returned"] = returned
    if total is not None:
        block["total"] = total
    return block


def byte_truncation(*, truncated: bool, cause: Optional[str] = None) -> dict:
    """A stop measured in UTF-8 bytes (serialization / host ``maxBytes``)."""
    return {
        "truncated": bool(truncated),
        "cause": cause if truncated else None,
        "unit": "utf8_bytes",
        "measured": True,
    }


def char_truncation(
    *, truncated: bool, cause: str = "response_char_budget",
    budget_chars: Optional[int] = None,
) -> dict:
    """A stop measured in characters (for example a per-call text budget).

    Characters are not bytes: a reader that trims page text to a character
    budget must not report a byte budget it never measured.
    """
    if truncated and cause not in ROW_CAUSES:
        raise ValueError(f"unsupported truncation cause: {cause}")
    return _compact({
        "truncated": bool(truncated),
        "cause": cause if truncated else None,
        "unit": "characters",
        "budget_chars": budget_chars,
        "measured": True,
    })


def cell_truncation(
    *,
    capability: bool,
    truncated: Optional[bool] = None,
    at_cap: Optional[int] = None,
    cap: Optional[int] = None,
) -> dict:
    """A capability flag is not a measurement of clipping.

    ``truncated`` is true/false only when the reader itself measured clipping
    (for example a truncation helper column fired). ``None`` means this path
    cannot tell, which is *not* the same as "nothing was clipped".

    ``at_cap`` is a separate diagnostic: how many returned cells reached the
    character cap. A cell that is exactly the cap length may have been intact,
    so ``at_cap`` never proves or disproves clipping by itself.
    """
    return {
        "capability": bool(capability),
        "truncated": truncated,
        "at_cap": at_cap,
        "cap": cap,
    }


def provenance(
    *,
    executed: bool,
    physical_sources: Optional[Iterable[str]] = (),
    reason: Optional[str] = None,
) -> dict:
    """Only a successful execution owns physical sources.

    ``physical_sources=None`` means the caller did not determine them (for
    example a reader that does not parse SQL); that is reported as unknown,
    never as "this statement read nothing".
    """
    if not executed:
        sources: Optional[list] = []
    elif physical_sources is None:
        sources = None
    else:
        sources = [str(source) for source in physical_sources]
    block = {"executed": bool(executed), "physical_sources": sources}
    if reason:
        block["reason"] = reason
    return block


def contract(
    *,
    level: str,
    limits: Optional[Mapping[str, Any]] = None,
    returned: Optional[Mapping[str, Any]] = None,
    total: Optional[Mapping[str, Any]] = None,
    truncation: Optional[Mapping[str, Any]] = None,
    provenance: Optional[Mapping[str, Any]] = None,
    counts: Optional[Mapping[str, Any]] = None,
    semantics: Optional[str] = None,
) -> dict:
    """Assemble one versioned metadata block for a result/operation/statement.

    The block is additive: callers must never raise a response budget to fit
    it. Prefer compacting/trimming measured rows; when no correct block plus
    data fits, an explicit budget error is preferable to silently dropping the
    contract or a legacy field.
    """
    if level not in {"result", "operation", "statement", "page", "diff", "schema"}:
        raise ValueError(f"unsupported contract level: {level}")
    block: dict = {"version": VERSION, "level": level}
    for key, value in (
        ("limits", limits),
        ("returned", returned),
        ("total", total),
        ("truncation", truncation),
        ("provenance", provenance),
        ("counts", counts),
        ("semantics", semantics),
    ):
        if value is not None:
            block[key] = dict(value) if isinstance(value, Mapping) else value
    return block


def clamp_cause(block: Mapping[str, Any], field: str = "limit") -> str:
    """Map ``decided_by`` for one field to the matching truncation cause."""
    decided = (block.get("decided_by") or {}).get(field)
    if decided == "server_cap":
        return "server_cap"
    if decided == "default":
        return "default_limit"
    return "requested_limit"
