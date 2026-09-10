"""`serpent.spec.decode`: the read direction of the three custom sections (G dossier C3)."""

from __future__ import annotations

from serpent import U32, Env, contract, contracterror, contracttype, errorcode
from serpent.spec import build_env_meta, build_meta, build_spec_entries
from serpent.spec.decode import (
    decode_env_meta,
    decode_meta,
    decode_spec_entries,
    spec_entry_names_by_kind,
)


@contracttype
class Point:
    x: U32


@contracterror
class Err:
    Bad = errorcode(1)


@contract
class Sample:
    def get(self, env: Env, p: Point) -> U32:
        return p.x


def test_env_meta_round_trips() -> None:
    assert decode_env_meta(build_env_meta(28)) == 28


def test_meta_round_trips_in_order() -> None:
    payload = build_meta("Sample", "1.2", {"team": "devrel"})
    pairs = decode_meta(payload)
    assert ("name", "Sample") in pairs
    assert ("version", "1.2") in pairs
    assert pairs[-1] == ("team", "devrel")
    assert dict(pairs)["serpentver"]


def test_spec_entries_round_trip_and_group_by_kind() -> None:
    payload = build_spec_entries(Sample, types=(Point, Err))
    entries = decode_spec_entries(payload)
    assert len(entries) == 3
    by_kind = spec_entry_names_by_kind(entries)
    assert list(by_kind) == ["functions", "structs", "unions", "enums", "error_enums", "events"]
    assert by_kind["functions"] == ["get"]
    assert by_kind["structs"] == ["Point"]
    assert by_kind["error_enums"] == ["Err"]
    assert by_kind["unions"] == by_kind["enums"] == by_kind["events"] == []
