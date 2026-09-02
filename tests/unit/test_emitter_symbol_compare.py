"""Two SMALL `Symbol`s compare in the GUEST, never through `obj_cmp`.

Review finding **B1** of sub-plan M1-F's adversarial plan review: the shipped
emitter lowered every `Symbol` `==`/`!=`/`<`/`<=`/`>`/`>=` to the host function
`obj_cmp`, unconditionally. The real `soroban-env-host` REFUSES that call when
BOTH operands are non-object `Val`s -- a `SymbolSmall` (9 characters or fewer,
tag 14) is non-object -- with `Error(Value, UnexpectedType)` ("two non-object
args to obj_cmp"), which the VM escalates to `Error(Context, InvalidAction)`.
The dossier fact: `examples/shapes.py`, deployed to testnet, traps on its
`area` method for exactly this reason, because `area` compares `tag()` against
`Symbol("Rect")` and both sides are `SymbolSmall`s. `tests/harness`'s mini host
accepted the call (its `obj_cmp` decodes both words to tier-1 values and
compares those), so tier 2a was silently green -- which is ruling E1's point
about what a green mini-host run is worth, arriving as a real bug.

## The ground truth (read before a byte was written)

The lowering mirrors the HOST, never tier 1. `soroban-env-common` at tag
`v28.0.2` is the pin.

`https://raw.githubusercontent.com/stellar/rs-soroban-env/v28.0.2/soroban-env-common/src/compare.rs`
-- `impl<E: Env> Compare<Val> for E`, the branches that decide a `Symbol` pair,
verbatim::

    fn compare(&self, a: &Val, b: &Val) -> Result<Ordering, Self::Error> {
        if a.get_payload() == b.get_payload() {
            // Fast-path exactly-equal values.
            return Ok(Ordering::Equal);
        }
        if a.is_object() || b.is_object() {
            // Delegate any object-comparing to environment.
            let v = self.obj_cmp(*a, *b)?;
            return if v == 0 {
                Ok(Ordering::Equal)
            } else if v < 0 {
                Ok(Ordering::Less)
            } else {
                Ok(Ordering::Greater)
            };
        }
        let a_tag = a.get_tag();
        let b_tag = b.get_tag();
        ...
                Tag::SymbolSmall => delegate_compare_to_wrapper!(SymbolSmall, a, b, self),

So the host's OWN model of "compare two `Val`s that are `Symbol`s" is exactly
the three-way split this emitter now lowers: word equality first, `obj_cmp`
when EITHER side is an object, and `SymbolSmall`'s own `Ord` when both are
small. `obj_cmp` is never reached with two small words.

`https://raw.githubusercontent.com/stellar/rs-soroban-env/v28.0.2/soroban-env-common/src/symbol.rs`
-- `impl Ord for SymbolSmall` and the iterator it orders over, verbatim::

    impl Ord for SymbolSmall {
        fn cmp(&self, other: &Self) -> Ordering {
            Iterator::cmp(self.into_iter(), *other)
        }
    }

    impl Iterator for SymbolSmallIter {
        type Item = char;

        fn next(&mut self) -> Option<Self::Item> {
            while self.0 != 0 {
                let res = match ((self.0 >> ((MAX_SMALL_CHARS - 1) * CODE_BITS)) & CODE_MASK) as u8 {
                    1 => b'_',
                    n @ (2..=11) => b'0' + n - 2,
                    n @ (12..=37) => b'A' + n - 12,
                    n @ (38..=63) => b'a' + n - 38,
                    _ => b'\0',
                };
                self.0 <<= CODE_BITS;
                if res != b'\0' {
                    return Some(res as char);
                }
            }
            None
        }
    }

**The reading, which Step 1 of the brief made a gate:** `Ord for SymbolSmall`
is `Iterator::cmp` over DECODED `char`s, so it is lexicographic order over the
symbol's TEXT in ASCII -- not over the packed 6-bit codes. The two disagree on
exactly one character: `_` is code 1 (so it sorts FIRST among the packed codes)
and ASCII 95 (so it sorts after every digit and every capital). The crate's own
`test_ord` states the same thing as an assertion, `assert_eq!(a.cmp(b),
a_sym.cmp(&b_sym))` over `&str` and `SymbolSmall` pairs. Tier 1's
`Symbol.__lt__` compares the text, so tier 1 and the host AGREE and this task
was not blocked -- and `cases.py:symbol_underscore_vs_A_ascii_order`, the
flagged sub-plan D/F divergence vector, resolves in tier 1's favour.

`symbol.rs` also carries an `impl<E: Env> Compare<Symbol> for E` that compares
the TAGS first (so every small symbol would sort before every symbol object).
That one is NOT the governing definition here: it is the typed-`Symbol`
comparison, while what a contract's `obj_cmp`-or-not decision has to agree with
is `Compare<Val>` above, which reaches `SymbolSmall::cmp` only when neither
side is an object and delegates every mixed pair to `obj_cmp`.

## What is pinned below

* **Structurally**, off `printer.disassemble`: the `obj_cmp` call sits inside
  the `then` arm of the object-tag guard, and the `else` arm is the guest-side
  answer -- `i64.ne` on the two words for `==`/`!=`, a call to the
  `symsmall_cmp` runtime part for the four orderings.
* **Behaviourally**, under a `FullHost` subclass whose `obj_cmp` refuses two
  non-object words the way the real host does. That host is defined HERE, not
  in `tests/harness`: E1's mini host is a model of tier 1 on purpose, and
  making it strict would change what every other tier-2a test is asserting.
  Task 4's real-host leg is the proof that outranks all of this.
"""

