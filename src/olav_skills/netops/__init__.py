"""Deterministic network model, parsing and topology implementation."""

__version__ = "0.28.0"


def setup() -> None:
    """Explicit entry-point initializer — call once during plugin registration.

    Registers all olav-netops tables into the platform TableRegistry so that
    DuckDB schema migrations discover them.  Kept separate from module import
    to avoid side effects during testing or partial imports.
    """
    from olav_skills.netops.core.tables import _register_all

    _register_all()
