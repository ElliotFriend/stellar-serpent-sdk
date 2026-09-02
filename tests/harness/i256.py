"""The 128/256-bit piece, accessor, and division bindings, as a mini host.

Test-only. `engine.MiniHost` binds whatever callbacks it is handed; this module
supplies the ones D's 128-bit limb code reaches -- the `obj_from_*_pieces`
constructors, the `obj_to_*` limb accessors, and `{i,u}256_div`, which is the
whole of S13's division route. Kept OUT of `engine.py` on purpose: Task 12
centralizes host callbacks, and `engine.py` is the pinned VM configuration,
which should not grow an arithmetic model.

Every implementation here is a plain Python big-integer one. The point is not
speed, it is that the oracle is written independently of the guest limb code it
checks -- a carry dropped in `arith.py` cannot be dropped in the same direction
here.

## What is pinned, and what is NOT

* **Shapes and signedness ARE pinned**, from `serpent._host.functions_by_name`:
  `obj_from_i256_pieces(hi_hi: i64, hi_lo: u64, lo_hi: u64, lo_lo: u64)` -- the
  high limb is SIGNED and the other three are not, and `obj_from_i128_pieces`
  is `(hi: i64, lo: u64)` the same way. Getting that backwards is silent: every
  non-negative operand agrees.
* **Small-vs-object return form IS pinned** by `serpent.val`'s tag table: an
  `I256Val`/`U256Val` whose value fits the 56-bit small body comes back
  small-tagged (13/12), and only a larger one becomes an object handle. This is
  review B4's whole point, so the accessors below REFUSE a non-object argument
  exactly as the host does -- a guest that forgot the tag branch fails loudly
  here instead of on chain.
* **`i256_div`'s ROUNDING IS PROVEN ON THE REAL HOST (E15).** It is implemented
  below with RUST-TRUNCATED semantics (toward zero), which is what A4 requires
  and what Rust's `/` on `i256` does -- and `tests/real_host/test_host_facts_real.py`
  (parametrized over `tests/semantics/host_facts.py`'s `HOST_FACTS`) is where
  that stopped being an assumption: rows `i128_floordiv_truncates_toward_zero`,
  `i128_mod_takes_the_dividends_sign` and `i128_min_mod_minus_one_is_zero`
  measure the real host's `{i,u}256_div` route (128-bit division lowers to it,
  S13) on a negative dividend and at `MIN % -1`, and match this module's
  answer.
* **`i256_rem_euclid`/`u256_rem_euclid` are deliberately ABSENT.** Their docs
  say "Performs checked **Euclidean** modulo", which contradicts A4's
  dividend-signed `%` (`-7 % 2 == -1`, not `+1`) -- F.1.2, the dossier's "single
  most consequential arithmetic finding". D computes `%` as
  `lhs - (lhs // rhs) * rhs` in guest limb code, and not binding them here means
  a lowering that reached for one cannot even link.
"""

from collections.abc import Callable

from serpent import val
from tests.harness.errors import HostError

MASK64 = (1 << 64) - 1
MASK128 = (1 << 128) - 1

I128_MIN = -(1 << 127)
I128_MAX = (1 << 127) - 1
I256_MIN = -(1 << 255)
I256_MAX = (1 << 255) - 1
U256_MAX = (1 << 256) - 1

#: What a failed `{i,u}256_div` hands back. `HostError` (`tests.harness.errors`,
#: M-3) is the harness's model of an abort, so it raises one carrying a
#: distinctive Error `Val`. **This code is no longer a harness convention
#: standing in for an unpinned fact (E15): it is the UNDERLYING code the real
#: host reported for a 128-bit `//0`, probe-confirmed 2026-09-02 for both
#: signednesses** (`tests/semantics/host_facts.py`'s `DIV128_BY_ZERO_HOST_ERROR`,
#: `("Object", "ArithDomain")` -- `ArithDomain`'s discriminant is 0, `Object`'s
#: is 4). `tests/unit/test_harness_objects.py` derives both discriminants from
#: that same pinned fact via `stellar_sdk.xdr.SCErrorType`/`SCErrorCode`'s own
#: member names, so this literal cannot silently drift from it. Tests still
#: assert against this constant, never against a literal word.
DIV_ERROR_VAL = val.error_val(0, val.ERROR_TYPE_OBJECT)


class WideHostFailure(Exception):
    """The host refused: a `{i,u}256_div` `ScError`, or a mistyped accessor.

    Raised rather than returned because a host function that answers `ScError`
    aborts the invocation; `Wide256Host.bindings` re-raises it as
    `engine.HostError` so tests see the same class every other abort uses.
    """


def _s64(word: int) -> int:
    """One argument word reinterpreted as a SIGNED 64-bit limb.

    `engine._trampoline` hands every argument over as an unsigned `Val` word
    (P4: one conversion, in one place), so a parameter the pin types `i64`
    -- `obj_from_i256_pieces`' `hi_hi`, `obj_from_i128_pieces`' `hi` -- is
    reinterpreted HERE and nowhere else.
    """
    return val.as_i64(word)