from __future__ import annotations

import random

import pytest

from serpent import val
from serpent.compiler.frontend import compile_module
from serpent.emitter import BuildResult, build_wasm
from serpent.emitter.printer import disassemble
from serpent.types import Symbol
from tests.harness import engine
from tests.harness.errors import HostTrap
from tests.harness.hostfns import FullHost

# --- the contract under test -------------------------------------------------

#: One method per operator, each a bare `Symbol` comparison of two parameters
#: so the lowering is the whole body. `Symbol` is an either-repr type (A3), so
#: the ABI prologue accepts both `SymbolSmall` and `SymbolObject` words for
#: every parameter -- which is what lets the mixed-representation pins below
#: hand a real object handle to the same export.
_SOURCE = '''\
"""Symbol comparison, one export per operator."""

from serpent import Bool, Env, Symbol, contract


@contract
class Compare:
    """Six exports, six operators, nothing else."""

    def eq(self, env: Env, a: Symbol, b: Symbol) -> Bool:
        return a == b

    def ne(self, env: Env, a: Symbol, b: Symbol) -> Bool:
        return a != b

    def lt(self, env: Env, a: Symbol, b: Symbol) -> Bool:
        return a < b

    def le(self, env: Env, a: Symbol, b: Symbol) -> Bool:
        return a <= b

    def gt(self, env: Env, a: Symbol, b: Symbol) -> Bool:
        return a > b

    def ge(self, env: Env, a: Symbol, b: Symbol) -> Bool:
        return a >= b
'''

_OPERATORS = ("eq", "ne", "lt", "le", "gt", "ge")

#: The four operators whose answer needs an ORDER, and therefore the part.
_ORDERINGS = ("lt", "le", "gt", "ge")


@pytest.fixture(scope="module")
def built() -> BuildResult:
    return build_wasm(compile_module(_SOURCE, "contracts/symbol_compare.py"))


# --- the strict host: `obj_cmp` as the real host implements it ----------------


class StrictObjCmpHost(FullHost):
    """`FullHost`, except that `obj_cmp` refuses two non-object words.

    That refusal IS the bug this module is about (review B1). The real host's
    `obj_cmp` answers `Error(Value, UnexpectedType)` -- "two non-object args to
    obj_cmp" -- which the VM turns into `Error(Context, InvalidAction)`, i.e. a
    trap with no error `Val` a client could classify, which is why `HostTrap`
    (`tests/harness/errors.py`: an env.json precondition violated) is the right
    class here and `HostError` is not.

    A subclass rather than a flag on `tests/harness`: the mini host models
    tier 1 by construction (E1), and tightening it for everyone would change
    what every other tier-2a assertion means.
    """

    def obj_cmp(self, left: int, right: int) -> int:
        if not val.is_object(left) and not val.is_object(right):
            raise HostTrap(
                "obj_cmp: two non-object args -- the real host answers "
                f"Error(Value, UnexpectedType) for ({left:#x}, {right:#x})"
            )
        return super().obj_cmp(left, right)


