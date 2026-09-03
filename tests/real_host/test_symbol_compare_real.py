"""The compiled `symsmall_cmp` ordering, on the real host (review Important 2).

`tests/unit/test_emitter_symbol_compare.py` proves the emitted part against a
Python transcription of `Ord for SymbolSmall` under the strict mock, and
`tests/real_host/test_semantics_real.py` runs ONE ordering vector
(`Symbol("_") < Symbol("A")`, the frozen `cases.py` row) against the embedded
soroban-env-host. One vector is a thin differential for a decoding loop: the
prefix rule, the zero-group skip, the digit/capital/underscore/lowercase
boundaries, the nine-character limit of the small form, and the small-versus-
object arm through the guard were all proven only against transcriptions.

This module widens that leg. It compiles ONE contract with the four ordering
methods, deploys it on the embedded host, and asks it every vector
`test_every_ordering_agrees_with_ascii_text_order` uses, plus the boundaries
the review named. Each answer is checked TWICE:

* against tier 1 (`Symbol(a) < Symbol(b)` and friends), which is the frozen
  table's own oracle. A disagreement raises `FrozenTableDisagreement` -- the
  implementer returns BLOCKED and the controller rules (ruling E10). Nobody
  edits the emitter or `serpent.types` to make this green;
* against the sign of `RealEnv.compare(Symbol(a), Symbol(b))` -- the host's own
  `Compare` trait, asked directly with no contract in between. That is the
  check the compiled part is a transcription OF, so the two have to agree on
  every vector or the transcription is wrong somewhere the mock cannot see.

A host that REFUSES the compare is kept apart from a host that ANSWERS
differently, exactly as `test_semantics_real.py` keeps them apart (review B1):
a refusal is an emitter bug in the small-operand guard, not a table matter.
"""

from __future__ import annotations

import pytest

from serpent import Bool, Symbol
from serpent.compiler.frontend import compile_module
from serpent.emitter import build_wasm
from serpent.testing import FrozenTableDisagreement, RealContract, RealEnv, RealHostError

real = pytest.mark.real_host  # per-test (review M12): the meta-tests below run everywhere

#: Four ordering methods on two `Symbol`s, and nothing else -- so every answer
#: this module reads came out of the emitted `symsmall_cmp` (or, for the
#: small-versus-object pair, out of the guard's `obj_cmp` arm). Method names are
#: two characters: the host's own limit on an exported function name is 30.
_SOURCE = """\
from serpent import Bool, Env, Symbol, contract


@contract
class C:
    def lt(self, env: Env, a: Symbol, b: Symbol) -> Bool:
        return Bool(a < b)

    def le(self, env: Env, a: Symbol, b: Symbol) -> Bool:
        return Bool(a <= b)

    def gt(self, env: Env, a: Symbol, b: Symbol) -> Bool:
        return Bool(a > b)

    def ge(self, env: Env, a: Symbol, b: Symbol) -> Bool:
        return Bool(a >= b)
"""

#: The vectors. The first twelve are exactly
#: `test_emitter_symbol_compare.test_every_ordering_agrees_with_ascii_text_order`'s
#: list, so the two legs ask the same questions; the last three are the ones the
#: final review named as missing.
#:
#: None of them is arbitrary:
#:
#: * `("A", "AB")`, `("AB", "B")` and `("abc", "abcd")` are the PREFIX/padding
#:   cases -- `Iterator::cmp` ends the shorter side with `None`, which orders
#:   before any `Some`, and the emitted part spells that with a 0 sentinel;
#: * `("_", "0")`, `("9", "A")`, `("Z", "_")` and `("_", "a")` walk the four
#:   boundaries of the 6-bit code table (`_` = 1, digits 2..11, capitals 12..37,
#:   lowercase 38..63). `_` is where the packed codes and ASCII DISAGREE, which
#:   is why the part decodes rather than comparing bodies;
#: * `("_________", "________")` is the nine-versus-eight boundary of the small
#:   form, and the pair whose leading zero group the iterator has to skip;
#: * `("a", "a")` and `("abcdefghi", "abcdefghi")` are the equal pairs, where
#:   `le`/`ge` are true and `lt`/`gt` are false;
#: * `("abc", "abcdefghijk")` is SMALL versus OBJECT: eleven characters cannot
#:   be a `SymbolSmall`, so this pair takes the guard's `obj_cmp` arm and is the
#:   one vector here that never reaches `symsmall_cmp` at all.
SYMBOL_ORDERING_VECTORS: tuple[tuple[str, str], ...] = (
    ("A", "B"),
    ("A", "AB"),
    ("AB", "B"),
    ("Hello", "hello"),
    ("hello", "hellos"),
    ("_", "0"),
    ("_", "z"),
    ("_________", "________"),
    ("a", "a"),
    ("abcdefghi", "abcdefghi"),
    ("Z", "a"),
    ("9", "A"),
    ("abc", "abcd"),
    ("Z", "_"),
    ("_", "a"),
    ("abc", "abcdefghijk"),
)

