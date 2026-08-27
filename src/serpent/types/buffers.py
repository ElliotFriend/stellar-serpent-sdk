"""The payload chain types: `String`, `Bytes`, and the fixed-length `BytesN`
family (`Bytes32`, `Bytes64`, `bytes_n(n)`).

Like the numeric types in `numeric.py`, these are part of serpent's behavioral
oracle, and they follow the same contracts:

* **Comparison.** `__eq__` never raises -- a foreign chain type, a non-chain
  object, or a raw `str`/`bytes` is simply unequal. **These types do not
  coerce**, which can surprise: `Symbol("a") == "a"` and `Bytes(b"a") == b"a"`
  are both `False` (there is no numeric-style "in-range int" rule for text or
  bytes, and silently accepting a raw `str` would hide the chain type at the
  one place the compiler needs to see it). Ordering is defined within one
  `ScVal` case only; anything else raises `TypeError`.
* **Immutability.** Values are frozen (`__slots__` plus a rejecting
  `__setattr__`) and reconstruct through their constructor, so `copy.copy`,
  `copy.deepcopy` and `pickle` all round-trip.
* **Val forms.** `serpent.val` owns all bit math. `String` and `Bytes` are
  always host objects, so `to_val()`/`from_val()` raise
  `NotImplementedError("host object form; sub-plan B")`.

`Bytes32` is an authoring-time refinement of the single `Bytes` `ScVal` case,
not a chain type of its own: equality, hashing and ordering all work across the
whole `Bytes` family by payload, which is what `val_cmp` will answer too.
"""

from typing import Any, ClassVar, Generic, NoReturn, Self, TypeVar, overload

from serpent.types.numeric import U32, _ChainScalar

_P = TypeVar("_P")


class _ChainPayload(Generic[_P]):
    """Immutable carrier for the chain types whose value is a text or byte
    payload.

    Mirrors `numeric._ChainScalar` rather than extending it: that class pins
    `_value: int`, which cannot also carry a `str` or `bytes` under
    `mypy --strict`. Both provide the same three things -- frozen instances, a
    constructor-based `__reduce__`, and the `_SCVAL_RANK`/`_cmp_payload` hooks
    that `types._ordering.val_cmp` reads off instances. (Folding both into one
    generic base in a `types/_base.py` is a natural follow-up once `Address`
    lands; it is not done here to keep this task additive.)

    Equality and ordering are answered on `_SCVAL_RANK` + payload, so a
    subclass that refines a chain type without changing its `ScVal` case
    (`Bytes32` under `Bytes`) compares equal to its base type.
    """

    __slots__ = ("_payload",)

    _SCVAL_RANK: ClassVar[int]
    _payload: _P

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __reduce__(self) -> tuple[type[Self], tuple[object, ...]]:
        """Reconstruct by re-running the (validating) constructor -- the default
        copy/pickle protocol cannot restore slots through `__setattr__`."""
        return (type(self), (self._payload,))

    def _order_key(self) -> bytes:
        """The payload as bytes, which is what the host compares."""
        raise NotImplementedError  # pragma: no cover - abstract

    def _cmp_payload(self) -> object:
        return self._order_key()

    def __hash__(self) -> int:
        return hash(self._payload)

    def __eq__(self, other: object) -> bool:
        """Never raises: a different `ScVal` case, a foreign chain type or a
        raw `str`/`bytes` is simply unequal."""
        if isinstance(other, _ChainPayload):
            other_payload: _ChainPayload[Any] = other
            return (
                other_payload._SCVAL_RANK == self._SCVAL_RANK
                and other_payload._order_key() == self._order_key()
            )
        return NotImplemented

    def _cmp_operand(self, other: object) -> bytes | None:
        """The other side's order key, or None to defer (NotImplemented).

        Ordering is defined within one `ScVal` case; a different case or any
        other chain value raises `TypeError` rather than inventing a total
        order that the host does not have here (`val_cmp` in Task 7 is where
        cross-type ordering lives).
        """
        if isinstance(other, _ChainPayload):
            other_payload: _ChainPayload[Any] = other
            if other_payload._SCVAL_RANK == self._SCVAL_RANK:
                return other_payload._order_key()
            raise TypeError(
                f"cannot order {type(self).__name__} against {type(other).__name__}"
            )
        if isinstance(other, _ChainScalar):
            raise TypeError(
                f"cannot order {type(self).__name__} against {type(other).__name__}"
            )
        return None

    def __lt__(self, other: Self) -> bool:
        o = self._cmp_operand(other)
        if o is None:
            return NotImplemented
        return self._order_key() < o

    def __le__(self, other: Self) -> bool:
        o = self._cmp_operand(other)
        if o is None:
            return NotImplemented
        return self._order_key() <= o

    def __gt__(self, other: Self) -> bool:
        o = self._cmp_operand(other)
        if o is None:
            return NotImplemented
        return self._order_key() > o

    def __ge__(self, other: Self) -> bool:
        o = self._cmp_operand(other)
        if o is None:
            return NotImplemented
        return self._order_key() >= o