def _strict(built: BuildResult) -> tuple[StrictObjCmpHost, engine.MiniHost]:
    """One instance of the contract, linked against the strict host."""
    host = StrictObjCmpHost()
    mini = engine.MiniHost(built.wasm, imports=host.bindings())
    host.attach(mini)
    return host, mini


def _call(host: StrictObjCmpHost, mini: engine.MiniHost, method: str, a: int, b: int) -> bool:
    """One invocation; asserts the answer is a Bool `Val` and that no `obj_cmp`
    happened -- the whole point, since the real host refuses that call for two
    non-object words."""
    word = mini.invoke(method, a, b)
    assert word in (val.FALSE_VAL, val.TRUE_VAL), f"{method} returned {word!r}, not a Bool Val"
    assert "obj_cmp" not in host.call_names(), (
        f"{method} called obj_cmp on ({a:#x}, {b:#x}); the real host refuses that "
        "when both words are non-object"
    )
    return word == val.TRUE_VAL


def _run(built: BuildResult, method: str, a: int, b: int) -> bool:
    """`_call` on a freshly instantiated module -- the single-shot spelling."""
    host, mini = _strict(built)
    return _call(host, mini, method, a, b)


# --- structural: the obj_cmp call is GUARDED ---------------------------------


def _func_body(wat: str, name: str) -> list[str]:
    """The mnemonic lines of one `(func $name ...)` block, stripped."""
    lines = wat.splitlines()
    header = f"(func ${name} "
    start = next(i for i, line in enumerate(lines) if line.startswith(header))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == ")")
    return [line.strip() for line in lines[start + 1 : end]]


#: The object-tag guard, as the emitter spells it. `(a | b) & 0xFF >= 64` is
#: `is_object(a) or is_object(b)` for two tag bytes: a tag byte is >= 64 iff
#: bit 6 or bit 7 is set, and `or` sets a bit iff either operand had it, so the
#: one compare over the OR-ed tags is exactly the two-sided test -- with no
#: second relop and no `i64.extend_i32_u` to widen a flag.
_GUARD = (
    "i64.or",
    "i64.const 255",
    "i64.and",
    "i64.const 64",
    "i64.ge_u",
    "if (result i64)",
)


def _guard_index(body: list[str]) -> int:
    """Where the guard run starts. Exactly one per body, asserted."""
    hits = [
        i for i in range(len(body) - len(_GUARD) + 1) if tuple(body[i : i + len(_GUARD)]) == _GUARD
    ]
    assert len(hits) == 1, f"expected exactly one object-tag guard, found {len(hits)}"
    return hits[0]


def _part_func_name(wat: str) -> str:
    """The rendered name of the module's ONE runtime part.

    `printer.disassemble` names a defined function by its EXPORT when it has
    one and `$fn<defined-space index>` otherwise, so a runtime part -- which is
    never exported -- renders as `$fnN`. This contract exports one method per
    operator and links one part, so the single `$fn`-named function is it. Read
    out of the rendering rather than hard-coded, because the index moves the
    moment the contract gains a method.
    """
    names = [
        line.split()[1]
        for line in wat.splitlines()
        if line.startswith("(func $fn")  # `$fn<N>`: defined, not exported
    ]
    assert len(names) == 1, f"expected exactly one unexported defined function, found {names}"
    return names[0]


@pytest.mark.parametrize("method", _OPERATORS)
def test_the_obj_cmp_call_sits_inside_the_object_tag_guard(built: BuildResult, method: str) -> None:
    """The structural half of review B1: no UNCONDITIONAL `call $obj_cmp`.

    The `then` arm of the guard is where `obj_cmp` lives and the only place it
    lives, so a mixed or object/object pair still reaches the host (which is
    required: only the host can say a `SymbolObject` and a `SymbolSmall` spell
    the same text) while two small words never do.
    """
    body = _func_body(disassemble(built.wasm), method)
    assert body.count("call $obj_cmp") == 1, "one obj_cmp call site, in the guarded arm"
    guard = _guard_index(body)
    call = body.index("call $obj_cmp")
    else_ = body.index("else", guard)
    assert guard < call < else_, (
        f"{method}: obj_cmp at {call} must sit between the guard's `if` ({guard}) "
        f"and its `else` ({else_})"
    )


