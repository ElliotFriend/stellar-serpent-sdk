"""Task 9: pre-emptive spec/XDR limit validation (the SPT5xxx band).

`validate_limits` re-checks, located, every cap `spec.sections` otherwise
enforces at emission time with no source location -- plus S23's wasm
export-arity cap, which nothing else in the codebase checks at all. The
boundary matrix (dossier-required: 1024/1025 encoded bytes including a
multibyte character straddling the boundary; 30/31; 60/61; 32/33 params) is
the heart of this file; a closing test ties the accepted boundary directly to
`spec.sections.build_spec_entries` actually succeeding, which is the
invariant this whole module exists to protect.

**Fix round 1** (controller review): SPT5002/SPT5003 originally checked
LENGTH only. A non-ASCII, length-legal type/case name was silently accepted
here and then blew up in `sections._check_name` with no location -- the same
class of gap SPT5001 already closes at the 30-character tier, three rows
wide (struct type names, error-enum type names, error-case names). Both
codes now check the Symbol charset too; the non-ASCII boundary tests and the
extended invariant test below are what would have caught this the first
time.
"""

from __future__ import annotations

import pytest

from serpent.compiler import codes
from serpent.compiler.diagnostics import Diagnostic, Diagnostics
from serpent.compiler.limits import EXPORT_PARAM_LIMIT, validate_limits
from serpent.compiler.loader import load_module
from serpent.decorators import NAME_LIMIT
from serpent.spec import build_spec_entries
from serpent.spec.sections import CASE_NAME_LIMIT, DOC_LIMIT, TYPE_NAME_LIMIT

PATH = "contracts/limits.py"

_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}


# --- test helpers --------------------------------------------------------------


def _check(source: str) -> Diagnostics:
    """Load `source` (asserting the loader itself is happy with it) and run
    `validate_limits` against a fresh sink, isolating its own diagnostics."""
    loaded = load_module(source, PATH)
    assert not loaded.diagnostics, [d.message for d in loaded.diagnostics.diagnostics]
    sink = Diagnostics()
    validate_limits(loaded, sink)
    return sink


def _accept(source: str) -> None:
    sink = _check(source)
    assert not sink, [(d.code, d.message) for d in sink.diagnostics]


def _reject(source: str) -> Diagnostic:
    sink = _check(source)
    assert len(sink) == 1, [(d.code, d.message) for d in sink.diagnostics]
    return sink.diagnostics[0]


def _assert_reject(diag: Diagnostic, code: str, substring: str) -> None:
    assert diag.code == code, f"expected {code}, got {diag.code}: {diag.message}"
    assert _INTENT[code] in diag.message, (
        f"{code}: message does not carry its registry intent\n  message: {diag.message}\n"
        f"  intent:  {_INTENT[code]}"
    )
    assert substring in diag.message, f"{code}: {substring!r} not in message: {diag.message}"
    assert diag.loc.path == PATH


# --- SPT5001: function/field/param names (length + Symbol charset, B11) -------


def _param_source(param_name: str) -> str:
    return f"""
from serpent import Env, U32, contract


@contract
class C:
    def act(self, env: Env, {param_name}: U32) -> U32:
        return {param_name}
"""


def test_a_parameter_name_at_the_cap_is_accepted() -> None:
    _accept(_param_source("a" * NAME_LIMIT))


def test_a_parameter_name_one_over_the_cap_is_rejected() -> None:
    """The boundary matrix's 30/31 case, and B11's central gap: `@contract`
    never checks a parameter's name at all."""
    name = "a" * (NAME_LIMIT + 1)
    diag = _reject(_param_source(name))
    _assert_reject(diag, "SPT5001", name)
    assert str(NAME_LIMIT + 1) in diag.message


def test_a_non_symbol_parameter_name_is_rejected() -> None:
    # A dash is not a valid Python identifier at all; a non-ASCII letter is a
    # valid Python identifier but outside the Symbol charset (val.SYMBOL_CHARS
    # is ASCII-only), so it exercises the charset branch without a SyntaxError.
    diag = _reject(_param_source("café"))
    _assert_reject(diag, "SPT5001", "outside [a-zA-Z0-9_]")


