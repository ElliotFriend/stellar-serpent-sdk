import pathlib
import struct

import emitter
import pytest
from build import validate
from emitter import (
    HOST_FN_NAMES,
    Compiler,
    EmitError,
    HostFn,
    _Fn,
    check_linear_memory_abi,
    emit_module,
    error_val,
    load_host_fns,
    pack_u32val,
    protocol_floor,
    symbol_small,
    symbol_small_text,
)
from frontend import ContractIR, parse_contract
from sections import env_meta, spec_entries
from stellar_sdk import xdr
from xdrlib3 import Unpacker

SPIKE_DIR = pathlib.Path(__file__).parent


def test_symbol_small_matches_rust_sdk_constant() -> None:
    # "COUNTER" packed by the Rust SDK == 253576579652878 (verified in research)
    assert symbol_small("COUNTER") == 253576579652878


def test_symbol_small_rejects_over_9() -> None:
    with pytest.raises(ValueError):
        symbol_small("counter_limit")  # 13 chars — must NOT silently overflow


def test_symbol_small_round_trips() -> None:
    """The decoder shares its alphabet with the encoder, so it cannot drift."""
    for text in ("COUNTER", "SETTINGS", "COUNT", "a", "_", "z9Z_0", "abcdefghi"):
        assert symbol_small_text(symbol_small(text)) == text
    with pytest.raises(ValueError, match="not a SymbolSmall"):
        symbol_small_text(pack_u32val(7))


def test_error_val_encoding() -> None:
    assert error_val(7) == (7 << 32) | 3


def test_u32val() -> None:
    assert pack_u32val(0) == 4 and pack_u32val(3_000_000_000) == (3_000_000_000 << 32) | 4


def test_env_meta_golden_bytes() -> None:
    # SCEnvMetaEntry(kind=0) + protocol=27 + preRelease=0, XDR-encoded: 12 bytes.
    assert env_meta(27) == bytes.fromhex("000000000000001b00000000")


# ----------------------------------------------------------------------------
# Beyond the brief's goldens: the properties the spike is actually claiming.
# ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ir() -> ContractIR:
    return parse_contract(str(SPIKE_DIR / "contract_src.py"))


@pytest.fixture(scope="module")
def host_fns() -> dict[str, HostFn]:
    return load_host_fns(SPIKE_DIR / "env.json", HOST_FN_NAMES)


@pytest.fixture(scope="module")
def wasm(ir: ContractIR, host_fns: dict[str, HostFn]) -> bytes:
    return emit_module(ir, host_fns, protocol=27, meta_pairs={"serpent": "test"})


def test_host_codes_come_from_env_json(host_fns: dict[str, HostFn]) -> None:
    """The module/field codes are read from the pinned env.json, never hardcoded."""
    assert {(f.module, f.field) for f in host_fns.values()} == {
        ("l", "_"),  # put_contract_data
        ("l", "0"),  # has_contract_data
        ("l", "1"),  # get_contract_data
        ("m", "9"),  # map_new_from_linear_memory
        ("m", "1"),  # map_get
        ("b", "j"),  # symbol_new_from_linear_memory
        ("b", "i"),  # string_new_from_linear_memory
        ("x", "5"),  # fail_with_error
    }


def test_missing_host_function_is_an_error() -> None:
    with pytest.raises(EmitError):
        load_host_fns(SPIKE_DIR / "env.json", {"put_contract_data", "no_such_host_fn"})


def test_import_floor_is_the_base_protocol(host_fns: dict[str, HostFn]) -> None:
    # None of the eight carries a min_supported_protocol in v28.0.2.
    assert all(f.min_protocol is None for f in host_fns.values())
    assert protocol_floor(host_fns, 20) == 20


def test_module_has_memory_export_and_data(wasm: bytes) -> None:
    assert wasm.startswith(b"\x00asm\x01\x00\x00\x00")
    ids = _section_ids(wasm)
    for sid in (1, 2, 3, 5, 7, 10, 11):  # type/import/function/memory/export/code/data
        assert sid in ids, f"missing section {sid}"
    assert ids == sorted(ids), "sections must appear in ascending id order"
    assert b"memory" in wasm  # the export name
    assert b"counter_limitdisplay_name" in wasm  # the literal pool
    assert b"serpent phase zero" in wasm


def test_module_carries_the_three_custom_sections(wasm: bytes) -> None:
    for name in (b"contractenvmetav0", b"contractspecv0", b"contractmetav0"):
        assert name in wasm


def test_long_field_names_are_never_packed_as_small_symbols(wasm: bytes) -> None:
    """The 13- and 12-char field names must reach the host as bytes, not as Vals."""
    for name in (b"counter_limit", b"display_name"):
        assert name in wasm
        with pytest.raises(ValueError):
            symbol_small(name.decode())


def test_map_keys_are_static_pointer_length_descriptors(ir: ContractIR, wasm: bytes) -> None:
    """env.json: map keys are 8-byte ``(u32 ptr, u32 len)`` pairs, not Symbol Vals.

    Getting this wrong yields a module that validates and then panics in the
    host, so assert the exact descriptor bytes. The names are interned back to
    back from offset 0, in the ascending byte order the host requires.
    """
    names = [n.encode() for n, _ in ir.structs["Settings"]]
    assert names == sorted(names), "field names must reach the host in ascending order"
    descriptors = b""
    offset = 0
    for name in names:
        descriptors += struct.pack("<II", offset, len(name))
        offset += len(name)
    assert descriptors in wasm


