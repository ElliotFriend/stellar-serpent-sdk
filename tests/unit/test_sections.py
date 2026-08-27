"""`serpent.spec.sections`: the three Soroban custom-section payloads.

Evidence classes are kept strictly apart here, exactly as
`tests/goldens/README.md` demands:

* **ON-CHAIN-verified** -- `env_meta_27.bin`, and the whole `spike.wasm` block
  below: that wasm is byte-identical to the artifact fetched back off testnet
  (sha256 pinned here, recorded in `spikes/spike1/DEPLOY_LOG.md`), so its
  `contractspecv0` section is literally the bytes a live network accepted and
  the official CLI rendered as the recorded interface.
* **RUST-SDK-BYTE-COMPAT-verified** -- `counter_spec.bin`, reproduced from the
  hand-rolled encoder recorded in `spikes/spike1/reference/mkmeta.py`.
* **serpent-self-pinned** -- `build_meta`'s bytes. There is no external
  artifact to compare against (the key set is serpent's own), so this is a
  regression pin and is labeled as one, never as verification.

Parsing is done with `stellar_sdk.xdr.SCSpecEntry.unpack` over an `xdrlib3`
stream (the payload is a bare stream of entries, no outer length prefix), so
the tests read the emitted bytes back through the protocol's own decoder.
"""

import hashlib
import pathlib
import shutil
import subprocess

import pytest
from stellar_sdk import xdr
from xdrlib3 import Unpacker

import serpent
from serpent import (
    I128,
    U32,
    Address,
    Bytes32,
    Env,
    Event,
    Map,
    String,
    Symbol,
    contract,
    contracterror,
    contractevent,
    contracttype,
    errorcode,
)
from serpent._host import declared_protocol
from serpent.spec import (
    SpecDocError,
    SpecNameError,
    SpecTypeError,
    build_env_meta,
    build_meta,
    build_spec_entries,
)
from tests.fixtures import token_style

# The same eight Phase 0 host functions `test_protocol_floor.py` pins, imported
# rather than copied so the two files cannot drift.
from tests.unit.test_protocol_floor import PHASE0_FNS

GOLDENS = pathlib.Path(__file__).parent.parent / "goldens"
SPIKE_WASM = pathlib.Path(__file__).parent.parent.parent / "spikes" / "spike1" / "spike.wasm"

#: `spikes/spike1/DEPLOY_LOG.md`: sha256 of the wasm FETCHED BACK off testnet,
#: which `cmp` reported identical to this local file. Pinning it is what makes
#: the assertions below on-chain-anchored rather than merely local.
SPIKE_WASM_SHA256 = "bc2e806302f655686084f5c604b4e642900e0fa7812310378667a9cabe4a9920"

#: The exact stdout of `stellar contract info interface --wasm spike.wasm`,
#: recorded in `DEPLOY_LOG.md` and there shown identical to the `--id` render
#: taken from the live network.
RECORDED_INTERFACE_RENDER = """#[soroban_sdk::contractargs(name = "Args")]
#[soroban_sdk::contractclient(name = "Client")]
pub trait Contract {
    fn setup(env: soroban_sdk::Env, counter_limit: u32);
    fn bump(env: soroban_sdk::Env) -> u32;
}
#[soroban_sdk::contracttype(export = false)]
#[derive(Debug, Clone, Eq, PartialEq, Ord, PartialOrd)]
pub struct Settings {
    pub counter_limit: u32,
    pub display_name: soroban_sdk::String,
}
#[soroban_sdk::contracterror(export = false)]
#[derive(Debug, Copy, Clone, Eq, PartialEq, Ord, PartialOrd)]
pub enum Error {
    LimitExceeded = 7,
}
"""


# --- fixtures ---------------------------------------------------------------


