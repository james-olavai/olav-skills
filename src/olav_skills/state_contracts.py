"""Fixed native parser profiles and state predicates shared by analytical readers."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    identity: str
    fields: tuple[str, ...]
    semantics: str

    @property
    def columns(self) -> tuple[str, ...]:
        return (self.identity, *self.fields)


IOS_INTERFACE = Profile('interface', ('status', 'proto', 'ip_address'), 'line_state')
JUNOS_INTERFACE = Profile('interface', ('link_state', 'proto', 'ip_address', 'admin_state'), 'family')
JUNOS_LEGACY_INTERFACE = Profile('interface', JUNOS_INTERFACE.fields[:-1], 'family')
IOS_BGP = Profile('bgp_neighbor', ('state_or_prefixes_received',), 'ios_state_or_prefix_count')
JUNOS_LEGACY_BGP = Profile('bgp_neighbor', IOS_BGP.fields, 'compat_state_or_prefix_count')
JUNOS_BGP = Profile('peer_ip', ('state',), 'junos_state')

SOURCE_PROFILES = {
    'interfaces_ios': ('show ip interface brief', (IOS_INTERFACE,)),
    'interfaces_junos': ('show interfaces terse', (JUNOS_INTERFACE, JUNOS_LEGACY_INTERFACE)),
    'bgp_ios': ('show ip bgp summary', (IOS_BGP,)),
    'bgp_junos': ('show bgp summary', (JUNOS_BGP, JUNOS_LEGACY_BGP)),
}
VIEW_SOURCES = {
    'interfaces': ('interfaces_ios', 'interfaces_junos'),
    'bgp': ('bgp_ios', 'bgp_junos'),
}
VIEW_SCOPE = ('device_name', 'snapshot_id')


def view_name(source: str) -> str:
    command, _ = SOURCE_PROFILES[source]
    return 'v_' + command.replace(' ', '_') + '_auto'


def view_columns(source: str) -> tuple[str, ...]:
    _, profiles = SOURCE_PROFILES[source]
    return (*VIEW_SCOPE, *dict.fromkeys(column for p in profiles for column in p.columns))


def view_profile(source: str, fields) -> Profile | None:
    present = set(fields)
    if not set(VIEW_SCOPE) <= present:
        return None
    _, profiles = SOURCE_PROFILES[source]
    if source == 'interfaces_junos' and 'admin_state' in present:
        profiles = (JUNOS_INTERFACE,)
    matches = [p for p in profiles if set(p.columns) <= present]
    return matches[0] if len(matches) == 1 else None


def interface_state_sql(value: str) -> str:
    """Recognize the finite interface state set and supported ingest spellings."""
    return f"""CASE lower(trim({value}))
        WHEN 'up' THEN 'up' WHEN 'connected' THEN 'up'
        WHEN 'down' THEN 'down' WHEN 'notconnect' THEN 'down' WHEN 'notconnected' THEN 'down'
        WHEN 'admin-down' THEN 'admin-down' WHEN 'admin down' THEN 'admin-down'
        WHEN 'administratively down' THEN 'admin-down' WHEN 'disabled' THEN 'admin-down'
        WHEN 'err-disabled' THEN 'err-disabled' WHEN 'err disabled' THEN 'err-disabled'
        WHEN 'testing' THEN 'testing' WHEN 'dormant' THEN 'dormant' END"""


def interface_health_sql(one: str, two: str) -> str:
    first, second = interface_state_sql(one), interface_state_sql(two)
    return f"""CASE WHEN ({first})<>'up' OR ({second})<>'up' THEN 'nonhealthy'
        WHEN ({first})='up' AND ({second})='up' THEN 'healthy' ELSE 'unknown' END"""


def bgp_state_sql(value: str, profile: Profile) -> str:
    numeric = (f" OR regexp_full_match(trim({value}), '[0-9]+')"
               if profile in (IOS_BGP, JUNOS_LEGACY_BGP) else '')
    return f"""CASE WHEN lower(trim({value})) IN ('established','estab','establ'){numeric} THEN 'Established'
        WHEN lower(trim({value}))='idle' THEN 'Idle'
        WHEN lower(trim({value}))='active' THEN 'Active'
        WHEN lower(trim({value}))='connect' THEN 'Connect'
        WHEN lower(trim({value}))='opensent' THEN 'OpenSent'
        WHEN lower(trim({value}))='openconfirm' THEN 'OpenConfirm' END"""


def bgp_health_sql(value: str, profile: Profile) -> str:
    state = bgp_state_sql(value, profile)
    return f"CASE WHEN ({state})='Established' THEN 'healthy' WHEN ({state}) IS NULL THEN 'unknown' ELSE 'nonhealthy' END"