def test_the_constructor_parameter_name_is_checked_too() -> None:
    """B11's other named gap: `__init__`'s parameters are never checked by
    `decorators.py` either -- only `sections.py` catches them, at emission
    time. `__constructor` itself is exercised by every other passing test in
    this file (every contract here has a name-checked export)."""
    name = "a" * (NAME_LIMIT + 1)
    source = f"""
from serpent import Env, U32, contract


@contract
class C:
    def __init__(self, env: Env, {name}: U32) -> None:
        pass
"""
    diag = _reject(source)
    _assert_reject(diag, "SPT5001", name)


# --- SPT5002: struct / error-enum type names (length + Symbol charset) --------


def _type_name_source(name: str) -> str:
    # A module needs exactly one @contract class (SPT4019); every helper here
    # adds a trivial one so `_check`'s `not loaded.diagnostics` assertion
    # isolates the ONE limit under test.
    return f"""
from serpent import Env, U32, contract, contracttype


@contracttype
class {name}:
    field: U32


@contract
class C:
    def act(self, env: Env) -> U32:
        return U32(0)
"""


def test_a_type_name_at_the_cap_is_accepted() -> None:
    _accept(_type_name_source("A" * TYPE_NAME_LIMIT))


def test_a_type_name_one_over_the_cap_is_rejected() -> None:
    """The boundary matrix's 60/61 case."""
    name = "A" * (TYPE_NAME_LIMIT + 1)
    diag = _reject(_type_name_source(name))
    _assert_reject(diag, "SPT5002", name)
    assert str(TYPE_NAME_LIMIT + 1) in diag.message


def test_an_error_enum_type_name_is_checked_too() -> None:
    name = "E" * (TYPE_NAME_LIMIT + 1)
    source = f"""
from serpent import Env, U32, contract, contracterror, errorcode


@contracterror
class {name}:
    Bad = errorcode(1)


@contract
class C:
    def act(self, env: Env) -> U32:
        return U32(0)
"""
    diag = _reject(source)
    _assert_reject(diag, "SPT5002", name)


def test_a_non_ascii_struct_type_name_is_rejected_even_when_length_legal() -> None:
    """Fix round 1: the exact bug class controller review caught -- a struct
    name that is non-ASCII but LENGTH-legal (<= 60 encoded bytes) must still
    be rejected, or `sections._check_name` would raise `SpecNameError` for
    the SAME name later, with no location at all."""
    name = "é" + "T" * (TYPE_NAME_LIMIT - 2)
    assert len(name.encode("utf-8")) == TYPE_NAME_LIMIT
    diag = _reject(_type_name_source(name))
    _assert_reject(diag, "SPT5002", "outside [a-zA-Z0-9_]")


def test_a_non_ascii_error_enum_type_name_is_rejected_even_when_length_legal() -> None:
    """The same gap, the other decorated-type kind (`@contracterror`)."""
    name = "é" + "E" * (TYPE_NAME_LIMIT - 2)
    assert len(name.encode("utf-8")) == TYPE_NAME_LIMIT
    source = f"""
from serpent import Env, U32, contract, contracterror, errorcode


@contracterror
class {name}:
    Bad = errorcode(1)


@contract
class C:
    def act(self, env: Env) -> U32:
        return U32(0)
"""
    diag = _reject(source)
    _assert_reject(diag, "SPT5002", "outside [a-zA-Z0-9_]")


# --- SPT5003: error-enum case names (length + Symbol charset) ------------------


def _case_name_source(case_name: str) -> str:
    return f"""
from serpent import Env, U32, contract, contracterror, errorcode


@contracterror
class E:
    {case_name} = errorcode(1)


@contract
class C:
    def act(self, env: Env) -> U32:
        return U32(0)
"""


def test_a_case_name_at_the_cap_is_accepted() -> None:
    _accept(_case_name_source("C" * CASE_NAME_LIMIT))


