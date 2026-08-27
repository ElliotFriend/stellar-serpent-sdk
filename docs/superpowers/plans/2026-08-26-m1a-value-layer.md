# M1-A: Value Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The permanent `serpent` value layer: the single Val codec, the error
hierarchy, strkey handling, all M1 chain-type classes with checked-arithmetic
Python semantics, and the four decorators — fully usable and comprehensively
tested without any compiler.

**Architecture:** Pure Python, zero runtime dependencies. `serpent/val.py` is THE
codec (spec §10: one implementation, shared later by compiler, emitter, harnesses).
Chain types model on-chain semantics exactly (checked arithmetic with
**truncating** division; traps ↔ builtin exceptions, contract errors ↔
`ContractError`). Decorators make contracts valid, `mypy --strict`-clean Python.
A machine-readable semantics case table (with compilable source strings) is
created here and reused verbatim by sub-plan D to prove compiled behavior matches
these classes.

**Tech Stack:** Python ≥ 3.11, pytest, Hypothesis, mypy --strict, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`
(§2 authoring model as amended, §10 one-codec rule, §13 appendix). Also:
`docs/superpowers/specs/2026-08-26-phase0-findings.md` §3–§4. This plan carries
two spec corrections surfaced by adversarial review (committed alongside this
plan): `@contracterror` members declared via `errorcode(N)` (bare `= N` cannot
pass strict mypy — decorators are invisible to static checkers), and
`Bytes32`/`Bytes64` aliases replacing the `BytesN[32]` annotation form (bare-int
subscripts are invalid types under strict mypy).

## Global Constraints

- Package: `serpent`, src layout, `py.typed`. `uv run mypy --strict` covers
  **`src` AND `tests`** (`[tool.mypy] files = ["src", "tests"]`); `tests/` and its
  subdirectories are packages (each gets `__init__.py`) so fixtures import as
  `from tests.fixtures import token_style`.
- **Zero runtime dependencies** for `serpent` core (`stellar-sdk` stays a dev
  dependency; strkey is implemented internally).
- Val layout: 64-bit; low 8 bits tag; body 56 bits; major/minor = body split 32/24
  (`val = (body << 8) | tag`; `body = (major << 24) | minor`).
- Tags: False=0 True=1 Void=2 Error=3 U32Val=4 I32Val=5 U64Small=6 I64Small=7
  TimepointSmall=8 DurationSmall=9 U128Small=10 I128Small=11 U256Small=12
  I256Small=13 SymbolSmall=14; objects 64–79 (U64=64 I64=65 Timepoint=66
  Duration=67 U128=68 I128=69 U256=70 I256=71 Bytes=72 String=73 Symbol=74 Vec=75
  Map=76 Address=77 MuxedAddress=78 ExecutableTag=79); Bad=0x7f. `is_object` =
  `63 < tag < 80`. NOTE for the executor: the frozen spike's
  `harness.py` has a STALE object upper bound (79-exclusive, matching v23) — the
  values above are verified against `val.rs` @ v28.0.2; do not "correct" `val.py`
  toward the spike. Tags 78/79 were protocol-gated in at 23 and 28 respectively —
  record that in a comment; M1-A emits nothing, so this is documentation only.
- Small-value bounds: `MAX_SMALL_U64 = 2**56 - 1`; signed small fits iff
  `-(2**55) <= v <= 2**55 - 1`; same numeric bounds for the 128-bit small forms.
- SymbolSmall: ≤ 9 chars from `[a-zA-Z0-9_]`, 6 bits/char, high-order-first,
  zero-padded high bits; codes `_`=1, `0`–`9`=2–11, `A`–`Z`=12–37, `a`–`z`=38–63.
  `SCSYMBOL_LIMIT = 32`. Spec-name caps: field/function names ≤ 30 chars, type
  names ≤ 60.
- Error Val: `(code << 32) | (error_type << 8) | 3`; contract type = 0.
- Golden constants (verified on-chain in Phase 0): `symbol_small("COUNTER") ==
  253576579652878`; `symbol_small("COUNT") == 61908344590`; `error_val(7) ==
  30064771075`; `pack_u32val(0) == 4`;
  `pack_u32val(3_000_000_000) == (3_000_000_000 << 32) | 4`.
- Reserved serpent runtime error codes: `RESERVED_CODE_MIN = 0xFFFF_FF00`;
  `CODE_BAD_ARGUMENT = 0xFFFF_FFFF`; `CODE_ARITHMETIC_OVERFLOW = 0xFFFF_FFFE`.
  `@contracterror` rejects user codes ≥ `RESERVED_CODE_MIN`.
- **Exception mapping rule** (document in every relevant docstring): host traps ↔
  builtin exceptions (`ZeroDivisionError`, `IndexError`, `KeyError`); contract
  errors (`fail_with_error`) ↔ `ContractError` subclasses; authoring-time misuse ↔
  `ValueError`/`TypeError`. **Equality is exempt:** `__eq__` NEVER raises (see the
  comparison contract below).
- **Checked arithmetic contract** (binding for every numeric chain type):
  - Supported: `+ - * // %`, unary `-`, reflected forms (`3 + U32(5)` works, so
    `sum()` over chain ints works), and augmented forms (via the binary ops).
  - Any result outside the type's range raises `ArithmeticOverflow` — including
    unary minus (`-U32(1)`, `-I32(-2**31)`) and `MIN // -1`. Never wraps, never
    silently widens.
  - **`//` truncates toward zero and `%` takes the dividend's sign** (WASM
    `div_s`/`rem_s` and Rust semantics — NOT Python floor/floormod). `MIN % -1`
    returns `0` (does not trap on-chain; must not raise here). `// 0` and `% 0`
    raise `ZeroDivisionError`.
  - `**`, `divmod`, bitwise ops: `TypeError` with a message naming the omission
    (not silently `NotImplemented`); revisit when a contract needs them.
  - Plain in-range `int` operands coerce (either side); out-of-range int operands
    raise `ValueError`; foreign chain types raise `TypeError`. (Arithmetic only —
    see comparison contract.)
- **Comparison contract** (binding for every chain type):
  - `__eq__` returns `True`/`False`, never raises: `False` for foreign chain
    types, non-chain objects, and out-of-range ints. `U32(1) == 1` is `True`.
  - `__hash__ = hash(self.value)` for numerics (int-equality contract:
    `a == b ⟹ hash(a) == hash(b)`); text/bytes types hash their payload.
  - Ordering (`< <= > >=`): same type, or any `int` for numerics — answered
    mathematically even for out-of-range ints (`U32(5) < 2**40` is `True`);
    foreign chain types → `TypeError`.
