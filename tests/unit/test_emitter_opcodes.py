"""Provenance tests for ``serpent.emitter.opcodes``.

Two kinds of evidence back this module's constants (dossier §B.2 / P17):
on-chain-verified (produced the deployed ``spikes/spike1/spike.wasm``
artifact) and spec-pinned (WebAssembly core binary format, unverified in this
repo). ``ON_CHAIN_VERIFIED`` and ``SPEC_PINNED`` hold constant NAMES, not
values -- values collide across namespaces (``VALTYPE_I64 == I64_MUL ==
BLOCKTYPE_I64 == 0x7E``), so only names can partition the module.
"""

from serpent.emitter import opcodes

# The twenty on-chain-verified opcodes (P17), plus the on-chain-verified
# valtype and blocktype the same evidence pins.
ON_CHAIN_VERIFIED_VALUES = {
    "END": 0x0B,
    "RETURN": 0x0F,
    "CALL": 0x10,
    "DROP": 0x1A,
    "LOCAL_GET": 0x20,
    "LOCAL_SET": 0x21,
    "I64_STORE": 0x37,
    "I32_CONST": 0x41,
    "I64_CONST": 0x42,
    "I64_EQ": 0x51,
    "I64_NE": 0x52,
    "I64_GT_U": 0x56,
    "I64_ADD": 0x7C,
    "I64_AND": 0x83,
    "I64_OR": 0x84,
    "I64_SHL": 0x86,
    "I64_SHR_U": 0x88,
    "I64_EXTEND_I32_U": 0xAD,
    "IF": 0x04,
    "ELSE": 0x05,
    "VALTYPE_I64": 0x7E,
    "BLOCKTYPE_VOID": 0x40,
}

# Every remaining constant §B.2's table lists, spec-derived from the
# WebAssembly core binary format and unverified anywhere in this repo.
SPEC_PINNED_VALUES = {
    "UNREACHABLE": 0x00,
    "BLOCK": 0x02,
    "LOOP": 0x03,
    "BR": 0x0C,
    "BR_IF": 0x0D,
    "LOCAL_TEE": 0x22,
    "I64_EQZ": 0x50,
    "I64_LT_S": 0x53,
    "I64_LT_U": 0x54,
    "I64_GT_S": 0x55,
    "I64_LE_S": 0x57,
    "I64_LE_U": 0x58,
    "I64_GE_S": 0x59,
    "I64_GE_U": 0x5A,
    "I64_SUB": 0x7D,
    "I64_MUL": 0x7E,
    "I64_DIV_S": 0x7F,
    "I64_DIV_U": 0x80,
    "I64_REM_S": 0x81,
    "I64_REM_U": 0x82,
    "I64_XOR": 0x85,
    "I64_SHR_S": 0x87,
    "I32_WRAP_I64": 0xA7,
    "I64_EXTEND32_S": 0xC4,
    "I64_LOAD": 0x29,
    "BLOCKTYPE_I64": 0x7E,
}


def test_on_chain_verified_constants_match_p17_exactly() -> None:
    for name, value in ON_CHAIN_VERIFIED_VALUES.items():
        assert getattr(opcodes, name) == value, name


def test_spec_pinned_constants_match_the_wasm_core_spec() -> None:
    for name, value in SPEC_PINNED_VALUES.items():
        assert getattr(opcodes, name) == value, name


def test_provenance_sets_hold_exactly_these_names() -> None:
    assert opcodes.ON_CHAIN_VERIFIED == frozenset(ON_CHAIN_VERIFIED_VALUES)
    assert opcodes.SPEC_PINNED == frozenset(SPEC_PINNED_VALUES)


def test_provenance_sets_partition_every_constant_name() -> None:
    """Every int constant opcodes.py defines is in exactly one of the two sets."""
    all_int_constant_names = {
        name
        for name, value in vars(opcodes).items()
        if not name.startswith("_") and isinstance(value, int) and not isinstance(value, bool)
    }
    assert all_int_constant_names == opcodes.ON_CHAIN_VERIFIED | opcodes.SPEC_PINNED
    assert opcodes.ON_CHAIN_VERIFIED.isdisjoint(opcodes.SPEC_PINNED)


def test_every_opcode_bsection2_table_lists_appears_exactly_once_by_name() -> None:
    """The union of both dicts above is meant to be §B.2's full table (review m3)."""
    all_expected = {**ON_CHAIN_VERIFIED_VALUES, **SPEC_PINNED_VALUES}
    assert len(all_expected) == len(ON_CHAIN_VERIFIED_VALUES) + len(SPEC_PINNED_VALUES)
    all_actual_names = {
        name
        for name, value in vars(opcodes).items()
        if not name.startswith("_") and isinstance(value, int) and not isinstance(value, bool)
    }
    assert all_actual_names == set(all_expected)


def test_valtype_and_blocktype_and_instruction_share_0x7e_but_are_distinct_names() -> None:
    """§B.2's named trap: one wasm byte, three distinct meanings."""
    assert opcodes.VALTYPE_I64 == opcodes.I64_MUL == opcodes.BLOCKTYPE_I64 == 0x7E
    names_at_0x7e = {"VALTYPE_I64", "I64_MUL", "BLOCKTYPE_I64"}
    assert len(names_at_0x7e) == 3  # distinct names, not aliases of one constant