def test_a_case_name_one_over_the_cap_is_rejected() -> None:
    """The boundary matrix's 60/61 case, and a real gap: `@contracterror`
    never checks a case's name at all."""
    name = "C" * (CASE_NAME_LIMIT + 1)
    diag = _reject(_case_name_source(name))
    _assert_reject(diag, "SPT5003", name)
    assert str(CASE_NAME_LIMIT + 1) in diag.message


def test_a_non_ascii_case_name_is_rejected_even_when_length_legal() -> None:
    """Fix round 1: the third row of the same bug class -- a case name that
    is non-ASCII but LENGTH-legal (<= 60 encoded bytes)."""
    name = "é" + "C" * (CASE_NAME_LIMIT - 2)
    assert len(name.encode("utf-8")) == CASE_NAME_LIMIT
    diag = _reject(_case_name_source(name))
    _assert_reject(diag, "SPT5003", "outside [a-zA-Z0-9_]")


# --- SPT5004: docstrings, counted in encoded bytes (B12) -----------------------


def _doc_source(text: str) -> str:
    return f"""
from serpent import Env, U32, contract, contracttype


@contracttype
class Documented:
    {text!r}
    field: U32


@contract
class C:
    def act(self, env: Env) -> U32:
        return U32(0)
"""


def test_a_doc_at_the_byte_cap_is_accepted() -> None:
    _accept(_doc_source("x" * DOC_LIMIT))


def test_a_doc_one_byte_over_the_cap_is_rejected() -> None:
    """The boundary matrix's 1024/1025 case."""
    diag = _reject(_doc_source("x" * (DOC_LIMIT + 1)))
    _assert_reject(diag, "SPT5004", str(DOC_LIMIT + 1))


def test_a_multibyte_doc_at_the_byte_cap_is_accepted() -> None:
    """A multibyte character straddling the boundary: 1023 one-byte
    characters plus one two-byte character is 1024 CHARACTERS by count but
    1024 encoded BYTES -- exactly at the cap, and must be accepted."""
    text = "x" * (DOC_LIMIT - 2) + "é"  # 'x' * 1022 + 'é'
    assert len(text) == DOC_LIMIT - 1
    assert len(text.encode("utf-8")) == DOC_LIMIT
    _accept(_doc_source(text))


def test_a_multibyte_doc_one_byte_over_the_cap_is_rejected() -> None:
    """The same straddle, one character longer: 1024 characters, but 1025
    encoded bytes -- counted in bytes (B12), so this must be rejected even
    though the CHARACTER count alone would sit exactly at the cap."""
    text = "x" * (DOC_LIMIT - 1) + "é"  # 'x' * 1023 + 'é'
    assert len(text) == DOC_LIMIT
    assert len(text.encode("utf-8")) == DOC_LIMIT + 1
    diag = _reject(_doc_source(text))
    _assert_reject(diag, "SPT5004", str(DOC_LIMIT + 1))


def test_a_method_doc_is_checked_too() -> None:
    text = "x" * (DOC_LIMIT + 1)
    source = f"""
from serpent import Env, U32, contract


@contract
class C:
    def act(self, env: Env) -> U32:
        {text!r}
        return U32(0)
"""
    diag = _reject(source)
    _assert_reject(diag, "SPT5004", "act")


def test_a_constructor_doc_is_checked_too() -> None:
    text = "x" * (DOC_LIMIT + 1)
    source = f"""
from serpent import Env, contract


@contract
class C:
    def __init__(self, env: Env) -> None:
        {text!r}
"""
    diag = _reject(source)
    _assert_reject(diag, "SPT5004", "__constructor")


# --- SPT5005: export parameter count (S23) --------------------------------------


def _param_count_source(count: int) -> str:
    params = ", ".join(f"p{i}: U32" for i in range(count))
    return f"""
from serpent import Env, U32, contract


@contract
class C:
    def act(self, env: Env, {params}) -> U32:
        return p0
"""


def test_an_export_at_the_param_cap_is_accepted() -> None:
    _accept(_param_count_source(EXPORT_PARAM_LIMIT))