- **`_SCVAL_RANK`**: every chain type declares `_SCVAL_RANK: ClassVar[int]` using
  the **`ScValType` order** (the host's actual cross-type order — `obj_cmp` falls
  back to `Tag::get_scval_type().cmp()`, NOT tag order): Bool 0, Void 1, Error 2,
  U32 3, I32 4, U64 5, I64 6, Timepoint 7, Duration 8, U128 9, I128 10, U256 11,
  I256 12, Bytes 13, String 14, Symbol 15, Vec 16, Map 17, Address 18.
- All tests via `uv run pytest`; paths via `pathlib`; suite, `uv run ruff check .`,
  and `uv run mypy --strict` green at every commit.
- Commits: conventional, no emojis, explicit paths, trailer:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Spikes are frozen: never modify `spikes/**`; port test vectors by value.

## File Structure

```
src/serpent/
├── __init__.py          # public exports (replaces the uv hello() stub)
├── val.py               # THE codec (imports nothing from serpent)
├── errors.py            # ContractError hierarchy + reserved codes (imports val)
├── _strkey.py           # internal strkey codec (standalone)
├── types/
│   ├── __init__.py
│   ├── _ordering.py     # val_cmp — reads _SCVAL_RANK/_cmp_payload off instances;
│   │                    #   imports NOTHING from types/ (no cycles)
│   ├── numeric.py       # Bool, U32, I32, U64, I64, U128, I128, Timepoint, Duration
│   ├── symbol.py        # Symbol
│   ├── buffers.py       # Bytes, Bytes32, Bytes64 (via internal bytes_n factory), String
│   ├── containers.py    # Vec, Map
│   └── address.py       # Address
├── decorators.py        # contract, contracttype, contracterror, errorcode, contractevent
└── env.py               # authoring-surface Env (typed, NotImplementedError bodies)
tests/__init__.py  tests/unit/__init__.py  tests/semantics/__init__.py  tests/fixtures/__init__.py
tests/unit/…             # one module per source module (+ _properties for Hypothesis)
tests/semantics/cases.py + test_semantics.py
tests/fixtures/token_style.py
```

U256/I256 are **deferred to M2** (per spec §2 "Later" and §11), including their
limb decomposition. For the record when M2 arrives: `obj_from_i256_pieces` takes
`hi_hi: i64, hi_lo: u64, lo_hi: u64, lo_lo: u64` (big-endian words).

---

### Task 1: `val.py` core — tags, masks, small-value packing

**Files:**
- Create: `src/serpent/val.py`, `tests/__init__.py`, `tests/unit/__init__.py`
- Test: `tests/unit/test_val.py`, `tests/unit/test_val_properties.py`

**Interfaces:**
- Produces (all `int -> int` / `int -> bool` unless noted): the TAG constants per
  Global Constraints (`TAG_FALSE … TAG_SYMBOL_SMALL`, `TAG_U64_OBJECT …
  TAG_EXECUTABLE_TAG_OBJECT`, `TAG_BAD`); `MASK64`, `MASK56`, `MAX_SMALL_U64`,
  `MIN_SMALL_I64`, `MAX_SMALL_I64`; `as_u64(v)`, `as_i64(v)`; `tag_of`, `body_of`,
  `major_of`, `minor_of`, `is_object`; `from_body_tag(body, tag)`,
  `from_major_minor_tag(major, minor, tag)`; `VOID_VAL=2`, `TRUE_VAL=1`,
  `FALSE_VAL=0`, `pack_bool`, `unpack_bool`; `pack_u32val`, `unpack_u32val`,
  `pack_i32val`, `unpack_i32val`; `pack_small_u64(x, tag)`,
  `unpack_small_u64(v, expected_tag)`, `pack_small_i64(x, tag)`,
  `unpack_small_i64(v, expected_tag)`, `fits_small_u(x)`, `fits_small_i(x)`.
  **Every `unpack_*` validates the tag first** and raises `ValueError` naming the
  found and expected tags.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_val.py` (write in full; every block required):

```python
import pytest

from serpent import val


def test_tag_constants_match_verified_table() -> None:
    assert (val.TAG_FALSE, val.TAG_TRUE, val.TAG_VOID, val.TAG_ERROR) == (0, 1, 2, 3)
    assert (val.TAG_U32, val.TAG_I32) == (4, 5)
    assert (val.TAG_U64_SMALL, val.TAG_I64_SMALL) == (6, 7)
    assert (val.TAG_TIMEPOINT_SMALL, val.TAG_DURATION_SMALL) == (8, 9)
    assert (val.TAG_U128_SMALL, val.TAG_I128_SMALL) == (10, 11)
    assert (val.TAG_U256_SMALL, val.TAG_I256_SMALL) == (12, 13)
    assert val.TAG_SYMBOL_SMALL == 14
    assert (val.TAG_U64_OBJECT, val.TAG_MAP_OBJECT, val.TAG_ADDRESS_OBJECT) == (64, 76, 77)
    assert (val.TAG_MUXED_ADDRESS_OBJECT, val.TAG_EXECUTABLE_TAG_OBJECT) == (78, 79)
    assert val.TAG_BAD == 0x7F


def test_layout_helpers() -> None:
    v = val.from_major_minor_tag(7, 0, val.TAG_U32)
    assert v == (7 << 32) | 4
    assert val.tag_of(v) == 4 and val.major_of(v) == 7 and val.minor_of(v) == 0
    assert val.body_of(v) == 7 << 24


def test_bool_and_void() -> None:
    assert val.pack_bool(True) == 1 and val.pack_bool(False) == 0
    assert val.unpack_bool(1) is True and val.unpack_bool(0) is False
    assert val.VOID_VAL == 2
    with pytest.raises(ValueError):
        val.unpack_bool(2)  # Void is not a bool


def test_u32val_golden() -> None:
    assert val.pack_u32val(0) == 4
    assert val.pack_u32val(3_000_000_000) == (3_000_000_000 << 32) | 4
    assert val.unpack_u32val((3_000_000_000 << 32) | 4) == 3_000_000_000
    with pytest.raises(ValueError):
        val.pack_u32val(2**32)
    with pytest.raises(ValueError):
        val.pack_u32val(-1)


def test_unpack_checks_tag() -> None:
    with pytest.raises(ValueError):
        val.unpack_u32val(val.pack_i32val(1))
    with pytest.raises(ValueError):
        val.unpack_small_i64(val.pack_small_u64(1, val.TAG_U64_SMALL), val.TAG_I64_SMALL)