#: The four methods, and what each one means at tier 1. Written as data so the
#: parametrized test cannot quietly stop asking one of them.
_OPS = ("lt", "le", "gt", "ge")


def _tier1(op: str, a: str, b: str) -> bool:
    left, right = Symbol(a), Symbol(b)
    if op == "lt":
        return left < right
    if op == "le":
        return left <= right
    if op == "gt":
        return left > right
    assert op == "ge", op
    return left >= right


def _from_sign(op: str, sign: int) -> bool:
    """What `RealEnv.compare`'s three-way answer says about `op`."""
    if op == "lt":
        return sign < 0
    if op == "le":
        return sign <= 0
    if op == "gt":
        return sign > 0
    assert op == "ge", op
    return sign >= 0


@pytest.fixture(scope="module")
def ordering_contract() -> RealContract:
    """Compiled and deployed ONCE: sixteen vectors times four methods is 64
    invocations, and a rebuild per invocation would make this the slowest module
    in the suite for no extra evidence."""
    built = build_wasm(compile_module(_SOURCE, "symbol_compare_real/ordering.py"))
    assert "symsmall_cmp" in built.runtime_parts_linked, (
        "the ordering part was not linked, so this module would prove nothing about it"
    )
    return RealEnv().deploy_wasm(built.wasm)


@real
@pytest.mark.parametrize(
    ("left", "right"),
    SYMBOL_ORDERING_VECTORS,
    ids=[f"{a}_vs_{b}" for a, b in SYMBOL_ORDERING_VECTORS],
)
def test_the_compiled_ordering_agrees_with_tier_1_and_with_the_hosts_compare(
    ordering_contract: RealContract, left: str, right: str
) -> None:
    try:
        answered = {op: ordering_contract.invoke(op, Symbol(left), Symbol(right)) for op in _OPS}
    except RealHostError as exc:
        raise AssertionError(
            f"the host refused the Symbol ordering {left!r} vs {right!r} "
            f"({exc.underlying}): an emitter bug in the small-operand guard, not a table "
            "disagreement (review B1)"
        ) from exc

    expected = {op: Bool(_tier1(op, left, right)) for op in _OPS}
    if answered != expected:
        raise FrozenTableDisagreement(
            f"symbol ordering {left!r} vs {right!r}: the real host answered {answered!r}, "
            f"tier 1 answers {expected!r}; controller decision required (ruling E10)"
        )

    sign = RealEnv().compare(Symbol(left), Symbol(right))
    from_compare = {op: Bool(_from_sign(op, sign)) for op in _OPS}
    if answered != from_compare:
        raise FrozenTableDisagreement(
            f"symbol ordering {left!r} vs {right!r}: the compiled part answered {answered!r}, "
            f"the host's own compare() says {sign} ({from_compare!r}); the emitted part is not "
            "a faithful transcription of Ord for SymbolSmall (ruling E10)"
        )


# --- meta-tests: unmarked, because they are about THIS MODULE ----------------


def test_the_vector_list_covers_every_shape_the_docstring_claims() -> None:
    """A vector list that quietly lost its prefix pair, its object pair or its
    equal pairs would still pass sixteen green tests and prove less. Each clause
    below is one sentence of the list's own comment, asserted."""
    vectors = SYMBOL_ORDERING_VECTORS
    assert any(a != b and b.startswith(a) for a, b in vectors), "no prefix pair"
    assert any(a == b for a, b in vectors), "no equal pair"
    assert any(len(a) == 9 or len(b) == 9 for a, b in vectors), "no nine-character pair"
    assert any(len(a) > 9 or len(b) > 9 for a, b in vectors), "no small-versus-object pair"
    assert {("_", "0"), ("9", "A"), ("Z", "_"), ("_", "a")} <= set(vectors), "a boundary is missing"


def test_the_ordering_vectors_are_not_all_the_same_answer() -> None:
    """The vacuity guard the trap table carries too: sixteen vectors that all
    order one way would pass a part that always answered `lt`."""
    signs = {(a > b) - (a < b) for a, b in SYMBOL_ORDERING_VECTORS}
    assert signs == {-1, 0, 1}, signs


def test_every_vector_is_a_legal_small_or_object_symbol() -> None:
    """`Symbol` refuses an illegal character or an over-long name, so this is
    what keeps a typo in the list from becoming a confusing host error."""
    for a, b in SYMBOL_ORDERING_VECTORS:
        assert Symbol(a) is not None and Symbol(b) is not None
