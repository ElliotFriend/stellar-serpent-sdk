import pathlib

import pytest
from emitter import STORAGE_INSTANCE, error_val, symbol_small
from harness import TAG_MAP_OBJECT, TAG_STRING_OBJECT, HostError, SpikeHost, mask, to_wasm

_WASM_PATH = pathlib.Path(__file__).parent / "spike.wasm"
if not _WASM_PATH.exists():
    pytest.skip(
        "spike.wasm not built - run: uv run python spikes/spike1/build.py "
        "spikes/spike1/contract_src.py -o spikes/spike1/spike.wasm",
        allow_module_level=True,
    )

WASM = str(_WASM_PATH)


def test_bump_sequence_and_error() -> None:
    host = SpikeHost(WASM)
    host.invoke("setup", [host.u32(3)])
    assert [host.invoke("bump", []) for _ in range(3)] == [host.u32(1), host.u32(2), host.u32(3)]
    err = host.invoke_expect_error("bump", [])
    assert err == (7 << 32) | 3  # contract error code 7 survives


def test_tag_check_prologue() -> None:
    host = SpikeHost(WASM)
    err = host.invoke_expect_error("setup", [2])  # Void where U32 expected
    assert err & 0xFF == 3  # rejected as an Error, not computed on


# ---------------------------------------------------------------------------
# Beyond the brief: the fidelity claims that make a green run above mean
# something. Without these, the harness could be lying in agreement.
# ---------------------------------------------------------------------------


def test_error_val_survives_the_signed_boundary_unmangled() -> None:
    """The masking bug the brief calls out, pinned to an exact value.

    `error_val(0xFFFFFFFF)` has its high bit set, so wasmtime reports it as a
    negative i64. `err & 0xFF == 3` passes either way — Python's `&` on a
    negative int sees the same low byte — so the brief's assertion alone cannot
    catch a missing mask. Assert the whole word.
    """
    host = SpikeHost(WASM)
    err = host.invoke_expect_error("setup", [2])
    assert err == error_val(0xFFFF_FFFF) == 0xFFFF_FFFF_0000_0003
    assert err > 0, "an unmasked Val would come back negative"


def test_mask_and_to_wasm_are_inverse_across_the_high_bit() -> None:
    for val in (0, 2, error_val(7), error_val(0xFFFF_FFFF), 0xFFFF_FFFF_FFFF_FFFF):
        assert mask(to_wasm(val)) == val
    assert to_wasm(0xFFFF_FFFF_0000_0003) == -4294967293  # what wasmtime is handed


def test_settings_map_round_trips_through_instance_storage() -> None:
    """Row 7's local half: what setup stored is a Map with both long-named fields."""
    host = SpikeHost(WASM)
    host.invoke("setup", [host.u32(3)])

    assert list(host.storage) == [(symbol_small("SETTINGS"), STORAGE_INSTANCE)]
    settings_val = host.storage[(symbol_small("SETTINGS"), STORAGE_INSTANCE)]
    assert settings_val & 0xFF == TAG_MAP_OBJECT

    settings = host.object_payload(settings_val)
    assert isinstance(settings, dict)
    assert sorted(settings) == ["counter_limit", "display_name"]
    assert settings["counter_limit"] == host.u32(3)
    display_name = settings["display_name"]
    assert display_name & 0xFF == TAG_STRING_OBJECT
    assert host.object_payload(display_name) == "serpent phase zero"


def test_unsorted_map_keys_are_rejected_like_the_real_host() -> None:
    """env.json: the host panics on unsorted keys. So does this, or it proves nothing."""
    host = SpikeHost(WASM)
    host._memory.write(host._store, b"bbbaaa", 0)  # "bbb" before "aaa": descending
    descriptors = b"".join(
        int(p).to_bytes(4, "little") + int(n).to_bytes(4, "little") for p, n in ((0, 3), (3, 3))
    )
    host._memory.write(host._store, descriptors, 64)
    with pytest.raises(HostError, match="ascending order"):
        host._map_new_from_linear_memory(host.u32(64), host.u32(128), host.u32(2))


def test_storage_is_per_instance() -> None:
    """Each SpikeHost is a fresh ledger, so tests cannot leak state into each other."""
    first = SpikeHost(WASM)
    first.invoke("setup", [first.u32(1)])
    assert first.storage
    assert not SpikeHost(WASM).storage
