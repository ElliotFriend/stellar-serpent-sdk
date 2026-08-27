"""The scaffolding every scalar chain value shares.

Two classes, no chain semantics of their own:

* `_ChainValue` -- a frozen carrier (`__slots__` plus a rejecting
  `__setattr__`), reconstruction through the validating constructor
  (`__reduce__`, which is what makes `copy`/`deepcopy`/`pickle` work on a
  slotted immutable object), and the two hooks that
  `types._ordering.val_cmp` reads off instances: the `_SCVAL_RANK` class
  variable and `_cmp_payload()`.
* `_ChainPayload` -- adds the comparison contract for the chain types whose
  value is a text or byte payload (`Symbol`, `String`, `Bytes`, `Address`):
  `__eq__` never raises, equality and ordering are answered on
  `(_SCVAL_RANK, _order_key())`, and ordering across `ScVal` cases raises
  `TypeError`.

This module holds what `numeric` and `buffers` previously duplicated. It
deliberately contains no arithmetic, no validation and no Val encoding -- the
per-type semantics stay in the modules that own each type.
"""

from typing import Any, ClassVar, Generic, NoReturn, Self, TypeVar

_P = TypeVar("_P")


class _ChainValue(Generic[_P]):
    """Base of every scalar chain value: frozen, reconstructable, rankable.

    `_payload` is the single constructor argument, so `__reduce__` can rebuild
    any subclass by re-running its validating constructor.
    """

    __slots__ = ("_payload",)

    _SCVAL_RANK: ClassVar[int]
    _payload: _P

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __reduce__(self) -> tuple[type[Self], tuple[object, ...]]:
        """Reconstruct by re-running the (validating) constructor.

        `__slots__` plus a rejecting `__setattr__` makes the default
        copy/pickle protocol (restore-then-setattr) impossible, so state is
        handed back as constructor arguments instead.
        """
        return (type(self), (self._payload,))

    def _cmp_payload(self) -> object:
        return self._payload


class _ChainPayload(_ChainValue[_P]):
    """Comparison contract for the text/bytes-payload chain types.

    Equality and ordering are answered on `_SCVAL_RANK` + `_order_key()`, so a
    subclass that refines a chain type without changing its `ScVal` case
    (`Bytes32` under `Bytes`) compares equal to its base type.
    """

    __slots__ = ()

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
        order that the host does not have here (`val_cmp` is where cross-type
        ordering lives).
        """
        if isinstance(other, _ChainPayload):
            other_payload: _ChainPayload[Any] = other
            if other_payload._SCVAL_RANK == self._SCVAL_RANK:
                return other_payload._order_key()
            raise TypeError(f"cannot order {type(self).__name__} against {type(other).__name__}")
        if isinstance(other, _ChainValue):
            # Any other scalar chain value (today: the numeric family).
            raise TypeError(f"cannot order {type(self).__name__} against {type(other).__name__}")
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