@contract
class Counter:
    """The Phase 0 counter interface: two zero-arg u32 getters, no docs.

    (A *contract* class's docstring is not emitted: `contractspecv0` has no
    entry for the contract itself, only its functions -- which is why this text
    does not disturb the 64-byte golden. `@contracttype` and `@contracterror`
    class docstrings ARE emitted, on their UDT entries.)
    """

    def get(self) -> U32:
        return U32(0)

    def increment(self) -> U32:
        return U32(0)


@contracttype
class Settings:
    counter_limit: U32
    display_name: String


@contracterror
class Error:
    LimitExceeded = errorcode(7)


@contract
class Spike:
    """serpent's re-authoring of `spikes/spike1/contract_src.py`.

    The docstrings below are transcribed EXACTLY from that (deployed) contract:
    they are what the on-chain `contractspecv0` section carries, so the byte
    equality asserted further down depends on them character for character.
    """

    def setup(self, env: Env, counter_limit: U32) -> None:
        """Store settings with a long-named field and a string literal."""
        env.storage().instance().set(Symbol("SETTINGS"), counter_limit)

    def bump(self, env: Env) -> U32:
        """Increment a persistent counter; raise LimitExceeded above the limit."""
        return env.storage().persistent().get(Symbol("COUNT"), U32, default=U32(0))


@contractevent
class Moved(Event):
    amount: I128


# --- helpers ----------------------------------------------------------------


def _unpack(payload: bytes) -> list[xdr.SCSpecEntry]:
    """Decode a `contractspecv0` payload: a bare stream of `SCSpecEntry`."""
    unpacker = Unpacker(payload)
    entries: list[xdr.SCSpecEntry] = []
    while unpacker.get_position() < len(payload):
        entries.append(xdr.SCSpecEntry.unpack(unpacker))
    return entries


def _unpack_meta(payload: bytes) -> list[tuple[bytes, bytes]]:
    unpacker = Unpacker(payload)
    pairs: list[tuple[bytes, bytes]] = []
    while unpacker.get_position() < len(payload):
        entry = xdr.SCMetaEntry.unpack(unpacker)
        assert entry.v0 is not None
        pairs.append((entry.v0.key, entry.v0.val))
    return pairs


def _shape(entries: list[xdr.SCSpecEntry]) -> list[tuple[str, str]]:
    """`(kind, name)` per entry -- the order assertion, independent of bytes."""
    shape: list[tuple[str, str]] = []
    for entry in entries:
        if entry.function_v0 is not None:
            shape.append(("fn", entry.function_v0.name.sc_symbol.decode()))
        elif entry.udt_struct_v0 is not None:
            shape.append(("struct", entry.udt_struct_v0.name.decode()))
        elif entry.udt_error_enum_v0 is not None:
            shape.append(("error_enum", entry.udt_error_enum_v0.name.decode()))
        else:  # pragma: no cover - no other kind is emitted in M1-B
            raise AssertionError(f"unexpected entry kind: {entry.kind}")
    return shape


def _function(entries: list[xdr.SCSpecEntry], name: str) -> xdr.SCSpecFunctionV0:
    for entry in entries:
        if entry.function_v0 is not None and entry.function_v0.name.sc_symbol == name.encode():
            return entry.function_v0
    raise AssertionError(f"no function entry named {name!r} in {_shape(entries)}")


def _wasm_custom_section(wasm: bytes, name: str) -> bytes:
    """The payload of one wasm custom section (id 0), by name.

    A deliberately tiny reader: section id byte, ULEB128 size, then for custom
    sections a ULEB128-prefixed name followed by the payload.
    """

    def uleb(data: bytes, index: int) -> tuple[int, int]:
        value = shift = 0
        while True:
            byte = data[index]
            index += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value, index
            shift += 7

    index = 8  # skip the 8-byte wasm preamble (magic + version)
    while index < len(wasm):
        section_id = wasm[index]
        index += 1
        size, index = uleb(wasm, index)
        body = wasm[index : index + size]
        index += size
        if section_id == 0:
            length, offset = uleb(body, 0)
            if body[offset : offset + length].decode() == name:
                return body[offset + length :]
    raise AssertionError(f"no custom section named {name!r}")


