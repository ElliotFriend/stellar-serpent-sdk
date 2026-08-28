"""WASM instruction/valtype/blocktype constants D uses (dossier §B.2).

**Attribution discipline.** Every constant below carries a provenance
comment. Twenty-two are on-chain-verified (P17): they are what actually
produced the deployed, on-chain artifact at ``spikes/spike1/spike.wasm``, or
(for the two non-opcode constants) the valtype/blocktype byte that artifact's
type section used. Everything else is spec-pinned: taken from the
WebAssembly core binary format and not verified anywhere in this repo --
correct by inspection of the spec, not by an on-chain result.

**The named trap (§B.2).** ``i64.mul``, the valtype ``i64``, and an ``if``
block's i64 result type are three different namespaces that all happen to be
byte ``0x7E``. Naming them ``I64_MUL``, ``VALTYPE_I64``, and ``BLOCKTYPE_I64``
keeps a reader from conflating "the stack holds an i64" with "multiply
those two i64s" -- both true statements about unrelated bytes.

``ON_CHAIN_VERIFIED`` and ``SPEC_PINNED`` hold constant NAMES, not values:
values collide across namespaces (see above), so only names can partition
this module without one number claiming two provenances at once.
"""

# --- Control (0x00, 0x02-0x05, 0x0B-0x0D, 0x0F-0x10) -------------------------

UNREACHABLE = 0x00  # spec-pinned: traps unconditionally (core spec 5.4.1)
BLOCK = 0x02  # spec-pinned: `While`'s exit block (core spec 5.4.1)
LOOP = 0x03  # spec-pinned: `While`'s loop head (core spec 5.4.1)
IF = 0x04  # on-chain-verified (P17): spike's `if_raise`, `load_durable`
ELSE = 0x05  # on-chain-verified (P17): spike's `load_durable`'s default arm
END = 0x0B  # on-chain-verified (P17): closes every function body and block
BR = 0x0C  # spec-pinned: `Break`/`Continue` (core spec 5.4.1)
BR_IF = 0x0D  # spec-pinned: `While`'s conditional exit (core spec 5.4.1)
RETURN = 0x0F  # on-chain-verified (P17): `_Fn.ret`, S2's "the real opcode"
CALL = 0x10  # on-chain-verified (P17): every host call and internal call

# --- Parametric (0x1A) -------------------------------------------------------

DROP = 0x1A  # on-chain-verified (P17): every void host-call result (P14)

# --- Locals (0x20-0x22) ------------------------------------------------------

LOCAL_GET = 0x20  # on-chain-verified (P17): params and locals
LOCAL_SET = 0x21  # on-chain-verified (P17): `LocalSet`, `LetLocal`
LOCAL_TEE = 0x22  # spec-pinned: the overflow-check idiom (compute, test, keep)

# --- Constants (0x41-0x42) ----------------------------------------------------

I32_CONST = 0x41  # on-chain-verified (P17): store addresses, `if` conditions
I64_CONST = 0x42  # on-chain-verified (P17): every literal Val

# --- i64 compares (0x50-0x5A) -------------------------------------------------

I64_EQZ = 0x50  # spec-pinned: `IsZero`/truthiness (D3/C7)
I64_EQ = 0x51  # on-chain-verified (P17): spike's `cond`/`load_durable`
I64_NE = 0x52  # on-chain-verified (P17): spike's `abi_prologue`
I64_LT_S = 0x53  # spec-pinned: signed `<` (F.1)
I64_LT_U = 0x54  # spec-pinned: unsigned `<` (F.1)
I64_GT_S = 0x55  # spec-pinned: signed `>` (F.1)
I64_GT_U = 0x56  # on-chain-verified (P17): spike's `cond`'s `GtU32`
I64_LE_S = 0x57  # spec-pinned: signed `<=` (F.1)
I64_LE_U = 0x58  # spec-pinned: unsigned `<=` (F.1)
I64_GE_S = 0x59  # spec-pinned: signed `>=` (F.1)
I64_GE_U = 0x5A  # spec-pinned: unsigned `>=` (F.1)

