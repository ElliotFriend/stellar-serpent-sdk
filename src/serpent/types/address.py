"""The `Address` chain type: an account (`G...`) or a contract (`C...`).

Built on the internal `serpent._strkey` codec, so serpent stays
zero-dependency. The strkey text is the value's payload -- it is what the
constructor takes, what `.strkey` gives back, and what `__reduce__` hands to
`copy`/`deepcopy`/`pickle`.

**Ordering: accounts before contracts, then the raw 32 bytes.** The host's
`ScAddress` is an XDR union whose discriminant is `SCAddressType`
(`SC_ADDRESS_TYPE_ACCOUNT = 0`, `SC_ADDRESS_TYPE_CONTRACT = 1`) and it derives
`Ord`, so the discriminant is compared first and only then the 32-byte body.
`_cmp_payload()` therefore returns `discriminant_byte + raw32`, which gives
`val_cmp` the host's answer for free.

An `Address` is always a host object on-chain, so `to_val()`/`from_val()` raise
`NotImplementedError` until sub-plan B, and `require_auth()` /
`require_auth_for_args()` need the `Env` runtime from sub-plan E -- they exist,
fully annotated, so contracts and the type checker can already see the shape.
"""

from typing import Any, ClassVar, Self

from serpent import _strkey
from serpent.types._base import _ChainPayload
from serpent.types.containers import Vec

# XDR SCAddressType discriminants, in ScAddress's derived Ord order.
_ACCOUNT = 0
_CONTRACT = 1

_PREFIXES = {
    "G": (_ACCOUNT, _strkey.VERSION_ACCOUNT),
    "C": (_CONTRACT, _strkey.VERSION_CONTRACT),
}


class Address(_ChainPayload[str]):
    """A Stellar account or Soroban contract identifier."""

    __slots__ = ("_kind", "_raw")

    _SCVAL_RANK: ClassVar[int] = 18

    _kind: int
    _raw: bytes

    def __init__(self, strkey: str) -> None:
        if not isinstance(strkey, str):
            raise TypeError(f"Address() takes a str strkey, not {type(strkey).__name__}")
        prefix = _PREFIXES.get(strkey[:1])
        if prefix is None:
            raise ValueError(
                f"not an account (G...) or contract (C...) strkey: {strkey!r}"
            )
        kind, version = prefix
        # Anything malformed past the prefix -- checksum, length, charset, or a
        # version byte that disagrees with the prefix -- raises the codec's own
        # ValueError, which names the actual problem.
        raw = _strkey.decode(version, strkey)
        object.__setattr__(self, "_payload", strkey)
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_raw", raw)

    @property
    def strkey(self) -> str:
        return self._payload

    @property
    def is_account(self) -> bool:
        return self._kind == _ACCOUNT

    @property
    def is_contract(self) -> bool:
        return self._kind == _CONTRACT

    def _order_key(self) -> bytes:
        """`SCAddressType` discriminant, then the raw 32 bytes -- the host's order."""
        return bytes([self._kind]) + self._raw

    def __repr__(self) -> str:
        return f"Address({self._payload!r})"

    def to_val(self) -> int:
        raise NotImplementedError("host object form; sub-plan B")

    @classmethod
    def from_val(cls, v: int) -> Self:
        raise NotImplementedError("host object form; sub-plan B")

    def require_auth(self) -> None:
        """Require this address to have authorized the current invocation."""
        raise NotImplementedError("Env runtime; sub-plan E")

    def require_auth_for_args(self, args: Vec[Any]) -> None:
        """Require authorization for this invocation with `args` substituted."""
        raise NotImplementedError("Env runtime; sub-plan E")