def _truncated_div(lhs: int, rhs: int) -> int:
    """`lhs / rhs` TRUNCATED TOWARD ZERO -- Rust's `/`, and A4's `//`.

    Written out rather than borrowed from Python's `//`, which FLOORS: at
    `-7 / 2` the two disagree (`-3` here, `-4` there) and every test that
    reached for the wrong one would encode the wrong contract.
    """
    quotient = abs(lhs) // abs(rhs)
    return -quotient if (lhs < 0) != (rhs < 0) else quotient


class Wide256Host:
    """A store of 128/256-bit object handles, plus the bindings that use it.

    Handles are opaque on chain; here a handle's body is an index into
    `_objects`, which holds `(tag, value)` with `value` the MATHEMATICAL
    integer -- signed for the `i` forms, unsigned for the `u` forms. Keeping
    the model as a Python int rather than as limbs is deliberate: the limb
    decomposition is what is under test, so the oracle must not share it.
    """

    def __init__(self) -> None:
        self._objects: list[tuple[int, int]] = []
        #: Every binding the guest called, in order. A test can then assert
        #: that a lowering answered something WITHOUT going to the host -- the
        #: only observable difference between "decided before the host call"
        #: and "the host happened to agree", which is the whole content of
        #: A4's `MIN // -1` pre-branch (both routes end in an overflow, so no
        #: assertion on the VALUE can tell them apart).
        self.calls: list[str] = []

    # -- handles ---------------------------------------------------------------

    def _handle(self, value: int, tag: int) -> int:
        self._objects.append((tag, value))
        return val.from_body_tag(len(self._objects) - 1, tag)

    def _object(self, handle: int, tag: int) -> int:
        """The value behind `handle`, refusing any other form (review B4).

        `obj_to_*` accepts an OBJECT and nothing else on chain, so a small-form
        `Val` reaching one is a host conversion error -- which is exactly the
        bug a missing tag branch on the division result produces. Loud here.
        """
        if val.tag_of(handle) != tag:
            raise WideHostFailure(
                f"obj_to_* expects an object with tag {tag}, got tag "
                f"{val.tag_of(handle)} in {handle:#018x}; a small-form Val "
                "reaching an accessor means the caller skipped its tag branch"
            )
        stored_tag, value = self._objects[val.body_of(handle)]
        assert stored_tag == tag, f"handle {handle:#018x} is not a {tag} object"
        return value

    # -- 128-bit pieces ---------------------------------------------------------

    def obj_from_u128_pieces(self, hi: int, lo: int) -> int:
        return self._handle((hi << 64) | lo, val.TAG_U128_OBJECT)

    def obj_to_u128_hi64(self, obj: int) -> int:
        return self._object(obj, val.TAG_U128_OBJECT) >> 64

    def obj_to_u128_lo64(self, obj: int) -> int:
        return self._object(obj, val.TAG_U128_OBJECT) & MASK64

    def obj_from_i128_pieces(self, hi: int, lo: int) -> int:
        # `hi` is SIGNED per the pin; `lo` is not.
        return self._handle((_s64(hi) << 64) | lo, val.TAG_I128_OBJECT)

    def obj_to_i128_hi64(self, obj: int) -> int:
        # Python's `>>` on a negative int is arithmetic, so this IS the signed
        # high limb; `as_u64` puts it back into the word the guest reads.
        return val.as_u64(self._object(obj, val.TAG_I128_OBJECT) >> 64)

    def obj_to_i128_lo64(self, obj: int) -> int:
        return self._object(obj, val.TAG_I128_OBJECT) & MASK64

    # -- 256-bit pieces ---------------------------------------------------------

    def obj_from_u256_pieces(self, hi_hi: int, hi_lo: int, lo_hi: int, lo_lo: int) -> int:
        value = (hi_hi << 192) | (hi_lo << 128) | (lo_hi << 64) | lo_lo
        return self._handle(value, val.TAG_U256_OBJECT)

    def obj_from_i256_pieces(self, hi_hi: int, hi_lo: int, lo_hi: int, lo_lo: int) -> int:
        # Only `hi_hi` is signed (the pin: `('i64', 'u64', 'u64', 'u64')`).
        value = (_s64(hi_hi) << 192) | (hi_lo << 128) | (lo_hi << 64) | lo_lo
        return self._handle(value, val.TAG_I256_OBJECT)

    def _u256_limb(self, obj: int, shift: int) -> int:
        return (self._object(obj, val.TAG_U256_OBJECT) >> shift) & MASK64

    def _i256_limb(self, obj: int, shift: int) -> int:
        return (self._object(obj, val.TAG_I256_OBJECT) >> shift) & MASK64

    # -- 256-bit Vals: the small/object split review B4 is about -----------------

    def u256_val(self, value: int) -> int:
        """Encode a `U256Val`: small-tagged when it fits 56 bits, else an object."""
        if val.fits_small_u(value):
            return val.pack_small_u64(value, val.TAG_U256_SMALL)
        return self._handle(value, val.TAG_U256_OBJECT)

    def i256_val(self, value: int) -> int:
        """Encode an `I256Val`: small-tagged when it fits 56 bits, else an object."""
        if val.fits_small_i(value):
            return val.pack_small_i64(value, val.TAG_I256_SMALL)
        return self._handle(value, val.TAG_I256_OBJECT)

    def read_u256(self, word: int) -> int:
        """Decode a `U256Val` -- the inverse of `u256_val`."""
        if val.tag_of(word) == val.TAG_U256_SMALL:
            return val.unpack_small_u64(word, val.TAG_U256_SMALL)
        return self._object(word, val.TAG_U256_OBJECT)

    def read_i256(self, word: int) -> int:
        """Decode an `I256Val` -- the inverse of `i256_val`."""
        if val.tag_of(word) == val.TAG_I256_SMALL:
            return val.unpack_small_i64(word, val.TAG_I256_SMALL)
        return self._object(word, val.TAG_I256_OBJECT)

    # -- division ---------------------------------------------------------------

    def u256_div(self, lhs: int, rhs: int) -> int:
        """`lhs / rhs`, `ScError` on `rhs == 0` (unsigned division cannot overflow)."""
        left = self.read_u256(lhs)
        right = self.read_u256(rhs)
        if right == 0:
            raise WideHostFailure("u256_div by zero")
        return self.u256_val(left // right)

    def i256_div(self, lhs: int, rhs: int) -> int:
        """`lhs / rhs`, TRUNCATED TOWARD ZERO; `ScError` on zero or overflow.

        The rounding is PROVEN on the real host, not an assumption (E15, and
        this module's own docstring): `tests/real_host/test_host_facts_real.py`
        (parametrized over `tests/semantics/host_facts.py`'s `HOST_FACTS`) rows
        `i128_floordiv_truncates_toward_zero`, `i128_mod_takes_the_dividends_sign`
        and `i128_min_mod_minus_one_is_zero`, measured 2026-09-02, match this
        rounding on a negative dividend and at `MIN % -1`. `MIN256 / -1` is
        the one overflow an i256 division has.
        """
        left = self.read_i256(lhs)
        right = self.read_i256(rhs)
        if right == 0:
            raise WideHostFailure("i256_div by zero")
        if left == I256_MIN and right == -1:
            raise WideHostFailure("i256_div overflow: MIN / -1")
        return self.i256_val(_truncated_div(left, right))

    # -- binding table ----------------------------------------------------------

    def bindings(self) -> dict[str, Callable[..., int]]:
        """Every callback D's 128-bit lowerings can name, by pinned host-fn name.

        `WideHostFailure` is translated to `HostError` here rather than raised
        as itself, so a test sees the same abort class it sees for
        `fail_with_error`. `HostError` comes from `tests.harness.errors` (M-3),
        which is wasmtime-free, so this module stays usable as a pure
        arithmetic oracle without pulling wasmtime in.

        The eight 256-bit limb accessors are built from their shift rather than
        written out one by one: `hi_lo` and `lo_hi` are one character apart,
        and a transposed pair in a hand-written table is exactly the kind of
        silently-wrong wiring this whole harness exists to catch.
        """

        def failing(name: str, impl: Callable[..., int]) -> Callable[..., int]:
            def wrapped(*args: int) -> int:
                self.calls.append(name)
                try:
                    return impl(*args)
                except WideHostFailure as exc:
                    raise HostError(DIV_ERROR_VAL) from exc

            return wrapped

        table: dict[str, Callable[..., int]] = {
            "obj_from_u128_pieces": self.obj_from_u128_pieces,
            "obj_to_u128_hi64": self.obj_to_u128_hi64,
            "obj_to_u128_lo64": self.obj_to_u128_lo64,
            "obj_from_i128_pieces": self.obj_from_i128_pieces,
            "obj_to_i128_hi64": self.obj_to_i128_hi64,
            "obj_to_i128_lo64": self.obj_to_i128_lo64,
            "obj_from_u256_pieces": self.obj_from_u256_pieces,
            "obj_from_i256_pieces": self.obj_from_i256_pieces,
            "u256_div": self.u256_div,
            "i256_div": self.i256_div,
        }
        for name, shift in (("hi_hi", 192), ("hi_lo", 128), ("lo_hi", 64), ("lo_lo", 0)):
            table[f"obj_to_u256_{name}"] = self._u256_shift(shift)
            table[f"obj_to_i256_{name}"] = self._i256_shift(shift)
        return {name: failing(name, impl) for name, impl in table.items()}

    def _u256_shift(self, shift: int) -> Callable[[int], int]:
        return lambda obj: self._u256_limb(obj, shift)

    def _i256_shift(self, shift: int) -> Callable[[int], int]:
        return lambda obj: self._i256_limb(obj, shift)