def test_i32val_bit_pattern() -> None:
    assert val.pack_i32val(-1) == (0xFFFF_FFFF << 32) | 5
    assert val.unpack_i32val((0xFFFF_FFFF << 32) | 5) == -1


def test_small_i64_round_trip_bounds() -> None:
    for x in (0, 1, -1, val.MAX_SMALL_I64, val.MIN_SMALL_I64):
        packed = val.pack_small_i64(x, val.TAG_I64_SMALL)
        assert val.unpack_small_i64(packed, val.TAG_I64_SMALL) == x
    with pytest.raises(ValueError):
        val.pack_small_i64(val.MAX_SMALL_I64 + 1, val.TAG_I64_SMALL)


def test_signed_boundary_masking() -> None:
    # wasmtime hands back signed i64; as_u64/as_i64 must round-trip bit patterns
    assert val.as_u64(-1) == val.MASK64
    assert val.as_i64(val.MASK64) == -1
    assert val.as_u64(val.as_i64((3_000_000_000 << 32) | 4)) == (3_000_000_000 << 32) | 4


def test_is_object_range() -> None:
    assert not val.is_object(val.pack_u32val(9))
    assert val.is_object(val.from_major_minor_tag(1, 0, val.TAG_VEC_OBJECT))
    assert val.is_object(val.from_major_minor_tag(1, 0, val.TAG_EXECUTABLE_TAG_OBJECT))
    assert not val.is_object(val.TAG_BAD)  # 0x7F is Bad, not an object
```

`tests/unit/test_val_properties.py`:

```python
from hypothesis import given, strategies as st

from serpent import val

u32s = st.integers(min_value=0, max_value=2**32 - 1)
i32s = st.integers(min_value=-(2**31), max_value=2**31 - 1)
small_us = st.integers(min_value=0, max_value=val.MAX_SMALL_U64)
small_is = st.integers(min_value=val.MIN_SMALL_I64, max_value=val.MAX_SMALL_I64)
u64_bits = st.integers(min_value=0, max_value=2**64 - 1)


@given(u32s)
def test_u32val_round_trips(x: int) -> None:
    assert val.unpack_u32val(val.pack_u32val(x)) == x


@given(i32s)
def test_i32val_round_trips(x: int) -> None:
    assert val.unpack_i32val(val.pack_i32val(x)) == x


@given(small_us)
def test_small_u64_round_trips(x: int) -> None:
    assert val.unpack_small_u64(val.pack_small_u64(x, val.TAG_U64_SMALL), val.TAG_U64_SMALL) == x


@given(small_is)
def test_small_i64_round_trips(x: int) -> None:
    assert val.unpack_small_i64(val.pack_small_i64(x, val.TAG_I64_SMALL), val.TAG_I64_SMALL) == x


@given(u64_bits)
def test_signed_masking_is_a_bijection(bits: int) -> None:
    assert val.as_u64(val.as_i64(bits)) == bits
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_val.py tests/unit/test_val_properties.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'serpent.val'`.

- [ ] **Step 3: Implement `src/serpent/val.py`**

Complete implementation per Interfaces. Core sketch — note the tag check IS in
the sketch (executors copy sketches):

```python
"""The single Val codec for serpent (spec §10: one implementation, everywhere)."""

MASK64 = (1 << 64) - 1
MASK56 = (1 << 56) - 1
MAX_SMALL_U64 = MASK56
MAX_SMALL_I64 = (1 << 55) - 1
MIN_SMALL_I64 = -(1 << 55)


def as_u64(v: int) -> int:
    return v & MASK64


def as_i64(v: int) -> int:
    v &= MASK64
    return v - (1 << 64) if v >= 1 << 63 else v


def tag_of(v: int) -> int:
    return v & 0xFF


def _require_tag(v: int, expected: int) -> None:
    if tag_of(v) != expected:
        raise ValueError(f"expected tag {expected}, found tag {tag_of(v)} in {v:#x}")


def pack_small_i64(x: int, tag: int) -> int:
    if not MIN_SMALL_I64 <= x <= MAX_SMALL_I64:
        raise ValueError(f"does not fit 56-bit signed small form: {x}")
    return from_body_tag(x & MASK56, tag)


def unpack_small_i64(v: int, expected_tag: int) -> int:
    _require_tag(v, expected_tag)
    body = body_of(v)
    return body - (1 << 56) if body >= 1 << 55 else body
```

Include a comment on the object-tag constants: values verified against
`soroban-env-common/src/val.rs` @ v28.0.2; `MuxedAddressObject=78` (protocol 23+),
`ExecutableTagObject=79` (protocol 28+); the frozen spike harness's 79-exclusive
upper bound is stale — do not copy it.

- [ ] **Step 4: Run tests to verify they pass** — plus `uv run mypy --strict` and
  `uv run ruff check .` clean.

- [ ] **Step 5: Commit**

```bash
git add src/serpent/val.py tests/__init__.py tests/unit/__init__.py tests/unit/test_val.py tests/unit/test_val_properties.py
git commit -m "feat: add Val codec core with tags, masks, and small-value packing

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `val.py` symbols — SymbolSmall pack/unpack/validate

**Files:**
- Modify: `src/serpent/val.py`
- Test: append to `tests/unit/test_val.py`, `tests/unit/test_val_properties.py`

**Interfaces:**
- Produces: `SCSYMBOL_LIMIT = 32`, `SYMBOL_CHARS`, `symbol_char_code(ch) -> int`,
  `is_valid_symbol(text) -> bool` (charset + ≤ 32), `symbol_small(text) -> int`
  (≤ 9 chars, full tagged Val; `ValueError` otherwise), `symbol_small_text(v) -> str`
  (tag-checked decode), `fits_symbol_small(text) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_val.py`:

```python
def test_symbol_small_goldens_from_chain() -> None:
    assert val.symbol_small("COUNTER") == 253576579652878
    assert val.symbol_small("COUNT") == 61908344590


def test_symbol_char_codes() -> None:
    assert val.symbol_char_code("_") == 1
    assert val.symbol_char_code("0") == 2 and val.symbol_char_code("9") == 11
    assert val.symbol_char_code("A") == 12 and val.symbol_char_code("Z") == 37
    assert val.symbol_char_code("a") == 38 and val.symbol_char_code("z") == 63


def test_symbol_small_rejects() -> None:
    with pytest.raises(ValueError):
        val.symbol_small("ten_chars_")
    with pytest.raises(ValueError):
        val.symbol_small("has-dash")
    with pytest.raises(ValueError):
        val.symbol_small("")


def test_symbol_validation_boundaries() -> None:
    assert val.is_valid_symbol("a" * 32) and not val.is_valid_symbol("a" * 33)
    assert val.fits_symbol_small("nine_char") and not val.fits_symbol_small("ten_chars_")


def test_symbol_small_text_round_trip() -> None:
    for s in ("A", "COUNT", "z9_", "nine_char"):
        assert val.symbol_small_text(val.symbol_small(s)) == s
```

Append to `tests/unit/test_val_properties.py`:

```python
symbol_alphabet = st.sampled_from("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
small_symbols = st.text(alphabet=symbol_alphabet, min_size=1, max_size=9)


@given(small_symbols)
def test_symbol_small_round_trips(s: str) -> None:
    assert val.symbol_small_text(val.symbol_small(s)) == s


@given(small_symbols)
def test_symbol_small_tag(s: str) -> None:
    assert val.tag_of(val.symbol_small(s)) == val.TAG_SYMBOL_SMALL
```

- [ ] **Step 2: Run to verify failure** (`AttributeError: symbol_small`).
- [ ] **Step 3: Implement.** Packing: `accum = (accum << 6) | symbol_char_code(ch)`
  high-order-first, then `from_body_tag(accum, TAG_SYMBOL_SMALL)`. Decode peels
  6-bit groups skipping leading zeros; inverse table derived from
  `symbol_char_code` (single alphabet source).
- [ ] **Step 4: Run to green** (suite + mypy + ruff).
- [ ] **Step 5: Commit**

```bash
git add src/serpent/val.py tests/unit/test_val.py tests/unit/test_val_properties.py
git commit -m "feat: add SymbolSmall packing with on-chain golden vectors

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Error Vals and the `ContractError` hierarchy

**Files:**
- Modify: `src/serpent/val.py`
- Create: `src/serpent/errors.py`
- Test: append to `tests/unit/test_val.py`; create `tests/unit/test_errors.py`

**Interfaces:**
- `val.py` adds: `ERROR_TYPE_CONTRACT = 0` through `ERROR_TYPE_AUTH = 9` (named per
  XDR `SCErrorType`), `error_val(code, error_type=0)`, `error_code_of(v)`,
  `error_type_of(v)` (tag-checked), `is_contract_error_val(v)`.
- `errors.py`: `RESERVED_CODE_MIN = 0xFFFF_FF00`, `CODE_BAD_ARGUMENT = 0xFFFF_FFFF`,
  `CODE_ARITHMETIC_OVERFLOW = 0xFFFF_FFFE`;
  `class ContractError(Exception)` — **abstract base**: declares
  `code: ClassVar[int]` (annotation only, no value); `__init_subclass__` enforces
  that concrete subclasses define an in-range `code`; instantiating `ContractError`
  directly raises `TypeError`; `.to_val() -> int`;
  `class ArithmeticOverflow(ContractError)` (`code = CODE_ARITHMETIC_OVERFLOW`);
  `class BadArgument(ContractError)` (`code = CODE_BAD_ARGUMENT`).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_errors.py`:

```python
import pytest

from serpent import val
from serpent.errors import (
    CODE_ARITHMETIC_OVERFLOW,
    CODE_BAD_ARGUMENT,
    RESERVED_CODE_MIN,
    ArithmeticOverflow,
    BadArgument,
    ContractError,
)


def test_error_val_golden() -> None:
    assert val.error_val(7) == 30064771075
    assert val.error_code_of(30064771075) == 7
    assert val.error_type_of(30064771075) == val.ERROR_TYPE_CONTRACT
    assert val.is_contract_error_val(30064771075)


def test_error_val_non_contract_type() -> None:
    v = val.error_val(6, error_type=2)  # Context/InvalidAction shape
    assert val.error_type_of(v) == 2 and not val.is_contract_error_val(v)


def test_contract_error_base_is_abstract() -> None:
    with pytest.raises(TypeError):
        ContractError("nope")


def test_contract_error_subclass_carries_code_and_val() -> None:
    class Custom(ContractError):
        code = 42

    err = Custom("boom")
    assert err.code == 42 and err.to_val() == val.error_val(42)
    with pytest.raises(Custom):
        raise Custom("boom")


def test_reserved_codes() -> None:
    assert ArithmeticOverflow().code == CODE_ARITHMETIC_OVERFLOW
    assert BadArgument().code == CODE_BAD_ARGUMENT
    assert CODE_ARITHMETIC_OVERFLOW >= RESERVED_CODE_MIN
```

- [ ] **Step 2: Verify failure. Step 3: Implement. Step 4: Green. Step 5: Commit**

```bash
git add src/serpent/val.py src/serpent/errors.py tests/unit/test_val.py tests/unit/test_errors.py
git commit -m "feat: add error Val encoding and ContractError hierarchy

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `_strkey.py` — zero-dep strkey codec

**Files:**
- Create: `src/serpent/_strkey.py`
- Test: `tests/unit/test_strkey.py`

**Interfaces:**
- `encode(version_byte: int, payload: bytes) -> str`,
  `decode(expected_version: int, s: str) -> bytes` (`ValueError` on bad checksum/
  version/length/charset), `VERSION_ACCOUNT = 48` ('G'), `VERSION_CONTRACT = 16`
  ('C'). Algorithm: version byte + 32 payload bytes + CRC16-XModem (poly 0x1021,
  init 0x0000, appended little-endian), RFC 4648 base32 uppercase, no padding.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_strkey.py`:

```python
import pytest

from serpent import _strkey

# Real identifiers from this repo's Phase 0 evidence (testnet):
ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"
CONTRACT = "CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI"


def test_round_trip_account_and_contract() -> None:
    raw_g = _strkey.decode(_strkey.VERSION_ACCOUNT, ACCOUNT)
    assert len(raw_g) == 32
    assert _strkey.encode(_strkey.VERSION_ACCOUNT, raw_g) == ACCOUNT
    raw_c = _strkey.decode(_strkey.VERSION_CONTRACT, CONTRACT)
    assert len(raw_c) == 32
    assert _strkey.encode(_strkey.VERSION_CONTRACT, raw_c) == CONTRACT


def test_wrong_version_rejected() -> None:
    with pytest.raises(ValueError):
        _strkey.decode(_strkey.VERSION_ACCOUNT, CONTRACT)


def test_corrupt_checksum_rejected() -> None:
    bad = ACCOUNT[:-1] + ("A" if ACCOUNT[-1] != "A" else "B")
    with pytest.raises(ValueError):
        _strkey.decode(_strkey.VERSION_ACCOUNT, bad)


def test_matches_stellar_sdk() -> None:
    from stellar_sdk import StrKey

    assert _strkey.decode(_strkey.VERSION_ACCOUNT, ACCOUNT) == StrKey.decode_ed25519_public_key(ACCOUNT)
    assert _strkey.decode(_strkey.VERSION_CONTRACT, CONTRACT) == StrKey.decode_contract(CONTRACT)
```