# --- build_env_meta ---------------------------------------------------------


def test_env_meta_27_is_byte_equal_to_the_on_chain_golden() -> None:
    golden = (GOLDENS / "env_meta_27.bin").read_bytes()
    assert len(golden) == 12
    assert build_env_meta(27) == golden


def test_env_meta_matches_the_deployed_contracts_env_meta_section() -> None:
    """The same 12 bytes, read out of the on-chain-verified wasm."""
    wasm = SPIKE_WASM.read_bytes()
    assert build_env_meta(27) == _wasm_custom_section(wasm, "contractenvmetav0")


def test_env_meta_round_trips_through_stellar_sdk() -> None:
    entry = xdr.SCEnvMetaEntry.from_xdr_bytes(build_env_meta(27))
    assert entry.kind == xdr.SCEnvMetaKind.SC_ENV_META_KIND_INTERFACE_VERSION
    assert entry.interface_version is not None
    assert entry.interface_version.protocol.uint32 == 27
    assert entry.interface_version.pre_release.uint32 == 0


def test_declared_protocol_feeds_build_env_meta() -> None:
    """The Task 2 -> Task 4 seam: the eight Phase 0 functions are ungated, so
    an unspecified target declares the floor (20), not the default target."""
    protocol = declared_protocol(PHASE0_FNS, None)
    assert protocol == 20
    assert build_env_meta(protocol) == bytes.fromhex("000000000000001400000000")


@pytest.mark.parametrize("protocol", [-1, 2**32])
def test_env_meta_rejects_an_out_of_range_protocol(protocol: int) -> None:
    with pytest.raises(ValueError, match="u32"):
        build_env_meta(protocol)


@pytest.mark.parametrize("protocol", [True, 27.0, "27", None])
def test_env_meta_rejects_a_non_int_protocol(protocol: object) -> None:
    """`bool` included: it is an `int` subclass, but `build_env_meta(True)` is
    never what an author meant."""
    with pytest.raises(TypeError, match="protocol"):
        build_env_meta(protocol)  # type: ignore[arg-type]


# --- counter golden (RUST-SDK-BYTE-COMPAT) ----------------------------------


def test_counter_spec_is_byte_equal_to_the_rust_sdk_compat_golden() -> None:
    golden = (GOLDENS / "counter_spec.bin").read_bytes()
    assert len(golden) == 64
    assert build_spec_entries(Counter) == golden


def test_counter_golden_round_trips_through_stellar_sdk() -> None:
    entries = _unpack((GOLDENS / "counter_spec.bin").read_bytes())
    assert _shape(entries) == [("fn", "get"), ("fn", "increment")]
    for entry in entries:
        function = entry.function_v0
        assert function is not None
        assert function.doc == b""
        assert function.inputs == []
        assert [output.type for output in function.outputs] == [xdr.SCSpecType.SC_SPEC_TYPE_U32]


# --- the ON-CHAIN-anchored check -------------------------------------------


def test_spike_wasm_is_the_artifact_fetched_back_off_testnet() -> None:
    """Anchor: without this pin, everything below is only a local comparison."""
    assert hashlib.sha256(SPIKE_WASM.read_bytes()).hexdigest() == SPIKE_WASM_SHA256


def test_spike_entries_are_byte_identical_to_the_deployed_spec_section() -> None:
    """serpent's `contractspecv0` for spike1's interface == the on-chain bytes.

    Stronger than the structural equality the plan asked for, and achievable
    because serpent takes its docs from the same docstrings the spike's own
    frontend did. If a future change breaks this, fall back to the structural
    assertion in the next test and say so -- do not weaken this one silently.
    """
    deployed = _wasm_custom_section(SPIKE_WASM.read_bytes(), "contractspecv0")
    assert build_spec_entries(Spike, types=(Settings, Error)) == deployed


