"""Topology Pydantic schemas — ARCH-28 agent-boundary contract.

Used by the ``query_topology`` agent tool to validate + serialize the
output of the ``v_*_auto`` views. Guarantees:

* ``neighbor_ip`` / ``neighbor_ip`` are real IP addresses (``IPvAnyAddress``)
* ``neighbor_as`` is an int (not a string like ``"65001"``)
* ``state`` is one of the canonical values (``Literal`` enum)
* No extra / misspelled fields leak through (``extra="forbid"``)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, field_validator


# ARCH-28 Round 69 note on BGP state canonical set:
# SQL-level canonicalization in view_builder._BGP_STATE_CASE handles
# common synonyms (numeric prefix-count → Established, "Establ" →
# Established, etc.). Values that slip through without matching a case
# arm are passed through verbatim — the Literal below must NOT be too
# strict or real states will fail validation.
BgpState = Literal[
    "Established",
    "Active",
    "Idle",
    "Connect",
    "OpenSent",
    "OpenConfirm",
    "Down",
]


# The stock IOS profile attaches a recorded neighbor-role suffix to both
# stable and transitional states. These spellings preserve the phase and
# role without interpreting either as health or proof of continuity.
OspfState = Literal[
    "Full",
    "2-Way",
    "Down",
    "Attempt",
    "Init",
    "ExStart",
    "Exchange",
    "Loading",
    "FULL/DR", "FULL/BDR", "FULL/DROTHER", "FULL/-",
    "2WAY/DR", "2WAY/BDR", "2WAY/DROTHER", "2WAY/-",
    "DOWN/DR", "DOWN/BDR", "DOWN/DROTHER", "DOWN/-",
    "ATTEMPT/DR", "ATTEMPT/BDR", "ATTEMPT/DROTHER", "ATTEMPT/-",
    "INIT/DR", "INIT/BDR", "INIT/DROTHER", "INIT/-",
    "EXSTART/DR", "EXSTART/BDR", "EXSTART/DROTHER", "EXSTART/-",
    "EXCHANGE/DR", "EXCHANGE/BDR", "EXCHANGE/DROTHER", "EXCHANGE/-",
    "LOADING/DR", "LOADING/BDR", "LOADING/DROTHER", "LOADING/-",
]
_OSPF_UNSUFFIXED_STATES = {
    "FULL": "Full", "2WAY": "2-Way", "DOWN": "Down", "ATTEMPT": "Attempt",
    "INIT": "Init", "EXSTART": "ExStart", "EXCHANGE": "Exchange", "LOADING": "Loading",
}


class BGPSession(BaseModel):
    """One BGP peer session from ``netops.v_bgp_neighbors_auto``."""
    model_config = ConfigDict(extra="forbid")

    device: str
    neighbor_ip: IPvAnyAddress
    neighbor_as: int
    local_as: int | None = None
    router_id: str | None = None
    state: BgpState
    uptime: str | None = None


class OSPFAdjacency(BaseModel):
    """One recorded OSPF neighbor from ``netops.v_show_ip_ospf_neighbor_auto``."""
    model_config = ConfigDict(extra="forbid")

    device: str
    neighbor_id: str
    neighbor_ip: IPvAnyAddress | None = None
    interface: str
    area: str | None = None
    state: OspfState
    dead_time: str | None = None

    @field_validator('state', mode='before')
    @classmethod
    def _normalize_state(cls, raw: object) -> object:
        if not isinstance(raw, str):
            return raw
        phase, separator, role = raw.strip().upper().partition('/')
        phase = phase.strip()
        if phase == '2-WAY':
            phase = '2WAY'
        if separator:
            return f'{phase}/{role.strip()}'
        return _OSPF_UNSUFFIXED_STATES.get(phase, raw)


class L2Link(BaseModel):
    """One L2 link from ``netops.v_l2_links_auto`` (CDP / LLDP)."""
    model_config = ConfigDict(extra="forbid")

    source_device: str
    source_interface: str
    destination_device: str
    destination_interface: str
    discovery_protocol: Literal["CDP", "LLDP"] | None = None
    link_status: str | None = None


class TopologySnapshot(BaseModel):
    """Full topology snapshot — the shape the ``query_topology`` tool returns."""
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    bgp_sessions: list[BGPSession] = Field(default_factory=list)
    ospf_adjacencies: list[OSPFAdjacency] = Field(default_factory=list)
    l2_links: list[L2Link] = Field(default_factory=list)
