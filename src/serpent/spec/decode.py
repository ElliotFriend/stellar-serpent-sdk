"""Decode the three Soroban custom sections serpent emits (G dossier C3; ruling E3).

The READ direction of `serpent.spec.sections`: `build_env_meta` /
`build_spec_entries` / `build_meta` produce the payloads, and these three
functions take them back apart through the same `stellar_sdk` XDR classes, so
there is one definition of each section's shape in this package and none in
the CLI (spec §10's one-codec rule, applied to XDR). `tests/unit/test_sections.py`
used to carry private copies of the two stream decoders; they moved here.

A `contractspecv0` payload is a bare STREAM of `SCSpecEntry` with no count
prefix, and `contractmetav0` a bare stream of `SCMetaEntry` -- hence the
`Unpacker` loops rather than a single `from_xdr_bytes`.
"""

from __future__ import annotations

from stellar_sdk import xdr
from xdrlib3 import Unpacker

#: The order `spec_entry_names_by_kind` REPORTS and `stellar-serpent inspect`
#: prints -- functions first because that is what a reader looks for. This is
#: NOT the emission order (ruling E7 emits declared types first, then
#: functions, then events; the deployed shapes bytes are union, enum, then
#: functions) [m2].
KIND_ORDER: tuple[str, ...] = ("functions", "structs", "unions", "enums", "error_enums", "events")


def decode_env_meta(payload: bytes) -> int:
    """The declared protocol out of a `contractenvmetav0` payload."""
    entry = xdr.SCEnvMetaEntry.from_xdr_bytes(payload)
    if entry.interface_version is None:
        raise ValueError("contractenvmetav0 entry carries no interface version")
    return int(entry.interface_version.protocol.uint32)


def decode_spec_entries(payload: bytes) -> list[xdr.SCSpecEntry]:
    """Every `SCSpecEntry` in a `contractspecv0` payload, in stream order."""
    unpacker = Unpacker(payload)
    entries: list[xdr.SCSpecEntry] = []
    while unpacker.get_position() < len(payload):
        entries.append(xdr.SCSpecEntry.unpack(unpacker))
    return entries


def decode_meta(payload: bytes) -> list[tuple[str, str]]:
    """Every `(key, value)` in a `contractmetav0` payload, in stream order."""
    unpacker = Unpacker(payload)
    pairs: list[tuple[str, str]] = []
    while unpacker.get_position() < len(payload):
        entry = xdr.SCMetaEntry.unpack(unpacker)
        if entry.v0 is None:
            raise ValueError("contractmetav0 entry is not a v0 pair")
        pairs.append((entry.v0.key.decode("utf-8"), entry.v0.val.decode("utf-8")))
    return pairs


def _name_of(entry: xdr.SCSpecEntry) -> tuple[str, str]:
    """`(kind, name)` for one entry; the field names are stellar_sdk's generated ones."""
    kind = entry.kind
    if kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_FUNCTION_V0 and entry.function_v0 is not None:
        return "functions", entry.function_v0.name.sc_symbol.decode("utf-8")
    if kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_STRUCT_V0 and entry.udt_struct_v0 is not None:
        return "structs", entry.udt_struct_v0.name.decode("utf-8")
    if kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_UNION_V0 and entry.udt_union_v0 is not None:
        return "unions", entry.udt_union_v0.name.decode("utf-8")
    if kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_ENUM_V0 and entry.udt_enum_v0 is not None:
        return "enums", entry.udt_enum_v0.name.decode("utf-8")
    if (
        kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_ERROR_ENUM_V0
        and entry.udt_error_enum_v0 is not None
    ):
        return "error_enums", entry.udt_error_enum_v0.name.decode("utf-8")
    if kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_EVENT_V0 and entry.event_v0 is not None:
        return "events", entry.event_v0.name.sc_symbol.decode("utf-8")
    raise ValueError(f"unrecognised SCSpecEntry kind {kind!r}")


def spec_entry_names_by_kind(entries: list[xdr.SCSpecEntry]) -> dict[str, list[str]]:
    """Names grouped under `KIND_ORDER`'s keys, every key present (possibly empty)."""
    grouped: dict[str, list[str]] = {kind: [] for kind in KIND_ORDER}
    for entry in entries:
        kind, name = _name_of(entry)
        grouped[kind].append(name)
    return grouped