def test_spike_entries_structurally_match_the_recorded_on_chain_interface() -> None:
    """The same claim read through the decoder, one field at a time -- so a
    failure says *what* diverged from the deployed interface."""
    entries = _unpack(build_spec_entries(Spike, types=(Settings, Error)))
    assert _shape(entries) == [
        ("struct", "Settings"),
        ("error_enum", "Error"),
        ("fn", "setup"),
        ("fn", "bump"),
    ]

    struct = entries[0].udt_struct_v0
    assert struct is not None
    assert struct.lib == b""
    assert [(field.name, field.type.type) for field in struct.fields] == [
        (b"counter_limit", xdr.SCSpecType.SC_SPEC_TYPE_U32),
        (b"display_name", xdr.SCSpecType.SC_SPEC_TYPE_STRING),
    ]

    enum = entries[1].udt_error_enum_v0
    assert enum is not None
    assert [(case.name, case.value.uint32) for case in enum.cases] == [(b"LimitExceeded", 7)]

    setup = _function(entries, "setup")
    # `env` is NOT a spec input: the CLI renders `fn setup(env: soroban_sdk::Env,
    # counter_limit: u32)` by re-inserting it, which is exactly why the recorded
    # on-chain render shows it while the spec does not.
    assert [(inp.name, inp.type.type) for inp in setup.inputs] == [
        (b"counter_limit", xdr.SCSpecType.SC_SPEC_TYPE_U32)
    ]
    assert setup.outputs == []
    assert setup.doc == b"Store settings with a long-named field and a string literal."

    bump = _function(entries, "bump")
    assert bump.inputs == []
    assert [output.type for output in bump.outputs] == [xdr.SCSpecType.SC_SPEC_TYPE_U32]


@pytest.mark.skipif(shutil.which("stellar") is None, reason="stellar CLI not installed")
def test_stellar_cli_renders_those_bytes_as_the_recorded_interface() -> None:
    """Closes the loop: the previous test proves serpent's bytes ARE the
    deployed section; this proves the official CLI reads that section back as
    the interface recorded on-chain in DEPLOY_LOG.md. Skipped when the CLI is
    absent (CI has no stellar binary), never silently passed."""
    result = subprocess.run(
        ["stellar", "contract", "info", "interface", "--wasm", str(SPIKE_WASM)],
        capture_output=True,
        text=True,
        check=True,
    )
    # Only trailing newlines are normalized: the CLI ends its output with a
    # blank line, which the DEPLOY_LOG.md code fence did not preserve. Every
    # other character, including indentation, is compared as-is.
    assert result.stdout.rstrip("\n") == RECORDED_INTERFACE_RENDER.rstrip("\n")


# --- build_spec_entries: constructor, order, env, docs ----------------------


def test_constructor_is_emitted_first_and_renamed() -> None:
    entries = _unpack(build_spec_entries(token_style.TokenStyle))
    assert _shape(entries)[0] == ("fn", "__constructor")


def test_constructor_arg_names_are_discoverable_for_the_cli() -> None:
    """The Stellar CLI derives `stellar contract deploy --arg-name` flags from
    this entry; dropping it makes a parameterized contract undeployable."""
    constructor = _function(_unpack(build_spec_entries(token_style.TokenStyle)), "__constructor")
    assert [inp.name for inp in constructor.inputs] == [b"admin", b"name"]
    assert [inp.type.type for inp in constructor.inputs] == [
        xdr.SCSpecType.SC_SPEC_TYPE_ADDRESS,
        xdr.SCSpecType.SC_SPEC_TYPE_STRING,
    ]
    assert constructor.outputs == []