def test_equality_takes_a_word_compare_on_the_small_path(built: BuildResult) -> None:
    """`==`/`!=`: the fast arm is `i64.ne` on the two `Val` WORDS.

    Canonical `SymbolSmall` packing (`val.symbol_small`: 6 bits per character,
    high-order-first, zero-padded, one tag byte) makes word inequality exact
    for two small symbols, so no decode is needed to answer `==`. The flag is
    widened to `0`/`1` and handed to the same "compare the three-way answer
    against zero" tail every other route uses -- `0` for equal, `1` for
    unequal, and `==`/`!=` never read the SIGN.
    """
    for method in ("eq", "ne"):
        body = _func_body(disassemble(built.wasm), method)
        else_ = body.index("else", _guard_index(body))
        end = body.index("end", else_)
        assert [line for line in body[else_ + 1 : end] if not line.startswith("local.get")] == [
            "i64.ne",
            "i64.extend_i32_u",
        ], f"{method}: the small path is a word compare, not a call"


@pytest.mark.parametrize("method", _ORDERINGS)
def test_an_ordering_takes_the_symsmall_cmp_part_on_the_small_path(
    built: BuildResult, method: str
) -> None:
    """`<`/`<=`/`>`/`>=`: the fast arm calls the ONE new runtime part.

    Ordering cannot be read off the words: the packed 6-bit codes put `_`
    first, ASCII puts it after every digit and capital (see this module's
    docstring), and the canonical zero PADDING sits in the high bits, so a
    naive word compare of `Symbol("B")` against `Symbol("AB")` reads a pad
    group against a real character. The part decodes.
    """
    wat = disassemble(built.wasm)
    body = _func_body(wat, method)
    else_ = body.index("else", _guard_index(body))
    end = body.index("end", else_)
    part = _part_func_name(wat)
    assert [line for line in body[else_ + 1 : end] if not line.startswith("local.get")] == [
        f"call {part}"
    ], f"{method}: the small path is one call to the {part} part and nothing else"


def test_the_part_decodes_rather_than_comparing_the_packed_bodies(built: BuildResult) -> None:
    """The part's own body, structurally: it LOOPS and it REMAPS.

    Two constants say it is the host's iterator rather than a word compare:
    `i64.const 48` is `(MAX_SMALL_CHARS - 1) * CODE_BITS`, the shift that
    brings the next character code down; `i64.const 95` is `_`'s ASCII, the
    one code whose packed rank and ASCII rank disagree and therefore the one
    remap a body compare could never do. Two `loop`s per operand plus the
    outer one, and no `call` at all -- the part reaches neither the host nor
    another part.
    """
    wat = disassemble(built.wasm)
    body = _func_body(wat, _part_func_name(wat).lstrip("$"))
    assert body.count("loop") == 3, "one outer loop, plus one iterator loop per operand"
    assert body.count("i64.const 48") == 2, "the CODE_BITS * 8 shift, once per operand"
    assert body.count("i64.const 95") == 2, "`_`'s ASCII remap, once per operand"
    assert not [line for line in body if line.startswith("call ")], "the part calls nothing"


def test_only_the_orderings_link_the_part(built: BuildResult) -> None:
    """`runtime_parts_linked` names `symsmall_cmp`, and a module of pure
    equality comparisons does not -- the part is linked because an ORDERING
    reached it, not because a `Symbol` appeared."""
    assert "symsmall_cmp" in built.runtime_parts_linked
    eq_only = build_wasm(
        compile_module(
            "from serpent import Bool, Env, Symbol, contract\n"
            "\n"
            "\n"
            "@contract\n"
            "class C:\n"
            "    def eq(self, env: Env, a: Symbol, b: Symbol) -> Bool:\n"
            "        return a == b\n",
            "contracts/eq_only.py",
        )
    )
    assert "symsmall_cmp" not in eq_only.runtime_parts_linked


def test_the_needed_set_is_still_a_subset_of_the_linked_set(built: BuildResult) -> None:
    """The invariant Task 13 states: C's `runtime_parts_needed` is a HINT, and
    D links a superset. `symsmall_cmp` is added on the emitter side only -- the
    frontend never names it -- so this is the assertion that the direction of
    the inclusion did not flip."""
    compiled = compile_module(_SOURCE, "contracts/symbol_compare.py")
    assert compiled.runtime_parts_needed <= built.runtime_parts_linked


# --- behavioural: the pins under the strict host -----------------------------


