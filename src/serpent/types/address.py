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
`NotImplementedError` until sub-plan B.

**`require_auth()` takes no `Env`** -- that is the shipped signature, and it is
the form spec Sec.2's own example uses (`from_.require_auth()`). So the tier-1
model finds the env it is authorizing against in the AMBIENT INVOCATION FRAME
(`serpent._frame`, whose docstring carries the reasoning and the verified
`env -> types -> address -> env` import cycle that put the contextvar in a leaf
module). Everything about the auth model itself -- the allow-set, the
recording, the deep copy of the args -- lives in `Env._record_auth`, next to the
state it writes and the docstrings that say what it refuses to pretend to be.
"""

from typing import TYPE_CHECKING, Any, ClassVar, Self, cast

from serpent import _frame, _strkey
from serpent.types._base import _ChainPayload
from serpent.types.containers import Vec

if TYPE_CHECKING:
    # Annotation-only: importing `serpent.env` at runtime here would close the
    # `env -> types -> address -> env` cycle. `_frame` is the leaf that carries
    # the value across, typed `object` for exactly that reason.
    from serpent.env import Env

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
            raise ValueError(f"not an account (G...) or contract (C...) strkey: {strkey!r}")
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
        """Require this address to have authorized the current invocation.

        At tier 1 this resolves the `Env` whose invocation frame is active and
        records the authorization there (`Env._record_auth`): mock-all-auths, so
        it SUCCEEDS unless the env carries an allow-set this address is not in,
        in which case it raises `AuthorizationFailed`.

        Outside any frame it is a loud `RuntimeError` naming `deploy` and
        `env.frame()`. It has to be: with mock-all-auths a silent pass would
        succeed, and a green test would then be asserting an authorization that
        no invocation ever asked for.
        """
        _ambient_env()._record_auth(self, None)

    def require_auth_for_args(self, args: Vec[Any]) -> None:
        """Require authorization for this invocation with `args` substituted.

        Same model as `require_auth`, plus the recorded args -- a DEEP COPY, so
        the record is a snapshot of what was authorized (the host serializes
        them into the authorization entry). The frontend's escape-analysis
        exemption for this call site depends on that copy; see
        `Env._record_auth`.
        """
        _ambient_env()._record_auth(self, args)


def _ambient_env() -> "Env":
    """The `Env` whose invocation frame is active, or a loud refusal.

    The cast is sound by construction: `serpent.env.Env.frame` is the only
    writer of the contextvar, so the only thing it can hold is an `Env`. It is a
    cast rather than an `isinstance` narrow because naming `Env` at runtime here
    would close the import cycle the leaf module exists to break.
    """
    env = _frame.current()
    if env is None:
        raise RuntimeError(
            "require_auth outside any invocation frame: an authorization only exists "
            "inside a contract invocation, and there is none open. At tier 1: "
            "`instance = serpent.env.deploy(MyContract, env)`, then authorize inside "
            "`with env.frame():` (deploy's own frame counts -- a constructor may "
            "authorize)."
        )
    return cast("Env", env)