def test_zero_argument_constructor_is_still_emitted() -> None:
    @contract
    class Bare:
        def __init__(self, env: Env) -> None:
            env.storage().instance().set(Symbol("K"), U32(0))

        def ping(self) -> U32:
            return U32(1)

    entries = _unpack(build_spec_entries(Bare))
    assert _shape(entries) == [("fn", "__constructor"), ("fn", "ping")]
    assert _function(entries, "__constructor").inputs == []


def test_a_contract_without_init_emits_no_constructor_entry() -> None:
    assert _shape(_unpack(build_spec_entries(Counter))) == [("fn", "get"), ("fn", "increment")]


def test_entry_order_is_structs_then_error_enums_then_functions() -> None:
    """Pinned independently of any golden: `types` order for the UDTs,
    `__constructor` first then declaration order for the functions."""
    entries = _unpack(
        build_spec_entries(
            token_style.TokenStyle,
            types=(token_style.BalanceKey, Settings, token_style.TokenError, Error),
        )
    )
    assert _shape(entries) == [
        ("struct", "BalanceKey"),
        ("struct", "Settings"),
        ("error_enum", "TokenError"),
        ("error_enum", "Error"),
        ("fn", "__constructor"),
        ("fn", "name"),
        ("fn", "is_admin"),
        ("fn", "balance"),
        ("fn", "mint"),
        ("fn", "transfer"),
    ]


def test_env_is_dropped_from_every_signature() -> None:
    entries = _unpack(build_spec_entries(token_style.TokenStyle))
    for entry in entries:
        function = entry.function_v0
        assert function is not None
        assert b"env" not in [inp.name for inp in function.inputs]
    assert [inp.name for inp in _function(entries, "is_admin").inputs] == [b"who"]


def test_env_in_a_non_leading_position_is_refused() -> None:
    @contract
    class Misplaced:
        def odd(self, who: Address, env: Env) -> U32:
            return U32(0)

    with pytest.raises(SpecTypeError, match="Env"):
        build_spec_entries(Misplaced)


def test_method_docs_are_the_full_cleandoc_text_not_the_first_line() -> None:
    @contract
    class Documented:
        def act(self) -> U32:
            """First line.

            Second paragraph, indented in the source, dedented by cleandoc.
            """
            return U32(0)

    doc = _function(_unpack(build_spec_entries(Documented)), "act").doc
    assert doc == (
        b"First line.\n\nSecond paragraph, indented in the source, dedented by cleandoc."
    )


def test_an_undocumented_method_gets_an_empty_doc_not_an_inherited_one() -> None:
    """`inspect.getdoc` walks up to `object.__init__`'s docstring, which must
    never leak into a contract spec."""
    constructor = _function(_unpack(build_spec_entries(token_style.TokenStyle)), "__constructor")
    assert constructor.doc == b""


def test_an_over_long_doc_raises_spec_doc_error_naming_the_method() -> None:
    @contract
    class Verbose:
        def act(self) -> U32:
            return U32(0)

    Verbose.act.__doc__ = "x" * 1025
    with pytest.raises(SpecDocError) as exc_info:
        build_spec_entries(Verbose)
    message = str(exc_info.value)
    assert "act" in message
    assert "1025" in message
    assert "1024" in message


def test_a_doc_at_the_limit_is_accepted() -> None:
    @contract
    class AtLimit:
        def act(self) -> U32:
            return U32(0)

    AtLimit.act.__doc__ = "x" * 1024
    assert _function(_unpack(build_spec_entries(AtLimit)), "act").doc == b"x" * 1024


def test_doc_length_is_counted_in_encoded_bytes_not_characters() -> None:
    @contract
    class Wide:
        def act(self) -> U32:
            return U32(0)

    Wide.act.__doc__ = "é" * 513  # 1026 UTF-8 bytes, 513 characters
    with pytest.raises(SpecDocError, match="1026"):
        build_spec_entries(Wide)


# --- build_spec_entries: UDT entries and refusals ---------------------------