class String(_ChainPayload[str]):
    """The chain `String`: arbitrary text, ordered by its UTF-8 bytes.

    Always a host object on-chain (there is no small form), so `to_val()` and
    `from_val()` raise `NotImplementedError` until sub-plan B. Text that cannot
    be UTF-8 encoded (a lone surrogate) has no on-chain representation and is
    rejected at construction.
    """

    __slots__ = ()

    _SCVAL_RANK: ClassVar[int] = 14

    def __init__(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError(f"String() takes a str, not {type(text).__name__}")
        try:
            text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"String is not UTF-8 encodable: {text!r}") from exc
        object.__setattr__(self, "_payload", text)

    @property
    def text(self) -> str:
        return self._payload

    def _order_key(self) -> bytes:
        return self._payload.encode("utf-8")

    def __repr__(self) -> str:
        return f"String({self._payload!r})"

    def to_val(self) -> int:
        raise NotImplementedError("host object form; sub-plan B")

    @classmethod
    def from_val(cls, v: int) -> Self:
        raise NotImplementedError("host object form; sub-plan B")


class Bytes(_ChainPayload[bytes]):
    """The chain `Bytes`: an immutable byte string, ordered bytewise.

    Indexing returns a `U32` because the host's `bytes_get` returns a `U32Val`
    -- chain types all the way down. Slicing returns a plain `Bytes` even from a
    fixed-length subclass, since the length invariant no longer holds. Always a
    host object on-chain, so `to_val()`/`from_val()` await sub-plan B.
    """

    __slots__ = ()

    _SCVAL_RANK: ClassVar[int] = 13
    #: Exact length required by this class; `None` means any length.
    LENGTH: ClassVar[int | None] = None

    def __init__(self, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"{type(self).__name__}() takes bytes, not {type(data).__name__}"
            )
        payload = bytes(data)  # copy: a caller's bytearray must not mutate us
        expected = self.LENGTH
        if expected is not None and len(payload) != expected:
            raise ValueError(
                f"{type(self).__name__}() takes exactly {expected} bytes, "
                f"got {len(payload)}"
            )
        object.__setattr__(self, "_payload", payload)

    @property
    def data(self) -> bytes:
        return self._payload

    def _order_key(self) -> bytes:
        return self._payload

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._payload!r})"

    def __len__(self) -> int:
        return len(self._payload)

    @overload
    def __getitem__(self, index: int) -> U32: ...

    @overload
    def __getitem__(self, index: slice) -> "Bytes": ...

    def __getitem__(self, index: int | slice) -> "U32 | Bytes":
        """`bytes[i]` -> `U32` (the host's `bytes_get`); a slice -> `Bytes`.

        Out of range raises `IndexError`, mirroring the host trap.
        """
        if isinstance(index, slice):
            return Bytes(self._payload[index])
        return U32(self._payload[index])

    # Ordering is widened from `Self` to the family root: every `BytesN` is the
    # same ScVal case as `Bytes`, so they are mutually comparable.
    def __lt__(self, other: "Bytes") -> bool:
        return super().__lt__(other)

    def __le__(self, other: "Bytes") -> bool:
        return super().__le__(other)

    def __gt__(self, other: "Bytes") -> bool:
        return super().__gt__(other)

    def __ge__(self, other: "Bytes") -> bool:
        return super().__ge__(other)

    def to_val(self) -> int:
        raise NotImplementedError("host object form; sub-plan B")

    @classmethod
    def from_val(cls, v: int) -> Self:
        raise NotImplementedError("host object form; sub-plan B")


class Bytes32(Bytes):
    """Exactly 32 bytes (hashes, ed25519 keys, contract ids)."""

    __slots__ = ()

    LENGTH: ClassVar[int | None] = 32


class Bytes64(Bytes):
    """Exactly 64 bytes (ed25519 signatures)."""

    __slots__ = ()

    LENGTH: ClassVar[int | None] = 64


# `Bytes32`/`Bytes64` are written as real `class` statements, not as
# `Bytes32 = bytes_n(32)`: a name bound to a factory call is a *variable* and
# `x: Bytes32` would not type-check under `mypy --strict`. The factory below
# hands those same classes back, so `bytes_n(32) is Bytes32` holds either way.
_BYTES_N_CACHE: dict[int, type[Bytes]] = {32: Bytes32, 64: Bytes64}


def bytes_n(n: int) -> type[Bytes]:
    """The fixed-length `Bytes` subclass of length `n`, cached by length.

    `bytes_n(32) is Bytes32` and `bytes_n(64) is Bytes64`. Other lengths are
    created on demand and cached, so identity holds for them too. **There is no
    `BytesN[32]` subscript form** -- a bare-int subscript is not a valid type
    under `mypy --strict`; contracts annotate with `Bytes32`/`Bytes64`.
    Annotating an arbitrary length awaits compiler support in sub-plan C.
    """
    if not isinstance(n, int) or isinstance(n, bool):
        raise TypeError(f"bytes_n() takes an int length, not {type(n).__name__}")
    cached = _BYTES_N_CACHE.get(n)
    if cached is not None:
        return cached
    if n < 0:
        raise ValueError(f"bytes_n() length must not be negative: {n}")

    class _BytesN(Bytes):
        __slots__ = ()

        LENGTH: ClassVar[int | None] = n

    _BytesN.__name__ = f"Bytes{n}"
    _BytesN.__qualname__ = f"Bytes{n}"
    _BytesN.__doc__ = f"Exactly {n} bytes."
    _BYTES_N_CACHE[n] = _BytesN
    return _BytesN
