"""olav_skills.netops.tools.textfsm_parse — universal CLI output parser.

Explicitly activated versions select the managed parser, including when it
returns no rows. Otherwise use the legacy priority chain:

    Priority 0  — PaC (Python) parser from .olav/templates/parsers/
                  (ARCH-25 — LLM-learned Python `parse()` functions)
    Priority 1  — Custom TextFSM from .olav/templates/<platform>/
                  (auto_learn output + user-seeded templates)
    Priority 2  — packaged command-specific parser, then ntc-templates

The first tier that returns a non-empty result wins. Failures at each
tier are logged at WARNING (no silent drops — GLUE-AUDIT G12).

External API (unchanged for back-compat): :func:`parse_output`. The old
``_CMD_ALIASES`` and local ``normalise_platform`` shims have been
removed — platform canonicalization now goes through
:mod:`olav_skills.netops.core.platform_canonical` (GLUE-AUDIT G1-G3 dedup).

Usage::

    from olav_skills.netops.tools.textfsm_parse import parse_output

    parsed = parse_output("cisco_ios", "show ip interface brief", raw_text)
"""

from __future__ import annotations

import logging
import hashlib
import io
from ipaddress import ip_address, ip_interface
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _safe_cmd(command: str) -> str:
    """Normalize a CLI command string for filesystem use.

    Matches the convention in :mod:`olav_skills.netops.core.parser_contract.safe_command`
    and :mod:`olav_skills.netops.core.auto_learn._template_path`. Kept local here
    to avoid a dependency cycle (parser_contract imports are heavier).
    """
    return command.strip().lower().replace(" ", "_").replace("/", "_")[:60]


def _templates_base() -> Path:
    """Resolve ``.olav/templates/`` via the platform paths config."""
    try:
        from olav_skills.core.config import get_paths_config
        return Path(get_paths_config().agent_dir_path) / "templates"
    except Exception as exc:
        logger.warning("_templates_base: get_paths_config failed (%s); falling back to CWD", exc)
        return Path.cwd() / ".olav" / "templates"


# ── Tier 0: PaC Python parser ────────────────────────────────────────────