# --- i64 arithmetic (0x7C-0x88) -----------------------------------------------

I64_ADD = 0x7C  # on-chain-verified (P17): spike's `AddU32` lowering
I64_SUB = 0x7D  # spec-pinned: checked subtraction (B.4)
I64_MUL = 0x7E  # spec-pinned: checked multiplication (B.4). NOT the valtype;
# `i64.mul_wide_s` is separately BANNED (S13) -- this is plain `i64.mul`.
I64_DIV_S = 0x7F  # spec-pinned: `//` truncated toward zero (A4)
I64_DIV_U = 0x80  # spec-pinned: unsigned `//` (A4)
I64_REM_S = 0x81  # spec-pinned: `%` takes the dividend's sign (A4)
I64_REM_U = 0x82  # spec-pinned: unsigned `%` (A4)
I64_AND = 0x83  # on-chain-verified (P17): spike's `abi_prologue` tag mask
I64_OR = 0x84  # on-chain-verified (P17): spike's `pack_u32`
I64_XOR = 0x85  # spec-pinned: core spec 5.4.5, unused by any lowering yet
I64_SHL = 0x86  # on-chain-verified (P17): spike's `pack_u32`
I64_SHR_S = 0x87  # spec-pinned: signed shift (core spec 5.4.5)
I64_SHR_U = 0x88  # on-chain-verified (P17): spike's `unpack_u32`

# --- Conversions ---------------------------------------------------------------

I32_WRAP_I64 = 0xA7  # spec-pinned: computed store addresses (B.2)
I64_EXTEND_I32_U = 0xAD  # on-chain-verified (P17): Bool-from-comparison
I64_EXTEND32_S = 0xC4  # spec-pinned: I32 unbox, sign-extension proposal (S23)

# --- Memory --------------------------------------------------------------------

I64_LOAD = 0x29  # spec-pinned: reading back scratch (B.2), if D needs it
I64_STORE = 0x37  # on-chain-verified (P17): `MakeStruct` field writes

# --- Valtypes and blocktypes ---------------------------------------------------
# Named distinctly from instruction constants (§B.2's trap): a valtype byte
# and an instruction byte are different namespaces even when they coincide.

VALTYPE_I64 = 0x7E  # on-chain-verified (P17): the type section's `i64` byte
BLOCKTYPE_VOID = 0x40  # on-chain-verified (P17): spike's `if_raise` (no result)
BLOCKTYPE_I64 = 0x7E  # spec-pinned: an `IfExp`'s `if (result i64)` (B.3.1)


# --- Provenance sets (partition constant NAMES, not values) --------------------

ON_CHAIN_VERIFIED = frozenset(
    {
        "END",
        "RETURN",
        "CALL",
        "DROP",
        "LOCAL_GET",
        "LOCAL_SET",
        "I64_STORE",
        "I32_CONST",
        "I64_CONST",
        "I64_EQ",
        "I64_NE",
        "I64_GT_U",
        "I64_ADD",
        "I64_AND",
        "I64_OR",
        "I64_SHL",
        "I64_SHR_U",
        "I64_EXTEND_I32_U",
        "IF",
        "ELSE",
        "VALTYPE_I64",
        "BLOCKTYPE_VOID",
    }
)

SPEC_PINNED = frozenset(
    {
        "UNREACHABLE",
        "BLOCK",
        "LOOP",
        "BR",
        "BR_IF",
        "LOCAL_TEE",
        "I64_EQZ",
        "I64_LT_S",
        "I64_LT_U",
        "I64_GT_S",
        "I64_LE_S",
        "I64_LE_U",
        "I64_GE_S",
        "I64_GE_U",
        "I64_SUB",
        "I64_MUL",
        "I64_DIV_S",
        "I64_DIV_U",
        "I64_REM_S",
        "I64_REM_U",
        "I64_XOR",
        "I64_SHR_S",
        "I32_WRAP_I64",
        "I64_EXTEND32_S",
        "I64_LOAD",
        "BLOCKTYPE_I64",
    }
)