def test_two_equal_small_symbols_compare_equal_without_the_host(built: BuildResult) -> None:
    ab = val.symbol_small("ab")
    assert _run(built, "eq", ab, ab) is True
    assert _run(built, "ne", ab, ab) is False


def test_two_different_small_symbols_compare_unequal_without_the_host(built: BuildResult) -> None:
    assert _run(built, "eq", val.symbol_small("ab"), val.symbol_small("ac")) is False
    assert _run(built, "ne", val.symbol_small("ab"), val.symbol_small("ac")) is True


def test_the_underscore_versus_A_vector_takes_the_hosts_ascii_order(built: BuildResult) -> None:
    """The flagged divergence vector, now answered in the GUEST.

    `Symbol("_") < Symbol("A")` is `False`: `Ord for SymbolSmall` compares
    DECODED characters, and `ord("_") == 95 > ord("A") == 65`. It would be
    `True` if the part compared packed codes, where `_` is 1 and `A` is 12 --
    which is why `symsmall_cmp` decodes rather than comparing bodies.
    """
    underscore = val.symbol_small("_")
    capital_a = val.symbol_small("A")
    assert val.symbol_char_code("_") < val.symbol_char_code("A")  # the codes disagree
    assert _run(built, "lt", underscore, capital_a) is False
    assert _run(built, "gt", underscore, capital_a) is True


@pytest.mark.parametrize(
    ("left", "right"),
    [
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
    ],
)
def test_every_ordering_agrees_with_ascii_text_order(
    built: BuildResult, left: str, right: str
) -> None:
    """`symsmall_cmp` against Python's own `str` comparison, which for this
    charset IS the ASCII order `Ord for SymbolSmall` produces (the crate's
    `test_ord` asserts the same equivalence, over `&str`).

    The pairs are not arbitrary: `("A", "AB")` and `("AB", "B")` are the
    prefix/padding cases (`Iterator::cmp` ends the shorter side with `None`,
    which is `Less`), `("_", "0")` and `("_", "z")` straddle the one character
    where the packed codes and ASCII disagree, and `_________` against
    `________` is the 9-versus-8-character boundary of the small form.
    """
    a = val.symbol_small(left)
    b = val.symbol_small(right)
    assert _run(built, "lt", a, b) is (left < right)
    assert _run(built, "le", a, b) is (left <= right)
    assert _run(built, "gt", a, b) is (left > right)
    assert _run(built, "ge", a, b) is (left >= right)
    assert _run(built, "eq", a, b) is (left == right)
    assert _run(built, "ne", a, b) is (left != right)
    # Tier 1 agrees, which is what made this task's Step 1 a pass and not a BLOCKED.
    assert (Symbol(left) < Symbol(right)) is (left < right)


# --- the differential against the pinned algorithm ---------------------------


def _host_chars(body: int) -> list[int]:
    """`SymbolSmallIter`, transcribed. The pinned source is in the module
    docstring; this is the same loop in Python, shift for shift.

    It exists to reach bodies `val.symbol_small` cannot build: the iterator
    SKIPS zero groups wherever they sit, and the host's `try_from_body` accepts
    any 54-bit word, so a non-canonical body with interior padding is a legal
    `SymbolSmall` that only this oracle can predict.
    """
    out: list[int] = []
    state = body & 0xFFFF_FFFF_FFFF_FFFF
    while state != 0:
        code = (state >> 48) & 0x3F
        state = (state << 6) & 0xFFFF_FFFF_FFFF_FFFF
        if code == 0:
            continue
        if code == 1:
            out.append(ord("_"))
        elif code <= 11:
            out.append(ord("0") + code - 2)
        elif code <= 37:
            out.append(ord("A") + code - 12)
        else:
            out.append(ord("a") + code - 38)
    return out


def _host_three_way(left: int, right: int) -> int:
    """`Ord for SymbolSmall` over two whole `Val` words: `Iterator::cmp` is a
    lexicographic compare of the decoded sequences, which is what Python's
    `list` comparison already is."""
    a = _host_chars(left >> 8)
    b = _host_chars(right >> 8)
    return (a > b) - (a < b)