def test_module_validates(wasm: bytes) -> None:
    validate(wasm)  # raises SystemExit if wasm-tools rejects it


def test_spec_stream_round_trips(ir: ContractIR) -> None:
    """Decode the spec payload back with the SDK: struct, error enum, both fns."""
    payload = spec_entries(ir)
    unpacker = Unpacker(payload)
    entries = []
    while unpacker.get_position() < len(payload):
        entries.append(xdr.SCSpecEntry.unpack(unpacker))
    kinds = [e.kind for e in entries]
    assert kinds == [
        xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_STRUCT_V0,
        xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_ERROR_ENUM_V0,
        xdr.SCSpecEntryKind.SC_SPEC_ENTRY_FUNCTION_V0,
        xdr.SCSpecEntryKind.SC_SPEC_ENTRY_FUNCTION_V0,
    ]
    struct = entries[0].udt_struct_v0
    assert struct is not None
    assert [f.name for f in struct.fields] == [b"counter_limit", b"display_name"]

    err = entries[1].udt_error_enum_v0
    assert err is not None
    assert err.cases[0].name == b"LimitExceeded" and err.cases[0].value.uint32 == 7

    setup, bump = entries[2].function_v0, entries[3].function_v0
    assert setup is not None and bump is not None
    assert setup.outputs == []  # void return -> empty array<1>
    assert bump.outputs[0].type == xdr.SCSpecType.SC_SPEC_TYPE_U32
    assert setup.doc.startswith(b"Store settings")  # docstring -> spec doc


def test_stack_imbalance_is_caught_before_bytes_exist() -> None:
    """The operand-stack tracker is the gate, not wasm-tools."""
    fn = _Fn(name="broken", nparams=0)
    fn.i64_const(1)
    fn.i64_const(2)  # two results where the signature promises one
    with pytest.raises(EmitError, match="operand stack"):
        fn.finish()


def test_leaked_operand_before_return_is_caught() -> None:
    """A value pushed and never consumed must fail at the `return`, not at `end`.

    `return` makes wasm's stack polymorphic, so a check at `end` never sees the
    leak -- which is exactly how this bug hid.
    """
    fn = _Fn(name="leaky", nparams=0)
    fn.i64_const(1)  # e.g. a put_contract_data result whose `drop` went missing
    fn.i64_const(2)  # the actual return value
    with pytest.raises(EmitError, match="operand stack"):
        fn.ret()


def test_missing_drop_after_a_storage_write_is_caught(
    monkeypatch: pytest.MonkeyPatch, ir: ContractIR, host_fns: dict[str, HostFn]
) -> None:
    """The regression in full: omit one `drop` and the whole build must fail.

    Before the balance check moved into `ret()`, this produced a module that
    `wasm-tools validate` accepted.
    """

    def put_without_drop(
        self: Compiler, fn: _Fn, key: object, value: object, storage: int
    ) -> None:
        self.expr(fn, key)  # type: ignore[arg-type]
        self.expr(fn, value)  # type: ignore[arg-type]
        fn.i64_const(storage)
        self.call_host(fn, "put_contract_data")  # result deliberately not dropped

    monkeypatch.setattr(Compiler, "put", put_without_drop)
    with pytest.raises(EmitError, match="operand stack"):
        emit_module(ir, host_fns, protocol=27, meta_pairs={})


def test_memory_export_name_is_asserted_at_build_time(
    monkeypatch: pytest.MonkeyPatch, ir: ContractIR, host_fns: dict[str, HostFn]
) -> None:
    """Renaming the memory export must fail the build, not ship a trapping module."""
    monkeypatch.setattr(emitter, "MEMORY_EXPORT_NAME", "linear_mem")
    with pytest.raises(EmitError, match='export name "memory"'):
        emit_module(ir, host_fns, protocol=27, meta_pairs={})


def test_linear_memory_abi_check_requires_exactly_one_memory() -> None:
    users = {"symbol_new_from_linear_memory"}
    check_linear_memory_abi(users, 1, ["setup", "memory"])  # the good case
    with pytest.raises(EmitError, match="declares 0 memories"):
        check_linear_memory_abi(users, 0, ["setup", "memory"])
    with pytest.raises(EmitError, match="declares 2 memories"):
        check_linear_memory_abi(users, 2, ["setup", "memory"])
    with pytest.raises(EmitError, match='export name "memory"'):
        check_linear_memory_abi(users, 1, ["setup"])


def test_linear_memory_abi_check_is_scoped_to_memory_users() -> None:
    """A module that never touches linear memory needs no memory export."""
    check_linear_memory_abi({"put_contract_data", "map_get"}, 0, ["setup"])


def test_unclosed_control_frame_is_caught() -> None:
    fn = _Fn(name="dangling", nparams=0)
    fn.i64_const(0)
    fn.i64_const(0)
    fn.relop_i64(0x51)  # i64.eq -> i32
    fn.begin_if(None)
    with pytest.raises(EmitError, match="control frame"):
        fn.finish()


def _section_ids(wasm: bytes) -> list[int]:
    """Ids of the non-custom sections, in the order they appear."""
    ids: list[int] = []
    i = 8
    while i < len(wasm):
        sid = wasm[i]
        i += 1
        size, i = _uleb(wasm, i)
        if sid != 0:
            ids.append(sid)
        i += size
    return ids


def _uleb(buf: bytes, i: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while True:
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7