def _try_pac_parser(platform: str, command: str, raw_output: str, *, trace=None) -> list[dict] | None:
    """Load and invoke a frozen PaC Python parser (ARCH-25)."""
    try:
        from olav_skills.netops.core import parser_registry
    except Exception as exc:
        logger.debug("parse_output: parser_registry unavailable: %s", exc)
        return None

    fn = parser_registry.load_parser(platform, command)
    if fn is None:
        return None

    try:
        rows = fn(raw_output, None)
    except Exception as exc:
        logger.warning(
            "parse_output: PaC parser %s/%s raised %s: %s",
            platform, command, type(exc).__name__, exc,
        )
        return None

    if not isinstance(rows, list):
        logger.warning(
            "parse_output: PaC parser %s/%s returned %s, expected list",
            platform, command, type(rows).__name__,
        )
        return None
    if not rows:
        # Empty result is legitimate ("BGP not active" etc.) — pass through
        # but log so it's visible.
        logger.debug("parse_output: PaC parser %s/%s returned 0 rows", platform, command)
    else:
        logger.debug(
            "parse_output: PaC parser %s/%s produced %d record(s)",
            platform, command, len(rows),
        )
    if rows and trace is not None:
        path = Path(fn.__code__.co_filename)
        trace({"tier": "pac", "template_id": str(path.relative_to(_templates_base())),
               "template_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return rows


# ── Tier 1: custom TextFSM at .olav/templates/<platform>/ ────────────────

def _try_custom_textfsm(platform: str, command: str, raw_output: str, *, trace=None) -> list[dict] | None:
    """Look for a user-seeded or auto_learn-generated TextFSM template."""
    import textfsm

    base = _templates_base()
    safe = _safe_cmd(command)
    candidates = [
        base / platform / f"{safe}.textfsm",              # nested (preferred)
        base / f"{platform}_{safe}.textfsm",              # flat combined
        base / f"{safe}.textfsm",                         # flat simple
    ]
    for tpl_path in candidates:
        if not tpl_path.exists() or tpl_path.stat().st_size == 0:
            continue
        try:
            template_bytes = tpl_path.read_bytes()
            fsm = textfsm.TextFSM(io.StringIO(template_bytes.decode("utf-8")))
            rows = fsm.ParseText(raw_output)
            if not rows:
                continue
            if trace is not None:
                trace({"tier": "custom_textfsm", "template_id": str(tpl_path.relative_to(base)),
                       "template_sha256": hashlib.sha256(template_bytes).hexdigest()})
            headers = [h.lower() for h in fsm.header]
            logger.debug("parse_output: custom TextFSM %s served parse", tpl_path)
            return [dict(zip(headers, row, strict=False)) for row in rows]
        except Exception as exc:
            logger.warning(
                "parse_output: custom TextFSM %s failed: %s", tpl_path, exc,
            )
    return None


# This source is embedded so the same module hash that the importer closure
# records also binds the default template. Local parser overrides still win.
_JUNOS_TERSE_TEMPLATE = r"""Value INTERFACE (\S+)
Value ADMIN_STATE ((?i:up|down|testing|dormant|unknown|notpresent|lowerlayerdown|-))
Value LINK_STATE ((?i:up|down|testing|dormant|unknown|notpresent|lowerlayerdown|-))
Value PROTO ((?i:inet|inet6|aenet|eth-switch|multiservice|mpls|tnp|iso|vpls|ccc|bridge))
Value IP_ADDRESS ((?:[0-9]{1,3}(?:\.[0-9]{1,3}){3}|[0-9A-Fa-f:.]*:[0-9A-Fa-f:.]+)(?:%[\w./:-]+)?(?:/\d+)?)

Start
  ^\s*$$
  ^Interface\s+Admin\s+Link\s+Proto\s+Local(?:\s+Remote)?\s*$$ -> Terse
  ^. -> Error

Terse
  ^${INTERFACE}\s+${ADMIN_STATE}\s+${LINK_STATE}\s+${PROTO}\s+${IP_ADDRESS}(?:\s+.*)?$$ -> Record
  ^${INTERFACE}\s+${ADMIN_STATE}\s+${LINK_STATE}\s+${PROTO}(?:\s+-->\s+\S+)?\s*$$ -> Record
  ^${INTERFACE}\s+${ADMIN_STATE}\s+${LINK_STATE}\s*$$ -> Record
  ^\s+${PROTO}\s+${IP_ADDRESS}(?:\s+.*)?$$ -> Record
  ^\s+${IP_ADDRESS}(?:\s+.*)?$$ -> Record
  ^\s+${PROTO}(?:\s+-->\s+\S+)?\s*$$ -> Record
  ^\s*$$
  ^. -> Error
"""


def _junos_input_bounded(raw_output: str) -> bool:
    # Check characters before UTF-8 encoding to bound the temporary allocation.
    size = 1024 * 1024
    if len(raw_output) > size or raw_output.count("\n") > 16_384:
        logger.warning("parse_output: native Junos summary input exceeds its parser budget")
        return False
    try:
        bytes_len = len(raw_output.encode("utf-8"))
    except UnicodeError:
        logger.warning("parse_output: native Junos summary input is not valid Unicode")
        return False
    lines = raw_output.splitlines()
    if bytes_len > size or len(lines) > 16_384 or any(len(line) > 4_096 for line in lines):
        logger.warning("parse_output: native Junos summary input exceeds its parser budget")
        return False
    return True


def _try_junos_terse(raw_output: str, *, trace=None) -> list[dict] | None:
    import textfsm

    try:
        fsm = textfsm.TextFSM(io.StringIO(_JUNOS_TERSE_TEMPLATE))
        parsed = fsm.ParseText(raw_output)
    except textfsm.TextFSMError:
        # TextFSM's error includes the captured line. Keep it out of logs.
        logger.warning("parse_output: packaged Junos terse parser rejected an unsupported line")
        return None
    if len(parsed) > 4_096:
        logger.warning("parse_output: packaged Junos terse row budget exceeded")
        return None
    rows = []
    parent = None
    family = ""
    for interface, admin, link, proto, ip in parsed:
        if ip:
            try:
                ip_interface(ip)
            except ValueError:
                logger.warning("parse_output: packaged Junos terse parser found an unsupported local address")
                return None
        if interface:
            parent, family = (interface, admin, link), proto
        elif parent is None or (not proto and not family):
            logger.warning("parse_output: packaged Junos terse parser found an unbound continuation")
            return None
        elif proto:
            family = proto
        rows.append(dict(zip(
            ("interface", "admin_state", "link_state", "proto", "ip_address"),
            (*parent, family, ip),
        )))
    if rows and trace is not None:
        trace({"tier": "package_textfsm", "template_id": "juniper_junos_show_interfaces_terse",
               "template_sha256": hashlib.sha256(_JUNOS_TERSE_TEMPLATE.encode()).hexdigest()})
    return rows or None


# ── Tier 2: ntc-templates built-in ───────────────────────────────────────

# ntc-templates filename aliases: some CLI commands don't map 1:1 to the
# template filename convention. Example: ``show vlan brief`` (the
# command operators actually run) maps to the ``show_vlan`` template
# (which ntc-templates ships under the shorter name). Previously each
# caller kept its own alias map; this one is the SSOT — add entries
# here rather than at call sites.
#
# (Removed: ``show ip ospf neighbors`` → ``show_ip_ospf_neighbor`` —
# stale entry from before ISSUE-NETOPS-COLLECT-OSPF-CMD was fixed.
# The collect path now sends the singular form ``show ip ospf neighbor``
# which matches the template name directly, so no alias needed.)
_NTC_FILENAME_ALIASES: dict[str, str] = {
    "show vlan brief":         "show_vlan",
    "show bgp summary":        "show_ip_bgp_summary",
    "show bgp all summary":    "show_ip_bgp_summary",
}

_JUNOS_BGP_PEER = re.compile(r"^([0-9A-Fa-f:.]+)[ \t]+(?=(?:\d+[ \t]+){5})", re.M)
_JUNOS_BGP_SUMMARY = re.compile(r"^Groups:[ \t]+\d+[ \t]+Peers:[ \t]+(\d+)[ \t]+Down peers:", re.M)
_UPTIME_FRAGMENT = re.compile(r"(?:\d+(?:[wdhms]\d*)+|\d+(?::\d+){1,2})", re.I)


def _junos_bgp_rows(fsm, raw_output: str) -> list[list]:
    """Keep peer records separate when NTC relies on implicit EOF Record.

    In a Junos summary a down peer can have no following routing table row.
    Without a record boundary, the next peer overwrites its TextFSM values.
    """
    peers = list(_JUNOS_BGP_PEER.finditer(raw_output))
    summary = _JUNOS_BGP_SUMMARY.findall(raw_output)
    if len(peers) > 4_096 or (peers and peers[0].start() > 4_096):
        logger.warning("parse_output: native Junos BGP peer budget exceeded")
        return []
    if len(summary) != 1 or summary[0].lstrip("0") != str(len(peers)).lstrip("0"):
        logger.warning("parse_output: native Junos BGP peer lines do not match one summary")
        return []
    if not peers:
        return []
    header = raw_output[:peers[0].start()]
    peer_index = fsm.header.index("PEER_IP")
    state_index = fsm.header.index("STATE")
    last_flap_index = fsm.header.index("LAST_FLAP")
    records = []
    for index, match in enumerate(peers):
        end = peers[index + 1].start() if index + 1 < len(peers) else len(raw_output)
        first, sep, tail = raw_output[match.start():end].partition("\n")
        tokens = first.split()
        if len(tokens) < 8:
            logger.warning("parse_output: native Junos BGP peer line lacks uptime or state")
            return []
        try:
            ip_address(tokens[0])
        except ValueError:
            logger.warning("parse_output: native Junos BGP peer address is unsupported")
            return []
        tokens[0] = tokens[0].lower()  # NTC's peer regex accepts lowercase hex.
        # The NTC template expects two fields for Last Up/Down. Junos can emit a single
        # uptime token. Pad the parser input and clear the padding in the row.
        single_uptime = len(tokens) == 8 or not _UPTIME_FRAGMENT.fullmatch(tokens[7])
        if single_uptime:
            tokens.insert(6, "-")
        # The template expects a space past the state token, including at EOF.
        fsm.Reset()
        rows = fsm.ParseText(header + " ".join(tokens) + " " + sep + tail)
        if len(rows) != 1 or rows[0][peer_index] != tokens[0] or rows[0][state_index] != tokens[8]:
            logger.warning("parse_output: native Junos BGP template failed to bind one peer and state")
            return []
        if single_uptime:
            rows[0][last_flap_index] = ""
        records.extend(rows)
    return records


def _try_ntc_templates(platform: str, command: str, raw_output: str, *, trace=None) -> list[dict] | None:
    """Fall back to the ntc-templates built-in library."""
    try:
        import ntc_templates
        import textfsm
    except ImportError as exc:
        logger.debug("parse_output: ntc_templates / textfsm not available: %s", exc)
        return None

    templates_dir = Path(ntc_templates.__file__).parent / "templates"
    junos_bgp = platform == "juniper_junos" and command.strip().lower() == "show bgp summary"
    safe = _safe_cmd(command) if junos_bgp else _NTC_FILENAME_ALIASES.get(command.strip().lower(), _safe_cmd(command))
    template_path = templates_dir / f"{platform}_{safe}.textfsm"
    if not template_path.exists():
        return None

    try:
        template_bytes = template_path.read_bytes()
        fsm = textfsm.TextFSM(io.StringIO(template_bytes.decode("utf-8")))
        rows = _junos_bgp_rows(fsm, raw_output) if junos_bgp else fsm.ParseText(raw_output)
    except Exception as exc:
        if junos_bgp:
            logger.warning("parse_output: installed Junos BGP template rejected the summary")
            return None
        # Upstream ntc-templates parser bugs (State Error / Rule Line N
        # mismatches) are ours to neither fix nor block on — drop to
        # debug so collection stays quiet.  The raw output still lands
        # in raw_output_store; user can /learn_cmd a PaC parser if they
        # need structured data.
        logger.debug(
            "parse_output: ntc-templates %s failed: %s", template_path, exc,
        )
        return None

    if not rows:
        return None
    # R83: lowercase headers to match Tier 1 (custom textfsm) + the
    # standard ``ntc_templates.parse.parse_output`` Python wrapper
    # convention.  Without this, Tier 1 produces ``interface`` while
    # Tier 2 produces ``INTERFACE`` for the same data — which broke
    # the per-command auto-views (a single ``json_structure`` sample
    # can't represent both shapes).  All ingest paths now produce
    # lowercase keys.
    if trace is not None:
        trace({"tier": "ntc_templates", "template_id": template_path.name,
               "template_sha256": hashlib.sha256(template_bytes).hexdigest()})
    headers = [h.lower() for h in fsm.header]
    logger.debug("parse_output: ntc-templates served parse for %s/%s", platform, command)
    return [dict(zip(headers, row)) for row in rows]


# ── Public API ──────────────────────────────────────────────────────────

def parse_output(
    platform: str,
    command: str,
    raw_output: str,
    *, trace=None,
) -> list[dict[str, Any]] | None:
    """Parse CLI output using the 3-tier priority chain.

    Args:
        platform: Device platform (raw string; normalized internally).
        command: CLI command that produced the output.
        raw_output: Raw CLI text to parse.

    Returns:
        List of parsed dicts (first-tier-success wins). Returns ``None``
        if every tier misses.
    """
    # Lazy import avoids circular dependency at module load.
    from olav_skills.netops.core.platform_canonical import canonicalize_platform

    plat = canonicalize_platform(platform)
    if plat is None:
        logger.warning(
            "parse_output: empty platform string — cannot select parser for command=%r",
            command,
        )
        return None

    from olav_skills.parser_lifecycle import parse_active

    active = parse_active(_templates_base().parent, plat, command, raw_output)
    if active is not None:
        rows, identity = active
        if trace is not None:
            trace(identity)
        return rows or None

    # Tier 0 — PaC Python parser
    rows = _try_pac_parser(plat, command, raw_output, trace=trace) if trace is not None else _try_pac_parser(plat, command, raw_output)
    if rows is None:
        # Tier 1 — custom TextFSM
        rows = _try_custom_textfsm(plat, command, raw_output, trace=trace) if trace is not None else _try_custom_textfsm(plat, command, raw_output)
    if not rows:
        # Tier 2 — fixed Junos terse profile or the installed NTC template.
        cmd = command.strip().lower()
        if plat == "juniper_junos" and cmd in {"show interfaces terse", "show bgp summary"}:
            if not _junos_input_bounded(raw_output):
                return None
            if cmd == "show interfaces terse":
                rows = _try_junos_terse(raw_output, trace=trace)
            else:
                rows = _try_ntc_templates(plat, command, raw_output, trace=trace)
        else:
            rows = _try_ntc_templates(plat, command, raw_output, trace=trace) if trace is not None else _try_ntc_templates(plat, command, raw_output)

    if rows is None or not rows:
        return None

    # R72: canonicalise interface/IP/ASN/MAC fields before returning so all
    # downstream consumers (view_builder, agent SQL queries, sim/lab Python)
    # see the same representation regardless of which tier produced it.
    try:
        from olav_skills.netops.tools.field_normalizer import normalize_fields
        rows = normalize_fields(rows)
    except Exception as exc:
        logger.warning("parse_output: field normalisation failed (%s); returning raw rows", exc)

    return rows