def test_an_export_one_over_the_param_cap_is_rejected() -> None:
    """The boundary matrix's 32/33 case."""
    diag = _reject(_param_count_source(EXPORT_PARAM_LIMIT + 1))
    _assert_reject(diag, "SPT5005", "act")
    assert str(EXPORT_PARAM_LIMIT + 1) in diag.message


def test_the_leading_env_parameter_does_not_count_toward_the_cap() -> None:
    """`env: Env` is dropped from `contractspecv0`'s `inputs` (SS C.3); the
    32-parameter cap tracks the wasm export's real arity, which is what
    `env`-inclusive `EXPORT_PARAM_LIMIT` parameters plus `env` itself would
    still be -- so this must be accepted, not rejected."""
    _accept(_param_count_source(EXPORT_PARAM_LIMIT))


# --- the invariant: an accepted boundary must never make sections.py raise -----


@pytest.mark.parametrize(
    ("type_name", "case_name"),
    [
        pytest.param("T" * TYPE_NAME_LIMIT, "C" * CASE_NAME_LIMIT, id="all-ascii-at-cap"),
        pytest.param(
            "é" + "T" * (TYPE_NAME_LIMIT - 2), "C" * CASE_NAME_LIMIT, id="non-ascii-type-name"
        ),
        pytest.param(
            "T" * TYPE_NAME_LIMIT, "é" + "C" * (CASE_NAME_LIMIT - 2), id="non-ascii-case-name"
        ),
    ],
)
def test_every_accepted_boundary_together_still_lets_sections_build(
    type_name: str, case_name: str
) -> None:
    """B12/S23's whole point: whatever `validate_limits` accepts here must
    never make `build_spec_entries` raise. Stacks every cap at its boundary
    -- name lengths, doc length, and parameter count -- in one module and
    proves `sections.py` really does accept it.

    Fix round 1 extends this with the two non-ASCII, LENGTH-legal probes: the
    exact shape controller review caught silently passing `validate_limits`
    before this fix and then raising `SpecNameError` out of
    `sections._check_name` with no location. Each probe is still exactly at
    the 60-BYTE boundary (`assert`ed below) -- only the charset is broken --
    so this is a genuine regression test for the gap, not a length case in
    disguise.
    """
    assert len(type_name.encode("utf-8")) == TYPE_NAME_LIMIT
    assert len(case_name.encode("utf-8")) == CASE_NAME_LIMIT

    method_name = "m" * NAME_LIMIT
    param_name = "p" * NAME_LIMIT
    doc = "x" * DOC_LIMIT
    ctor_params = ", ".join(f"a{i}: U32" for i in range(EXPORT_PARAM_LIMIT))

    source = f"""
from serpent import Env, U32, contract, contracterror, contracttype, errorcode


@contracttype
class {type_name}:
    field: U32


@contracterror
class Err:
    {case_name} = errorcode(1)


@contract
class C:
    def __init__(self, env: Env, {ctor_params}) -> None:
        {doc!r}
        pass

    def {method_name}(self, env: Env, {param_name}: U32) -> U32:
        {doc!r}
        return {param_name}
"""
    loaded = load_module(source, PATH)
    assert not loaded.diagnostics, [d.message for d in loaded.diagnostics.diagnostics]
    sink = Diagnostics()
    validate_limits(loaded, sink)

    non_ascii = any(ord(char) > 127 for char in type_name + case_name)
    if non_ascii:
        # This IS the invariant firing correctly: validate_limits must catch
        # it here, so build_spec_entries never gets a chance to raise.
        assert sink, "a non-ASCII, length-legal type/case name must be rejected here"
        assert {d.code for d in sink.diagnostics} <= {"SPT5002", "SPT5003"}, sink.diagnostics
        return

    assert not sink, [(d.code, d.message) for d in sink.diagnostics]
    assert loaded.contract_cls is not None
    struct_cls = loaded.decorated_types_in_order[0].cls
    enum_cls = loaded.decorated_types_in_order[1].cls
    # Must not raise: the whole point of this test.
    build_spec_entries(loaded.contract_cls, types=(struct_cls, enum_cls))