def test_the_orderings_agree_with_the_pinned_algorithm_over_many_bodies(
    built: BuildResult,
) -> None:
    """`symsmall_cmp` against `_host_three_way` on every pair of a spread of
    bodies, INCLUDING non-canonical ones.

    The ASCII pins above are what keeps this honest -- they check the
    transcription against Python's own `str` order, independently -- and this
    is what gives the transcription reach: pseudo-random 54-bit bodies exercise
    interior zero groups, which is exactly where a part that assumed canonical
    high-side padding would answer confidently and wrongly.
    """
    words = [val.symbol_small(ch) for ch in val.SYMBOL_CHARS[:8]]
    words += [val.symbol_small(a + b) for a in "_0Az" for b in "_9Za"]
    words += [val.symbol_small(t) for t in ("Hello", "hello", "hellos", "_________")]
    rng = random.Random(7)
    words += [val.from_body_tag(rng.getrandbits(54), val.TAG_SYMBOL_SMALL) for _ in range(24)]
    host, mini = _strict(built)
    for left in words:
        for right in words:
            want = _host_three_way(left, right)
            assert _call(host, mini, "lt", left, right) is (want < 0)
            assert _call(host, mini, "gt", left, right) is (want > 0)


def test_word_equality_is_the_documented_bound_on_the_fast_path(built: BuildResult) -> None:
    """Where `==` and `Ord for SymbolSmall` part company, stated on purpose.

    The `==`/`!=` fast path is `i64.ne` on the WORDS, which the brief's design
    rule licenses because canonical packing makes word equality exact. Two
    DIFFERENT bodies can still decode to the same text -- the host's iterator
    skips zero groups wherever they are, so body `12` and body `12 << 6` both
    read as `"A"` -- and for those the guest answers "unequal" where the host's
    `Compare<Val>` would answer `Equal`.

    Nothing on chain can reach that state: a `SymbolSmall` only enters a
    contract from the host, which builds one from BYTES and always packs it
    canonically (`val.symbol_small` does the same, and is serpent's only
    producer). Pinned rather than left implicit, so that a future change which
    starts accepting caller-supplied `Val` payloads has to come back here.
    """
    canonical = val.symbol_small("A")
    shifted = val.from_body_tag(val.body_of(canonical) << 6, val.TAG_SYMBOL_SMALL)
    assert shifted != canonical
    assert _host_chars(val.body_of(shifted)) == _host_chars(val.body_of(canonical))
    assert _host_three_way(canonical, shifted) == 0  # the host: equal
    assert _run(built, "eq", canonical, shifted) is False  # the guest: unequal
    # The ORDERING route decodes, so it agrees with the host even here.
    assert _run(built, "lt", canonical, shifted) is False
    assert _run(built, "gt", canonical, shifted) is False


# --- the object arm is still the host's job ----------------------------------


def test_a_mixed_pair_still_goes_to_the_host(built: BuildResult) -> None:
    """A `SymbolObject` and a `SymbolSmall` spelling the SAME text are EQUAL,
    and only the host can say so -- their words differ in both tag and body.

    So the guard's `then` arm is not a leftover: `Compare<Val>` delegates
    every pair with an object in it to `obj_cmp`, and so does this lowering.
    The strict host permits the call here (one side IS an object), which is
    why this pin uses `FullHost` through the same subclass rather than
    asserting `obj_cmp` was avoided.
    """
    host = StrictObjCmpHost()
    mini = engine.MiniHost(built.wasm, imports=host.bindings())
    host.attach(mini)
    small = val.symbol_small("ab")
    wide = host._new(val.TAG_SYMBOL_OBJECT, Symbol("ab"))
    assert val.is_object(wide) and not val.is_object(small)
    assert mini.invoke("eq", small, wide) == val.TRUE_VAL
    assert mini.invoke("ne", small, wide) == val.FALSE_VAL
    assert host.count("obj_cmp") == 2, "the object arm is the host call, twice"


def test_a_long_symbol_is_unequal_to_a_short_one_through_the_host(built: BuildResult) -> None:
    """The brief's second equality pin: `Symbol("ab")` against an 11-character
    symbol, which has no small form at all."""
    host = StrictObjCmpHost()
    mini = engine.MiniHost(built.wasm, imports=host.bindings())
    host.attach(mini)
    small = val.symbol_small("ab")
    long = host.val_word(Symbol("abcdefghijk"))
    assert val.tag_of(long) == val.TAG_SYMBOL_OBJECT, "11 characters has no small form"
    assert mini.invoke("eq", small, long) == val.FALSE_VAL
    assert host.count("obj_cmp") == 1