def test_struct_entry_carries_its_fields_with_empty_per_field_docs() -> None:
    """Per-field docs are `b""` in M1-B: `_serpent_type_` records no per-field
    doc, so there is nothing to emit (a real gap, noted for sub-plan C)."""
    entries = _unpack(build_spec_entries(Counter, types=(token_style.BalanceKey,)))
    struct = entries[0].udt_struct_v0
    assert struct is not None
    assert struct.name == b"BalanceKey"
    assert [(field.name, field.doc, field.type.type) for field in struct.fields] == [
        (b"owner", b"", xdr.SCSpecType.SC_SPEC_TYPE_ADDRESS)
    ]


def test_struct_docstring_is_emitted_and_the_dataclass_default_is_not() -> None:
    """`@contracttype` applies `dataclasses.dataclass`, which SYNTHESIZES a
    `__doc__` (`"Name(field: type)"`) for a class that has none. That synthetic
    string must never reach the spec."""
    documented = _unpack(build_spec_entries(Counter, types=(token_style.BalanceKey,)))[0]
    assert documented.udt_struct_v0 is not None
    assert documented.udt_struct_v0.doc == (
        b"A struct storage key -- the widened surface, not just `Symbol`."
    )

    undocumented = _unpack(build_spec_entries(Counter, types=(Settings,)))[0]
    assert undocumented.udt_struct_v0 is not None
    assert undocumented.udt_struct_v0.doc == b""


def test_error_enum_entry_carries_every_errorcode_case() -> None:
    entries = _unpack(build_spec_entries(Counter, types=(token_style.TokenError,)))
    enum = entries[0].udt_error_enum_v0
    assert enum is not None
    assert enum.name == b"TokenError"
    assert [(case.name, case.doc, case.value.uint32) for case in enum.cases] == [
        (b"InsufficientBalance", b"", 1),
        (b"Unauthorized", b"", 2),
    ]


def test_an_event_class_in_types_is_refused_pointing_at_sub_plan_e() -> None:
    """`SCSpecEventV0` needs a `data_format` and per-parameter `location` that
    M1-A's event metadata does not carry -- a guessed entry would be a
    valid-but-lying spec."""
    with pytest.raises(SpecTypeError) as exc_info:
        build_spec_entries(Counter, types=(Moved,))
    message = str(exc_info.value)
    assert "Moved" in message
    assert "sub-plan E" in message


def test_a_non_contract_class_is_refused() -> None:
    with pytest.raises(SpecTypeError, match="Settings"):
        build_spec_entries(Settings)


def test_an_undecorated_class_in_types_is_refused() -> None:
    class Plain:
        pass

    with pytest.raises(SpecTypeError, match="Plain"):
        build_spec_entries(Counter, types=(Plain,))


def test_a_contract_class_in_types_is_refused() -> None:
    with pytest.raises(SpecTypeError, match="Counter"):
        build_spec_entries(Counter, types=(Counter,))


def test_the_same_type_declared_twice_is_refused() -> None:
    """A UDT reference names a type, so two entries under one name is a spec
    that cannot be resolved -- the likely sub-plan D bug is collecting the
    module's classes twice."""
    with pytest.raises(SpecNameError, match="Settings"):
        build_spec_entries(Counter, types=(Settings, Settings))


def test_two_types_sharing_a_name_are_refused() -> None:
    """The same failure reached the other way: same spec name, different
    classes (two modules each declaring a `Settings`)."""

    @contracttype
    class Imported:
        other: U32

    Imported.__name__ = "Settings"  # as if imported from another module
    with pytest.raises(SpecNameError, match="Settings"):
        build_spec_entries(Counter, types=(Settings, Imported))


def test_omitting_types_silently_drops_the_structs_it_references() -> None:
    """The documented footgun: `build_spec_entries` cannot discover the structs
    a contract mentions, so sub-plan D must collect them from the module. The
    reference is still emitted -- only the UDT *entry* is missing."""
    entries = _unpack(build_spec_entries(token_style.TokenStyle))
    assert all(kind == "fn" for kind, _ in _shape(entries))


