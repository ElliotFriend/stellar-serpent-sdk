"""The `Symbol` chain type.

A Symbol is the host's short identifier: 1-32 characters from `[a-zA-Z0-9_]`,
used for contract function names, map keys and event topics. Up to 9 characters
it packs into a `SymbolSmall` Val (6 bits per character); longer symbols are
host objects and await sub-plan B.

The comparison contract is the same one the rest of the value layer follows:
`__eq__` never raises, ordering is defined within the one `ScVal` case, and
there is **no coercion** -- `Symbol("counter") == "counter"` is `False`, because
a raw `str` is not a chain value and silently accepting one would hide the chain
type exactly where the compiler needs to see it.
"""

from typing import ClassVar, Self

from serpent import val
from serpent.types.buffers import _ChainPayload


class Symbol(_ChainPayload[str]):
    """The chain `Symbol`. Ordered by its (ASCII) UTF-8 bytes, like the host.

    The empty symbol is rejected: it is representable on-chain but always an
    authoring error (see `val.is_valid_symbol`).
    """

    __slots__ = ()

    _SCVAL_RANK: ClassVar[int] = 15

    def __init__(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError(f"Symbol() takes a str, not {type(text).__name__}")
        if not val.is_valid_symbol(text):
            raise ValueError(
                f"not a valid Symbol (1-{val.SCSYMBOL_LIMIT} characters from "
                f"[a-zA-Z0-9_]): {text!r}"
            )
        object.__setattr__(self, "_payload", text)

    @property
    def text(self) -> str:
        return self._payload

    def _order_key(self) -> bytes:
        return self._payload.encode("utf-8")

    def __repr__(self) -> str:
        return f"Symbol({self._payload!r})"

    def to_val(self) -> int:
        """The `SymbolSmall` form for up to 9 characters.

        Longer symbols are host objects: `NotImplementedError` until sub-plan B.
        """
        if val.fits_symbol_small(self._payload):
            return val.symbol_small(self._payload)
        raise NotImplementedError("host object form; sub-plan B")

    @classmethod
    def from_val(cls, v: int) -> Self:
        """Decode a `SymbolSmall` Val.

        Wrong tag -> `ValueError` (raised by the codec, naming both tags); the
        `SymbolObject` tag -> `NotImplementedError`, mirroring `to_val`.
        """
        if val.tag_of(v) == val.TAG_SYMBOL_OBJECT:
            raise NotImplementedError("host object form; sub-plan B")
        return cls(val.symbol_small_text(v))