Plus Hypothesis: random 32-byte payloads round-trip for both versions, and a
differential property encoding random payloads with both implementations.

- [ ] **Step 2–4: RED → implement (~50 lines) → GREEN. Step 5: Commit**

```bash
git add src/serpent/_strkey.py tests/unit/test_strkey.py
git commit -m "feat: add zero-dependency strkey codec with stellar-sdk differential tests

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Numeric chain types — `Bool U32 I32 U64 I64 U128 I128 Timepoint Duration`

**Files:**
- Create: `src/serpent/types/__init__.py`, `src/serpent/types/numeric.py`
- Test: `tests/unit/test_numeric.py`, `tests/unit/test_numeric_properties.py`

**Interfaces:**
- `class _ChainInt` (internal): immutable; `value: int` bounds-validated at
  construction (`ValueError`); implements the **checked arithmetic contract** and
  the **comparison contract** from Global Constraints verbatim (truncating `//`
  and `%`, `ArithmeticOverflow` on out-of-range incl. unary minus and `MIN // -1`,
  `MIN % -1 == 0`, reflected ops, eq-never-raises, `hash(value)`,
  mathematical ordering vs any int). `_SCVAL_RANK: ClassVar[int]` and
  `_cmp_payload(self) -> object` on every type (consumed by Task 8's `val_cmp`).
  `__repr__` like `U32(7)`.
- Concrete: `Bool` (rank 0; `to_val()` → `TRUE_VAL`/`FALSE_VAL`; `__bool__`;
  `False < True`; not a `_ChainInt` — no arithmetic), `U32`(3), `I32`(4),
  `U64`(5), `I64`(6), `U128`(9), `I128`(10), `Timepoint`(7), `Duration`(8) with
  the obvious bounds. `Timepoint`/`Duration`: no cross arithmetic (documented
  deferral) but `from_u64(cls, u: U64)` / `to_u64(self) -> U64` conversions exist
  (sub-plan E needs `env.ledger().timestamp()` bridging immediately).
- `I128`/`U128` additionally expose the host limb convention:
  `I128.hi64 -> int` (signed) / `.lo64 -> int` (unsigned), matching
  `obj_from_i128_pieces(hi: i64, lo: u64)`. Goldens:
  `I128(-1).hi64 == -1 and I128(-1).lo64 == 2**64 - 1`;
  `I128(-(2**64)).hi64 == -1 and .lo64 == 0`; `U128(2**64).hi64 == 1, .lo64 == 0`.
- `to_val()`: inline/small form where it fits (Bool/U32/I32 always; U64/I64/U128/
  I128/Timepoint/Duration small forms with correct tags); else
  `NotImplementedError("host object form; sub-plan B")` — explicit and tested.
  `from_val(v)` correspondingly (tag-checked).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_numeric.py` (excerpt — write all analogous blocks for every type):

```python
import pytest

from serpent import val
from serpent.errors import ArithmeticOverflow
from serpent.types import I32, I128, U32, U64, Bool, Duration, Timepoint


def test_construction_bounds() -> None:
    assert U32(0).value == 0 and U32(2**32 - 1).value == 2**32 - 1
    with pytest.raises(ValueError):
        U32(2**32)
    with pytest.raises(ValueError):
        U32(-1)


def test_checked_arithmetic_overflow_raises() -> None:
    with pytest.raises(ArithmeticOverflow):
        U32(2**32 - 1) + U32(1)
    with pytest.raises(ArithmeticOverflow):
        U32(0) - U32(1)
    with pytest.raises(ArithmeticOverflow):
        I32(-(2**31)) - 1
    with pytest.raises(ArithmeticOverflow):
        -U32(1)                      # unary minus out of range
    with pytest.raises(ArithmeticOverflow):
        -I32(-(2**31))
    assert -I32(5) == I32(-5)


def test_truncating_division_semantics() -> None:
    # WASM div_s/rem_s truncate toward zero; Python floors. We match the chain.
    assert I32(-7) // I32(2) == I32(-3)      # Python would say -4
    assert I32(-7) % I32(2) == I32(-1)       # Python would say 1
    assert I32(7) // I32(-2) == I32(-3) and I32(7) % I32(-2) == I32(1)
    with pytest.raises(ArithmeticOverflow):
        I32(-(2**31)) // I32(-1)             # overflows i32
    assert I32(-(2**31)) % I32(-1) == I32(0)  # rem_s does NOT trap here
    with pytest.raises(ZeroDivisionError):
        U32(1) // U32(0)


def test_int_coercion_and_reflected_ops() -> None:
    assert (U32(5) + 3) == U32(8)
    assert (3 + U32(5)) == U32(8)            # __radd__: sum() works
    assert sum([U32(1), U32(2)], start=U32(0)) == U32(3)
    with pytest.raises(ValueError):
        U32(5) + (2**32)


def test_no_implicit_widening_in_arithmetic() -> None:
    with pytest.raises(TypeError):
        U32(1) + U64(1)           # type: ignore[operator]
    with pytest.raises(TypeError):
        Timepoint(1) + Duration(1)  # type: ignore[operator]
    with pytest.raises(TypeError):
        U32(2) ** U32(3)          # type: ignore[operator]


def test_equality_never_raises_and_hash_contract() -> None:
    assert U32(1) == 1 and hash(U32(1)) == hash(1)
    assert (U32(1) == U64(1)) is False        # foreign chain type: False, not TypeError
    assert (U32(5) == 2**40) is False         # out-of-range int: False
    assert U32(5) < 2**40                     # ordering vs any int is mathematical
    assert {U32(1): "a"}[1] == "a"            # eq/hash invariant holds


def test_bool_type() -> None:
    assert Bool(True).to_val() == val.TRUE_VAL and Bool(False).to_val() == val.FALSE_VAL
    assert bool(Bool(True)) and not bool(Bool(False))
    assert Bool(False) < Bool(True)


def test_i128_limbs_golden() -> None:
    assert I128(-1).hi64 == -1 and I128(-1).lo64 == 2**64 - 1
    assert I128(-(2**64)).hi64 == -1 and I128(-(2**64)).lo64 == 0


def test_timepoint_u64_bridge() -> None:
    assert Timepoint.from_u64(U64(1000)).to_u64() == U64(1000)


def test_val_round_trip_and_object_form_boundary() -> None:
    assert U32(9).to_val() == val.pack_u32val(9)
    assert U32.from_val(val.pack_u32val(9)) == U32(9)
    assert U64(val.MAX_SMALL_U64).to_val() == val.pack_small_u64(val.MAX_SMALL_U64, val.TAG_U64_SMALL)
    with pytest.raises(NotImplementedError):
        U64(val.MAX_SMALL_U64 + 1).to_val()
```

`tests/unit/test_numeric_properties.py`: Hypothesis over each type's full range —
(a) each op equals the **truncating-division reference model** (`+ - *` plain
bigint; `//` = `int(a / b)`-style trunc; `%` = `a - b * trunc(a/b)`) whenever that
result is in-range, and raises `ArithmeticOverflow` exactly when not; (b)
`from_val(to_val(x)) == x` for small-form values; (c) comparisons agree with int
comparisons; (d) eq/hash invariant vs equal ints.

- [ ] **Step 2: Verify failure. Step 3: Implement `numeric.py`** — one `_ChainInt`
  base; subclasses declare `MIN`/`MAX`/rank/tag mapping only.
- [ ] **Step 4: Run to green** (suite + mypy strict incl. tests + ruff).
- [ ] **Step 5: Commit**

```bash
git add src/serpent/types tests/unit/test_numeric.py tests/unit/test_numeric_properties.py
git commit -m "feat: add numeric chain types with chain-exact checked arithmetic

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: `Symbol`, `String`, `Bytes`, `Bytes32`/`Bytes64`

**Files:**
- Create: `src/serpent/types/symbol.py`, `src/serpent/types/buffers.py`
- Test: `tests/unit/test_symbol_string_bytes.py`

**Interfaces:**
- `Symbol(text)`: validates via `val.is_valid_symbol` (`ValueError`); `.text`;
  eq/ordering by text; rank 15; `to_val()` small form iff ≤ 9 chars else
  `NotImplementedError` (object form: sub-plan B); `from_val` small form.
- `String(text)`: arbitrary `str`; `.text`; eq/ordering by UTF-8 bytes; rank 14;
  no `to_val` yet (always host-object — sub-plan B).
- `Bytes(data)`: immutable; `.data`; eq/ordering bytewise; rank 13; `len()`;
  **`__getitem__(i) -> U32`** (chain types everywhere — the host's `bytes_get`
  returns U32Val; slicing returns `Bytes`); `IndexError` out of range.
- Fixed-length: internal factory `bytes_n(n: int) -> type[Bytes]` returning a
  cached subclass validating `len(data) == n`; public aliases
  `Bytes32 = bytes_n(32)` and `Bytes64 = bytes_n(64)` exported. **There is no
  `BytesN[32]` annotation form** — a bare-int subscript is not a valid type under
  `mypy --strict` (adversarial-review verified); contracts annotate with
  `Bytes32`. Arbitrary other lengths via `bytes_n(n)` (documented, compiler
  support in sub-plan C).

- [ ] **Step 1: Tests** — validation matrix (Symbol charset/length incl. 32-char
  cap; `Bytes32` length enforcement; `bytes_n(32) is Bytes32` cache; `isinstance
  (Bytes32(b"\0" * 32), Bytes)`; String unicode round-trip); `Bytes(b"ab")[0] ==
  U32(97)`; slicing; `Symbol("COUNT").to_val() == val.symbol_small("COUNT")`;
  the `NotImplementedError` boundaries; a typed annotation usage
  (`x: Bytes32 = Bytes32(b"\0" * 32)`) that mypy --strict must accept (tests are
  type-checked per Global Constraints).
- [ ] **Step 2–4: RED → implement → GREEN. Step 5: Commit**

```bash
git add src/serpent/types tests/unit/test_symbol_string_bytes.py
git commit -m "feat: add Symbol, String, Bytes, and fixed-length byte types

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: `val_cmp` ordering + `Vec` and `Map` containers

**Files:**
- Create: `src/serpent/types/_ordering.py`, `src/serpent/types/containers.py`
- Test: `tests/unit/test_containers.py`

**Interfaces:**
- `_ordering.val_cmp(a, b) -> int`: total order = (`type(a)._SCVAL_RANK`, then
  `_cmp_payload()` within-type). **`ScValType` rank, NOT tag rank** — the host's
  `obj_cmp` falls back to `Tag::get_scval_type().cmp()` and its object-vs-object
  discriminant is deliberately kept in `ScVal` order (adversarial-review verified
  against `compare.rs`/`host.rs`/`comparison.rs` @ v28.0.2; tag order gives the
  WRONG answer for e.g. Symbol-vs-Bytes). `_ordering` imports NOTHING from
  `types/` — it reads the ClassVar/method off instances, so no import cycles and
  Task 8's `Address` extends it purely additively. Document: "partial model of
  host obj_cmp, differential-validated in sub-plans D/F; extending the supported
  set requires extending the differential tests."
- `Vec`: element type passed EXPLICITLY at construction — `Vec(U32)` /
  `Vec(U32, [U32(1), ...])` — because `Generic` cannot deliver `T` at runtime
  (`__orig_class__` is set only after `__init__`; adversarial-review verified).
  `Vec[U32]` remains the *annotation* form (class is `Generic[T]`). Host-shaped
  API with host semantics: `push_back`, `pop_back`, `push_front`, `pop_front`,
  `get(i)`, `put(i, v)`, `del_(i)`, `insert(i, v)`, `append(other)`,
  `slice(lo, hi)`, `first_index_of(v)`, `__len__`, iteration. Out-of-bounds
  `get/put/del_/insert` and `pop_*` on empty raise `IndexError` (trap mapping);
  wrong element type raises `TypeError` (authoring-time).
- `Map`: `Map(Symbol, U32)` construction (same explicit-type rule; `Map[K, V]` as
  annotation); sorted-by-`val_cmp(key)`; `set`, `get` (missing → `KeyError`, host
  `map_get` traps), `has`, `del_` (missing → `KeyError`, host traps), `keys() ->
  Vec`, `values() -> Vec`, `key_by_pos`, `val_by_pos`; iteration yields keys in
  sorted order (observable on-chain — pinned by tests).

- [ ] **Step 1: Tests** — the rank-order golden that catches the tag-order bug:

```python
from serpent.types import Bytes, Symbol
from serpent.types._ordering import val_cmp


def test_scval_rank_not_tag_rank() -> None:
    # Host orders by ScValType (Bytes=13 < Symbol=15), NOT by tag
    # (SymbolSmall=14 < BytesObject=72). Verified against obj_cmp @ v28.0.2.
    assert val_cmp(Bytes(b"\xff"), Symbol("a")) < 0
    assert val_cmp(Symbol("a"), Bytes(b"\xff")) > 0
```

plus: full rank-table test across one instance of every M1-A type; Phase 0 map
golden (`Symbol("counter_limit")` sorts before `Symbol("display_name")`); Map/Vec
host-shaped ops incl. every error mapping named above; explicit-element-type
enforcement (`Vec(U32).push_back(I32(1))` → `TypeError`); Hypothesis — Map
iteration order equals `sorted(keys, key=cmp_to_key(val_cmp))`, Vec ops against a
plain-list reference model.

- [ ] **Step 2–4: RED → implement → GREEN. Step 5: Commit**

```bash
git add src/serpent/types tests/unit/test_containers.py
git commit -m "feat: add Vec and Map containers with host ScValType ordering

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: `Address`

**Files:**
- Create: `src/serpent/types/address.py`
- Test: `tests/unit/test_address.py`

**Interfaces:**
- `Address(strkey)`: accepts `G...`/`C...` (via `_strkey`; `ValueError` otherwise);
  `.strkey`; `.is_contract`/`.is_account`; rank 18; **ordering: accounts before
  contracts, then raw 32 bytes** (XDR `SCAddressType`: `ACCOUNT = 0 < CONTRACT =
  1`; `ScAddress` derives `Ord` on the discriminant) — pinned with a golden using
  the two Phase 0 identifiers (`G… < C…`); `require_auth()`/
  `require_auth_for_args(...)` exist, fully annotated, raising
  `NotImplementedError("Env runtime; sub-plan E")`.

- [ ] **Step 1: Tests** — Phase 0 identifiers round-trip; wrong-kind/corrupt
  rejection; `is_contract`; the G-before-C ordering golden via `val_cmp`; the
  NotImplementedError boundary.
- [ ] **Step 2–4: RED → implement → GREEN. Step 5: Commit**

```bash
git add src/serpent/types/address.py tests/unit/test_address.py
git commit -m "feat: add Address chain type over the internal strkey codec

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Decorators + `Env` authoring surface

**Files:**
- Create: `src/serpent/decorators.py`, `src/serpent/env.py`
- Test: `tests/unit/test_decorators.py`

**Interfaces:**
- **`errorcode(code: int) -> type[ContractError]`** — the field specifier for
  error declarations. REQUIRED because mypy never executes decorators: a bare
  `LimitExceeded = 7` is inferred `int` and `raise Error.LimitExceeded` fails
  strict mypy ("Exception must be derived from BaseException") — adversarial-
  review verified with live repros, including that decorator return-type tricks
  do NOT rescue the bare-int form. `errorcode(N)` is annotated to return
  `type[ContractError]`; at runtime it returns a placeholder the decorator
  replaces with a real named subclass. (This also hands sub-plan C an unambiguous
  `ast.Call` to read codes from.) **This corrects spec §2 and Phase 0 findings
  §4(a) — the spec edit ships with this plan.**
- `@contracterror`: for each `NAME = errorcode(N)` attribute, installs a generated
  `ContractError` subclass named `Error.NAME` with `code = N`; validates codes
  unique and `0 <= code < RESERVED_CODE_MIN`; bare-int members are a `ValueError`
  at class-creation time with a message showing the `errorcode(...)` form; stores
  `_serpent_type_ = {"kind": "error_enum", "cases": [...]}`.
- `@contracttype`: `@dataclass_transform()`-annotated; applies
  `dataclasses.dataclass(frozen=True, eq=True)`; validates field names are valid
  Symbols ≤ 30 chars and annotations are chain types, serpent-decorated classes,
  or `X | None`; stores `_serpent_type_ = {"kind": "struct", "fields": [...]}`.
  NOTE (3.11 floor): `dataclass_transform(frozen_default=...)` doesn't exist on
  3.11 and we take no runtime deps — consequence is only that mypy won't flag
  field mutation; runtime `FrozenInstanceError` still fires. Document in the
  docstring.
- `@contractevent`: dataclass-transform like `@contracttype`; metadata under
  `_serpent_type_ = {"kind": "event", ...}`; `publish(env)` exists, annotated,
  raises `NotImplementedError("sub-plan E")`.
- `@contract`: class-creation-time checks with clear errors — every public
  method's first parameter is literally `self` (exempt from the annotation rule);
  every OTHER parameter and the return annotated; method names valid Symbols
  ≤ 30 chars; if `__init__` exists it must be annotated `-> None` (it compiles to
  `__constructor`). Stores `_serpent_type_ = {"kind": "contract", "methods": [...]}`.
- `env.py`: authoring-surface `Env` — `storage()` returning typed instance/
  persistent/temporary buckets, `ledger()`, `events()`, all fully annotated with
  chain types, all bodies `raise NotImplementedError("sub-plan E")`. The complete
  type surface contracts import; zero runtime behavior.

- [ ] **Step 1: PROVE the gate first.** Before any implementation, write a
  three-line scratch snippet (`tests/unit/test_decorators.py` can host it as its
  first test-adjacent fixture):

```python
# tests/fixtures is created in Task 10; for THIS proof use a module-level check
# in test_decorators.py itself: the decorated forms below must pass
# `uv run mypy --strict tests/unit/test_decorators.py` BEFORE the runtime
# implementation is written (stub the decorators with typed signatures first).
```

Write the decorator/errorcode SIGNATURES (bodies `raise NotImplementedError`),
write the test file, and run `uv run mypy --strict` — it must pass on the
`raise Error.LimitExceeded` usage. Only then implement runtime behavior. This
front-loads the exact failure that would otherwise surface at Task 10's gate.

- [ ] **Step 2: Write the failing runtime tests**

```python
import pytest

# Submodule imports: the public serpent/__init__.py is assembled in Task 10.
from serpent.decorators import contract, contracterror, contracttype, errorcode
from serpent.env import Env
from serpent.errors import ContractError
from serpent.types import U32, String


def test_contracterror_members_are_exception_classes() -> None:
    @contracterror
    class Error:
        LimitExceeded = errorcode(7)
        Unauthorized = errorcode(2)

    assert issubclass(Error.LimitExceeded, ContractError)
    assert Error.LimitExceeded.code == 7
    with pytest.raises(ContractError) as exc_info:
        raise Error.LimitExceeded
    assert exc_info.value.code == 7


def test_contracterror_rejects_bare_int_reserved_and_duplicate() -> None:
    with pytest.raises(ValueError, match="errorcode"):
        @contracterror
        class Bare:
            X = 1                       # bare int: must instruct errorcode(...)
    with pytest.raises(ValueError):
        @contracterror
        class Reserved:
            X = errorcode(0xFFFF_FFFF)
    with pytest.raises(ValueError):
        @contracterror
        class Dup:
            X = errorcode(1)
            Y = errorcode(1)


def test_contracttype_kwargs_and_field_validation() -> None:
    @contracttype
    class Settings:
        counter_limit: U32
        display_name: String

    s = Settings(counter_limit=U32(3), display_name=String("hi"))
    assert s.counter_limit == U32(3)
    with pytest.raises(ValueError):
        @contracttype
        class Bad:
            this_field_name_is_way_over_thirty: U32


def test_contract_requires_self_and_annotations() -> None:
    with pytest.raises(ValueError):
        @contract
        class C1:
            def f(env: Env) -> None: ...      # no self
    with pytest.raises(ValueError):
        @contract
        class C2:
            def f(self, env) -> None: ...     # unannotated param (self exempt)
    with pytest.raises(ValueError):
        @contract
        class C3:
            def __init__(self, env: Env) -> U32: ...  # constructor must return None
```

- [ ] **Step 3–4: RED → implement → GREEN** (whole suite + mypy strict over src
  AND tests + ruff).
- [ ] **Step 5: Commit**

```bash
git add src/serpent/decorators.py src/serpent/env.py tests/unit/test_decorators.py
git commit -m "feat: add contract decorators with strict-clean errorcode form and Env surface

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Public API, strict-mypy fixture, semantics table

**Files:**
- Modify: `src/serpent/__init__.py` (replace the `hello()` stub — closes the
  deferred T1 minor), `src/serpent/types/__init__.py`, `pyproject.toml`
  (`[tool.mypy] files = ["src", "tests"]`)
- Create: `tests/fixtures/__init__.py`, `tests/fixtures/token_style.py`,
  `tests/semantics/__init__.py`, `tests/semantics/cases.py`,
  `tests/semantics/test_semantics.py`, `tests/unit/test_public_api.py`

**Interfaces:**
- `serpent/__init__.py` exports exactly: `Bool U32 I32 U64 I64 U128 I128 Symbol
  String Bytes Bytes32 Bytes64 Vec Map Address Timepoint Duration`, `bytes_n`,
  the decorators + `errorcode`, `Env`, `ContractError`, `ArithmeticOverflow`,
  `BadArgument`, `__version__`. `test_public_api.py` pins sorted `__all__`.
- `tests/fixtures/token_style.py`: realistic contract (struct, error enum via
  `errorcode`, storage chains, `raise`, `require_auth`) in amended spec-§2 style,
  imported by a test (`from tests.fixtures import token_style`) AND covered by
  the now-tests-wide `mypy --strict` — **the executable proof of the zero-plugin
  --strict claim.**
- `tests/semantics/cases.py` — THE cross-tier table, frozen as (per adversarial
  review: a bare Callable is opaque to sub-plan D's compiler; the source string
  is what makes it ONE table):

```python
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SemCase:
    name: str
    source: str            # single expression, eval-able in the chain-type
                           # namespace AND compilable by sub-plan D in a method body
    kind: Literal["value", "contract_error", "trap", "reject"]
    expect: object | None = None   # chain-type instance, for kind == "value"
    code: int | None = None        # contract error code, for kind == "contract_error"
    trap: type[BaseException] | None = None  # tier-1 builtin, for kind == "trap"
```

  Kinds map the exception rule across tiers: `value` → compare via `to_val()`
  where available; `contract_error` → tier 1 expects `ContractError` with `code`,
  tier 2 (sub-plan D) expects the on-chain error code; `trap` → tier 1 expects
  the named builtin, tier 2 expects a VM trap; **`reject`** → tier-1-only
  (authoring-time `TypeError`/`ValueError`; sub-plan C compile errors) — D skips
  these BY CONSTRUCTION (documented in the module docstring). ≥ 40 cases: checked
  ops at every boundary (incl. truncating division and `MIN % -1`), unary minus,
  reflected ops, division/modulo by zero, coercion accepts/rejects, cross-type
  rejects, symbol/bytes validation edges, Map ordering observables (incl. the
  Bytes-before-Symbol rank case), Vec bounds, error `.code` round-trips.
  `test_semantics.py` evals every case against the chain-type namespace and
  asserts per-kind.

- [ ] **Step 1: Tests first** (public-API pin; fixture import test; semantics
  runner proven on 3 cases, then filled to ≥ 40).
- [ ] **Step 2–4: RED → implement → GREEN**, then the full gate:
  `uv run pytest -q && uv run ruff check . && uv run mypy --strict`
- [ ] **Step 5: Commit**

```bash
git add src/serpent tests pyproject.toml
git commit -m "feat: assemble serpent public API with semantics table and strict-typed fixture

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- One job, matrix Python 3.11/3.12/3.13: setup-uv (verify the current major of
  `astral-sh/setup-uv` on the marketplace BEFORE writing the YAML; do not trust
  this plan's memory of `@v10`) with `enable-cache: true` →
  `uv sync --all-groups` → `uv run ruff check .` → `uv run ruff format --check .`
  (run a one-time format pass first if the tree isn't format-clean; exclude
  `spikes/` from format — spikes are frozen) → `uv run mypy --strict` →
  `uv run --frozen pytest -q`. (`testpaths = ["tests"]`, so spike tests are not
  collected in CI — deliberate; the spikes are frozen evidence, not CI subjects.)

- [ ] **Step 1: Run every command locally in sequence; fix format fallout if any.**
- [ ] **Step 2: Write the workflow; validate YAML parses
  (`uv run python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text())"`
  — add pyyaml to the dev group if absent, or use yamllint via uvx).**
- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml pyproject.toml uv.lock
git commit -m "ci: add lint, typecheck, and test workflow across supported pythons

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