# --- name caps --------------------------------------------------------------


def test_an_over_long_type_name_raises_spec_name_error_naming_it() -> None:
    """Class names are never checked by the decorators, so sections owns this
    cap -- and owns it at the DECLARATION, before typemap sees a reference."""

    @contracttype
    class Long:
        field: U32

    Long.__name__ = "L" * 61
    with pytest.raises(SpecNameError) as exc_info:
        build_spec_entries(Counter, types=(Long,))
    message = str(exc_info.value)
    assert "L" * 61 in message
    assert "60" in message


def test_a_type_name_at_the_cap_is_accepted() -> None:
    @contracttype
    class AtCap:
        field: U32

    AtCap.__name__ = "A" * 60
    entries = _unpack(build_spec_entries(Counter, types=(AtCap,)))
    assert _shape(entries)[0] == ("struct", "A" * 60)


def test_an_over_long_error_case_name_raises_spec_name_error_naming_it() -> None:
    """`@contracterror` does not check member names at all, so sections must.

    Declared through `type()` rather than a `class` statement only because a
    61-character member name is unreadable inline -- this is the same
    declaration a contract author could write by hand, not a metadata rewrite.
    """
    wordy: type = contracterror(type("Wordy", (), {"C" * 61: errorcode(1)}))
    with pytest.raises(SpecNameError, match="C" * 61):
        build_spec_entries(Counter, types=(wordy,))


def test_an_over_long_function_name_raises_spec_name_error_naming_it() -> None:
    """A guard, not a reachable authoring path: `@contract` caps method names at
    30 itself, so the metadata is written directly here -- the shape sub-plan D
    would produce if it ever built `_serpent_type_` without the decorator.
    Sections must not trust its input."""

    @contract
    class Sneaky:
        def ok(self) -> U32:
            return U32(0)

    # A decorator-installed attribute is invisible to mypy by design (see the
    # decorators module docstring), hence the narrow ignore on every rewrite.
    Sneaky._serpent_type_ = {  # type: ignore[attr-defined]
        "kind": "contract",
        "methods": [("m" * 31, [], type(None))],
    }
    with pytest.raises(SpecNameError) as exc_info:
        build_spec_entries(Sneaky)
    assert "m" * 31 in str(exc_info.value)
    assert "30" in str(exc_info.value)


def test_an_over_long_parameter_name_raises_spec_name_error() -> None:
    """Parameter names are checked NOWHERE else: `@contract` validates method
    and field names, never argument names, and the XDR caps an input name at 30."""

    @contract
    class Wordy2:
        def act(self, aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa: U32) -> U32:  # 31 characters
            return U32(0)

    with pytest.raises(SpecNameError) as exc_info:
        build_spec_entries(Wordy2)
    assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in str(exc_info.value)
    assert "30" in str(exc_info.value)


def test_the_local_charset_check_agrees_with_val_is_valid_symbol() -> None:
    """`sections` cannot call `val.is_valid_symbol` for the 60-byte type-name
    fields (it also enforces the 32-character Symbol cap), so it re-uses
    `val.SYMBOL_CHARS` directly. This pins the two against drift for every
    name short enough for both to apply."""
    from serpent import val
    from serpent.spec.sections import _check_name

    @contracttype
    class Owner:
        field: U32

    for candidate in ("ok", "Ok_9", "_x", "a" * 30):
        assert val.is_valid_symbol(candidate)
        assert _check_name(candidate, Owner, "field", 30) == candidate
    for bad in ("no-dashes", "has space", "dot.ted", "é", ""):
        assert not val.is_valid_symbol(bad)
        with pytest.raises(SpecNameError):
            _check_name(bad, Owner, "field", 30)


def test_a_non_symbol_name_raises_spec_name_error() -> None:
    """Also a guard: `def no-dashes` is not even parseable Python."""

    @contract
    class Dashed:
        def ok(self) -> U32:
            return U32(0)

    Dashed._serpent_type_ = {  # type: ignore[attr-defined]
        "kind": "contract",
        "methods": [("no-dashes", [], type(None))],
    }
    with pytest.raises(SpecNameError, match="Symbol"):
        build_spec_entries(Dashed)


# --- build_meta -------------------------------------------------------------


def test_meta_prepends_the_reserved_keys_in_order() -> None:
    pairs = _unpack_meta(build_meta("counter", "1.0.0"))
    assert pairs == [
        (b"name", b"counter"),
        (b"version", b"1.0.0"),
        (b"serpentver", serpent.__version__.encode()),
    ]


def test_meta_appends_user_pairs_in_iteration_order() -> None:
    pairs = _unpack_meta(build_meta("c", "1", {"zeta": "z", "alpha": "a"}))
    assert pairs[3:] == [(b"zeta", b"z"), (b"alpha", b"a")]


@pytest.mark.parametrize("key", ["name", "version", "serpentver"])
def test_meta_refuses_a_user_key_colliding_with_a_reserved_one(key: str) -> None:
    with pytest.raises(ValueError, match=key):
        build_meta("c", "1", {key: "x"})


#: `build_meta("counter", "1.0.0")` with `serpent.__version__ == "0.0.1"`.
#: A serpent-SELF-PIN, not verification: the reserved key set is serpent's own
#: invention, so there is no external artifact to compare against. Its value is
#: catching an accidental change to the key names, their order, or the framing.
META_SELF_PIN = (
    "00000000000000046e616d6500000007636f756e74657200000000000000000776657273696f6e"
    "0000000005312e302e30000000000000000000000a73657270656e74766572000000000005302e"
    "302e31000000"
)


def test_meta_is_pinned_against_serpents_own_output() -> None:
    assert serpent.__version__ == "0.0.1", "regenerate META_SELF_PIN for the new version"
    assert build_meta("counter", "1.0.0").hex() == META_SELF_PIN
    assert len(build_meta("counter", "1.0.0")) == 84


@pytest.mark.parametrize(("name", "version"), [("", "1.0.0"), ("counter", "")])
def test_meta_requires_a_name_and_a_version(name: str, version: str) -> None:
    """Both keys are always present in the payload, so an empty value would
    publish a contract whose name or version reads as blank."""
    with pytest.raises(ValueError, match="non-empty"):
        build_meta(name, version)


def test_meta_round_trips_through_stellar_sdk() -> None:
    payload = build_meta("counter", "1.0.0", {"repo": "https://example.test/x"})
    assert _unpack_meta(payload)[3] == (b"repo", b"https://example.test/x")


# --- package surface --------------------------------------------------------


def test_spec_package_exports_the_builders_and_errors() -> None:
    import serpent.spec

    assert serpent.spec.__all__ == [
        "SpecDocError",
        "SpecNameError",
        "SpecTypeError",
        "build_env_meta",
        "build_meta",
        "build_spec_entries",
        "to_spec_type",
    ]


def test_map_and_bytes_annotations_reach_the_spec_entries() -> None:
    """A shape neither golden covers: containers and a fixed-length Bytes,
    proving sections delegates to typemap rather than reimplementing it."""

    @contract
    class Wide2:
        def keys(self, env: Env, book: Map[Symbol, I128], salt: Bytes32) -> U32:
            return U32(0)

    inputs = _function(_unpack(build_spec_entries(Wide2)), "keys").inputs
    assert [inp.name for inp in inputs] == [b"book", b"salt"]
    assert inputs[0].type.type == xdr.SCSpecType.SC_SPEC_TYPE_MAP
    assert inputs[1].type.type == xdr.SCSpecType.SC_SPEC_TYPE_BYTES_N
