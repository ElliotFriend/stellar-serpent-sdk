"""`RealEnv`: the tier-2b façade over `serpent_host`, in serpent's own vocabulary.

The Rust layer speaks ScVal XDR bytes and strkey strings and knows nothing about
serpent's types (`host/serpent_host.pyi`). This module is the only place that
translation happens, and it deliberately mirrors the tier-1 `Env`/`deploy` verbs
-- `advance`, `storage(...)`, `invoke`, `events`, `auths` -- wherever the
semantics coincide, so a tier-1 test re-points at the real host by swapping one
fixture (dossier D.2) instead of being rewritten.

Three shapes here are not obvious and are each a review ruling:

* **`deploy_source(path, ...)` is the primary form, `deploy(cls, ...)` the
  convenience** (review B3). An example module loaded by path is not in
  `sys.modules`, so `inspect.getsourcefile(cls)` on its contract class raises --
  and the compiler needs the SOURCE FILE, not the class, because it compiles
  text. So the path is the input, and the class is discovered from the module
  the path produced.
* **the allow-set is re-set before every invoke** (review M6). `mock_auths`
  REPLACES the sdk's whole entry set, so the façade -- not the caller -- owns
  the complete list and rebuilds it per call.
* **every allowed address must be a CONTRACT strkey** (review B2). The sdk mocks
  auth by registering a `MockAuthContract` AT the authorizer's address, which a
  `G...` account cannot host; account auth needs real signatures and is M2's
  problem. The fence is at construction, before the extension is even required,
  so a Rust-less checkout gives the same answer.
"""

from __future__ import annotations

import contextlib
import importlib.util
import inspect
import sys
import typing
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from stellar_sdk import scval
from stellar_sdk.strkey import StrKey
from stellar_sdk.xdr import ContractEvent, DiagnosticEvent, SCVal

from serpent.decorators import _METADATA_ATTR
from serpent.emitter import build_file
from serpent.env import (
    DEFAULT_LEDGER_SEQUENCE,
    DEFAULT_LEDGER_TIMESTAMP,
    ChainValue,
    PublishedEvent,
    RecordedAuth,
)
from serpent.testing._errors import RealHostUnavailable, raise_from_failure
from serpent.testing._marker import unavailable_reason
from serpent.testing._scval import decode_loose, from_xdr, to_xdr
from serpent.types import Address, Vec

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from types import ModuleType

    import serpent_host

#: The ledger-config knobs the embedded host is built with. They are the sdk's
#: own test defaults, restated here because the Rust constructor takes them all
#: explicitly (there is no "default env" on that surface) and because two of
#: them are load-bearing in assertions: `min_persistent_entry_ttl` is what makes
#: a freshly written persistent entry read a relative TTL of 4095, and
#: `max_entry_ttl` is what `max_ttl()` reports one below.
DEFAULT_MIN_TEMP_ENTRY_TTL = 16
DEFAULT_MIN_PERSISTENT_ENTRY_TTL = 4096
DEFAULT_MAX_ENTRY_TTL = 6_312_000
DEFAULT_BASE_RESERVE = 5_000_000
DEFAULT_NETWORK_ID = bytes(32)

#: The protocol every `RealEnv` runs at. Pinned to the MAJOR of
#: `serpent._host._codegen.PINNED_TAG` by `test_real_env.py` (review E11): the
#: embedded host and the emitter's generated bindings have to be the same
#: release line, or a tier-2b answer is evidence about a different chain than
#: the one the wasm was built for.
DEFAULT_PROTOCOL = 28

#: The three storage buckets, spelled as the Rust layer expects them.
Durability = Literal["persistent", "temporary", "instance"]


def _require_host() -> Any:
    """The extension module, or `RealHostUnavailable` naming the fix.

    The import is INSIDE the function, every time. A module-level import would
    make `serpent.testing` unimportable on a Rust-less checkout -- which is
    where `tests/conftest.py` has to be able to read the marker -- and it would
    also outrun the `sys.modules["serpent_host"] = None` shim the skip-policy
    tests use to stand in for such a checkout.

    Returns `Any` rather than a typed module because there is no importable
    module object to annotate when the extension is absent; the TYPE checking
    happens on `RealEnv._raw`, declared as `serpent_host.RealEnv` against
    `host/serpent_host.pyi` (`mypy_path = ["host"]`, review m8).
    """
    try:
        import serpent_host
    except ImportError as exc:  # also covers `sys.modules[...] = None`
        raise RealHostUnavailable(unavailable_reason()) from exc
    return serpent_host


class RealEnv:
    """One embedded soroban-env-host, with serpent's `Env` verbs on the front.

    One env per thread (the Rust class is `unsendable`, P9), so parallelism
    across real-host tests is process-level only.
    """

    def __init__(
        self,
        *,
        timestamp: int = DEFAULT_LEDGER_TIMESTAMP,
        sequence: int = DEFAULT_LEDGER_SEQUENCE,
        auths: Iterable[Address] | None = None,
    ) -> None:
        """`auths=None` mocks EVERY authorization (tier-1 parity); a tuple is
        the allow-set, and every member of it must be a contract strkey (B2).

        The allow-set is validated before the extension is required, so the B2
        fence is the same `ValueError` whether or not the host is built.
        """
        self._allow: tuple[Address, ...] | None = None if auths is None else tuple(auths)
        if self._allow is not None:
            for who in self._allow:
                if not StrKey.is_valid_contract(who.strkey):
                    raise ValueError(
                        f"RealEnv(auths=...) takes contract (C...) strkeys, not {who.strkey!r}: "
                        "the test host mocks an authorization by registering a MockAuthContract "
                        "AT the authorizer's address, which an account address cannot host. "
                        "Account authorization needs real signatures (M2)."
                    )
        self._host = _require_host()
        self._raw: serpent_host.RealEnv = self._host.RealEnv(
            protocol_version=DEFAULT_PROTOCOL,
            sequence_number=sequence,
            timestamp=timestamp,
            network_id=DEFAULT_NETWORK_ID,
            base_reserve=DEFAULT_BASE_RESERVE,
            min_temp_entry_ttl=DEFAULT_MIN_TEMP_ENTRY_TTL,
            min_persistent_entry_ttl=DEFAULT_MIN_PERSISTENT_ENTRY_TTL,
            max_entry_ttl=DEFAULT_MAX_ENTRY_TTL,
        )
        self._sequence = sequence
        if self._allow is None:
            self._raw.mock_all_auths()

    # --- the ledger -----------------------------------------------------------

    @property
    def sequence(self) -> int:
        """The ledger sequence, tracked here because the Rust surface has no
        getter -- and `RealStorage.live_until` needs it to turn the host's
        relative TTL into the absolute quantity tier 1 speaks (B10)."""
        return self._sequence

    def protocol_version(self) -> int:
        return self._raw.protocol_version()

    def max_ttl(self) -> int:
        return self._raw.max_ttl()

    def advance(self, n: int) -> None:
        """Move the ledger forward `n` sequences -- tier 1's verb, verbatim.

        Tier 1's PRECONDITION too, with tier 1's wording (`serpent.env.Env.advance`):
        a ledger sequence does not go backwards, and a zero advance is a test-
        authoring mistake worth naming rather than absorbing. Sharing the
        precondition is not cosmetic -- a differential test that calls
        `advance(n)` on both legs from one table row must get one answer for a
        bad `n`, or the table has a row whose two legs disagree about something
        that is not a host fact at all.

        `set_ledger(sequence=...)` is the way to move the ledger absolutely,
        including backwards; this verb is the monotonic one.
        """
        if not isinstance(n, int) or isinstance(n, bool):
            raise TypeError(f"advance() takes an int, not {type(n).__name__}")
        if n <= 0:
            raise ValueError(f"advance() takes a positive number of ledgers, not {n}")
        self._sequence += n
        self._raw.set_ledger(sequence_number=self._sequence)

    def set_ledger(self, *, sequence: int | None = None, timestamp: int | None = None) -> None:
        """Set either ledger field absolutely. `None` leaves that field alone,
        which is the Rust layer's own convention for this call."""
        if sequence is not None:
            self._sequence = sequence
        self._raw.set_ledger(sequence_number=sequence, timestamp=timestamp)

    # --- host facts -----------------------------------------------------------

    def compare(self, a: object, b: object) -> int:
        """The host's `Compare<Val>` verdict for two chain values (review M2).

        Not `obj_cmp`, which refuses two small operands: this is the total order
        the host itself uses, which is what a tier-1 `val_cmp` row has to be
        compared against.
        """
        return self._raw.compare(to_xdr(a), to_xdr(b))

    def diagnostics(self) -> tuple[DiagnosticEvent, ...]:
        """The host's diagnostic buffer for the LAST invocation, decoded.

        This is where B5's real classification lives; `_errors._innermost_error`
        is the reader for it.
        """
        return tuple(DiagnosticEvent.from_xdr_bytes(raw) for raw in self._raw.diagnostics())

    # --- deploying ------------------------------------------------------------

    def deploy_source(self, path: Path, *args: object) -> RealContract:
        """Compile the contract at `path`, deploy it, and wrap it.

        THE primary form (review B3). The module is loaded by path -- and, like
        `tests/unit/test_examples.load_example`, deliberately NOT registered in
        `sys.modules` -- and handed to the `RealContract`, which is what lets
        `RealContractError.member` and the return-type decode find declarations
        that no import statement could reach.
        """
        module = _load_by_path(path)
        cls = _the_contract_class(module, path)
        return self._deploy(build_file(path).wasm, args, cls, module)

    def deploy(self, cls: type, *args: object) -> RealContract:
        """Convenience: deploy the module `cls` was declared in.

        Resolves the source file the only two ways available and raises rather
        than guessing, because a path-loaded class (B3) genuinely cannot be
        traced back to its file this way and a silent wrong answer here would
        compile some OTHER contract.
        """
        module = sys.modules.get(cls.__module__)
        path: str | None = getattr(module, "__file__", None)
        if path is None:
            try:
                path = inspect.getsourcefile(cls)
            except TypeError:
                path = None
        if path is None:
            raise ValueError(
                f"{cls!r} was loaded by path, so its source file cannot be recovered from "
                "the class; use RealEnv.deploy_source(path, ...) instead."
            )
        return self.deploy_source(Path(path), *args)

    def deploy_wasm(self, wasm: bytes, *args: object) -> RealContract:
        """Deploy pre-built wasm with no Python class behind it.

        Results decode through `decode_loose` (by ScVal kind alone), because
        without a class there is no declared return type to re-type them as.
        """
        return self._deploy(wasm, args, None, None)

    def register_raw(self, wasm: bytes, constructor_args: Sequence[object]) -> str:
        """Upload and instantiate; returns the `C...` strkey, nothing wrapped.

        No module behind it, so a constructor that fails with a contract code
        gets no `.member` -- which is honest: there is nothing here that
        declared one.
        """
        return self._register(wasm, constructor_args, None)

    def invoke_raw(self, address: str, method: str, args: Sequence[object]) -> bytes:
        """Invoke and return the result's ScVal XDR UNDECODED.

        For a test that owns the decode (Task 4's ABI probes): the point is to
        see the bytes the host produced, not serpent's reading of them. Errors
        are still re-raised typed -- that part is never the caller's job -- and
        the allow-set is re-set here too, so auth behaves identically whether a
        test goes through `RealContract` or around it.
        """
        args_xdr = [to_xdr(arg) for arg in args]
        return self._invoke(address, method, args_xdr, None)

    # --- internals ------------------------------------------------------------

    def _deploy(
        self,
        wasm: bytes,
        args: Sequence[object],
        cls: type | None,
        module: ModuleType | None,
    ) -> RealContract:
        return RealContract(self, Address(self._register(wasm, args, module)), cls, module)

    def _register(self, wasm: bytes, args: Sequence[object], module: ModuleType | None) -> str:
        """Upload + instantiate, with failures typed.

        The `CreateContractV2HostFn` authorization the sdk records for this call
        is skipped by the Rust layer (review M8), so a deploy needs no
        `mock_auths` set even in allow-set mode -- and `auths()` accumulation
        therefore starts after it, not with it.
        """
        try:
            return self._raw.register(wasm, [to_xdr(arg) for arg in args])
        except self._host.HostFailure as exc:
            raise_from_failure(exc, module, self.diagnostics())

    def _invoke(
        self,
        address: str,
        method: str,
        args_xdr: list[bytes],
        module: ModuleType | None,
    ) -> bytes:
        """One invocation, with the auth set re-established first (M6)."""
        if self._allow is not None:
            # `mock_auths` REPLACES the sdk's whole entry set, so the complete
            # list is rebuilt here per call: one entry per allowed address, for
            # THIS contract, method and argument list -- the shape the host
            # records for a bare `require_auth`.
            self._raw.mock_auths([(who.strkey, address, method, args_xdr) for who in self._allow])
        try:
            return self._raw.invoke(address, method, args_xdr)
        except self._host.HostFailure as exc:
            raise_from_failure(exc, module, self.diagnostics())


class RealContract:
    """One deployed contract, and the readers for what its last call did."""

    def __init__(
        self,
        env: RealEnv,
        address: Address,
        cls: type | None,
        module: ModuleType | None,
    ) -> None:
        self.env = env
        self.address = address
        self.cls = cls
        self._module = module

    def invoke(self, method: str, *args: object) -> object:
        """Call `method` and decode the result as the method DECLARES it.

        The declared return annotation is the type guidance -- on chain a struct
        and a `Map`, a union and a `Vec`, an int enum and a `U32` are the same
        three words (D6), so the declaration is the only thing that can tell
        them apart. `-> None` decodes as Void, which also CHECKS that the host
        returned Void rather than silently discarding a value.

        The type is resolved AFTER the call, not before: `invoke("no_such")` has
        to reach the host and come back as a `RealHostError`, not die in a local
        `getattr`.
        """
        result = self.env._invoke(
            self.address.strkey, method, [to_xdr(arg) for arg in args], self._module
        )
        return from_xdr(result, self._return_type(method))

    def events(self) -> tuple[PublishedEvent, ...]:
        """The LAST invocation's contract events, in order.

        Topics and data go through `decode_loose`: an event carries no declared
        types on the wire, and tier 1's `PublishedEvent` is the same coarse
        shape, which is what makes the two comparable.

        Note (review m7): the sdk's `Events::all()` already filters events from
        a `failed_call`, so a rolled-back publish never appears here at all --
        the rollback evidence is the host's `failed_call` flag one layer down,
        not an absence in this tuple.
        """
        published: list[PublishedEvent] = []
        for raw in self.env._raw.events():
            event = ContractEvent.from_xdr_bytes(raw)
            body = event.body.v0
            if body is None:
                # `body.v0` is Optional in the generated XDR because the union
                # is versioned. RAISE rather than skip: silently dropping an
                # event would make `events()` answer a SHORTER tuple than the
                # host published, and a differential comparing that tuple would
                # report the contract's behaviour as the disagreement instead of
                # this reader's blind spot.
                raise NotImplementedError(
                    f"the host published a contract event with body version "
                    f"{event.body.v!r}; serpent.testing reads v0 only"
                )
            topics = tuple(_loose(topic) for topic in body.topics)
            published.append((topics, _loose(body.data)))
        return tuple(published)

    def auths(self) -> tuple[RecordedAuth, ...]:
        """The LAST invocation's root-contract authorizations.

        The args are ALWAYS a `Vec`, never `None`: the host records the
        invocation's own argument list even for a bare `require_auth`, where
        tier 1 records `None`. Task 5's differential runner is what reconciles
        the two; flattening it here would destroy the evidence.

        `register`'s own `CreateContractV2HostFn` authorization never appears --
        the Rust layer drops non-contract functions (review M8) -- so
        accumulation starts after the deploy.
        """
        return tuple(
            (Address(who), _loose_vec(args))
            for who, _contract, _function, args in self.env._raw.auths()
        )

    def storage(self, durability: Durability) -> RealStorage:
        return RealStorage(self, durability)

    def budget(self) -> tuple[int, int]:
        """`(cpu instructions, memory bytes)` of the last invocation."""
        return self.env._raw.budget()

    def resources(self) -> dict[str, int] | None:
        """The last invocation's `InvocationResources`; `None` before the first
        one (the sdk panics there, review m14)."""
        return self.env._raw.resources()

    def diagnostics(self) -> tuple[DiagnosticEvent, ...]:
        """The last invocation's diagnostics. The buffer is the ENV's, not this
        contract's -- there is one host -- so this delegates rather than
        pretending otherwise."""
        return self.env.diagnostics()

    def _return_type(self, method: str) -> object:
        """What `method` declares it returns, or "whatever comes" with no class.

        A missing annotation and a `-> None` are the same request here: expect
        Void. `getattr(..., None)` rather than a bare `getattr`: a host that
        answered a call the class does not declare is not a place to raise
        `AttributeError` from.

        The no-class answer is bare `object`, which `_scval.decode` already
        reads as "decode this however it comes" -- a distinct marker would only
        duplicate that module's `_LOOSE`, which is private to it for the same
        reason it exists at all: `None` is a MEANINGFUL `ty` at this boundary.
        """
        if self.cls is None:
            return object
        function = getattr(self.cls, method, None)
        if function is None:
            return object
        hints = typing.get_type_hints(function)
        return hints.get("return", type(None))


class RealStorage:
    """One durability bucket of one contract, read and seeded directly.

    Direct storage access is what tier 3's seeding (Task 9) and the tier-1
    differential need: the host is the only thing that knows what a contract
    actually wrote, and reading it back through another invocation would be
    testing the contract's reader rather than its writes.
    """

    def __init__(self, contract: RealContract, durability: Durability) -> None:
        self._contract = contract
        self._durability = durability

    def get(self, key: object, ty: object) -> object:
        """The stored value as `ty`, or `None` when the entry is absent."""
        with self._typed():
            raw = self._raw.storage_get(self._address, self._durability, to_xdr(key))
        return None if raw is None else from_xdr(raw, ty)

    def has(self, key: object) -> bool:
        with self._typed():
            return self._raw.storage_has(self._address, self._durability, to_xdr(key))

    def set(self, key: object, value: object) -> None:
        """Write an entry from OUTSIDE the contract (tier-3 seeding, Task 9).

        Visible to the contract on its next call, which is the whole point: a
        test can put the ledger in a state no sequence of invocations reaches.
        """
        with self._typed():
            self._raw.storage_set(self._address, self._durability, to_xdr(key), to_xdr(value))

    def ttl(self, key: object) -> int | None:
        """Ledgers remaining EXCLUDING the current one -- the host's own
        relative quantity (review B10). `None` when the entry is absent or
        expired.

        `key` is IGNORED for the instance bucket: the instance sub-map has one
        shared live-until (the same reason tier 1's `extend_ttl` is keyless
        there), and the Rust layer refuses a key in that position.
        """
        key_xdr = b"" if self._durability == "instance" else to_xdr(key)
        with self._typed():
            return self._raw.storage_ttl(self._address, self._durability, key_xdr)

    def live_until(self, key: object) -> int | None:
        """The ABSOLUTE ledger the entry lives until -- `sequence + ttl`.

        The quantity tier 1's `_TtlState` speaks, and therefore the one the
        Task-5 differential compares. `None` propagates from `ttl`.
        """
        relative = self.ttl(key)
        return None if relative is None else self._contract.env.sequence + relative

    @property
    def _raw(self) -> serpent_host.RealEnv:
        return self._contract.env._raw

    @property
    def _address(self) -> str:
        return self._contract.address.strkey

    @contextlib.contextmanager
    def _typed(self) -> Iterator[None]:
        """Map a `HostFailure` out of any accessor to serpent's hierarchy.

        The diagnostics buffer IS passed on, which needed measuring before it
        was safe: the worry was that it holds the last INVOCATION's events, so a
        storage probe would be handed some earlier call's `underlying` error as
        if it were its own. Measured on this host, it does not -- a storage
        accessor that panics REPLACES the buffer with its own single event
        (a read on an undeployed address leaves exactly
        `[error, Error(Storage, MissingValue)]`, where a preceding failed invoke
        had left three events), so the B5 classification a panic would otherwise
        lose survives.
        """
        host = self._contract.env._host
        try:
            yield
        except host.HostFailure as exc:
            raise_from_failure(exc, self._contract._module, self._contract.env.diagnostics())


def _loose(sc: SCVal) -> ChainValue:
    """`decode_loose`, narrowed to the alias tier 1 uses for the same position.

    `decode_loose` is typed `-> object` because it answers by ScVal kind alone;
    every kind it answers with IS a `ChainValue` (Void is refused inside a
    container and cannot reach an event topic), so the cast states what the
    function's own docstring already promises.
    """
    return cast("ChainValue", decode_loose(sc))


def _loose_vec(args: list[bytes]) -> Vec[Any]:
    """An auth entry's recorded args as a `Vec`.

    Built by wrapping the ScVals in an ScVec and reading THAT loosely, rather
    than decoding each and guessing an element class: `decode_loose`'s vec arm
    already has the widening ladder for a heterogeneous argument list, and
    duplicating it here is how the two would drift.
    """
    vec = decode_loose(scval.to_vec([SCVal.from_xdr_bytes(arg) for arg in args]))
    return cast("Vec[Any]", vec)


def _load_by_path(path: Path) -> ModuleType:
    """Import a contract module BY PATH, without registering it.

    The same mechanism `tests/unit/test_examples.load_example` uses, and
    deliberately the same omission: no `sys.modules` entry, so nothing can
    accidentally reach this module by name and a second load is an honestly
    distinct set of class objects.
    """
    spec = importlib.util.spec_from_file_location(f"serpent_real_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"{path} is not an importable Python module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _the_contract_class(module: ModuleType, path: Path) -> type:
    """The one `@contract` class in `module`.

    Discovered, not named: a serpent module declares exactly one contract (the
    frontend enforces it), so anything the caller could pass to name it could
    only ever disagree with the file. Same rule as
    `tests/unit/test_env_differential._contract_class`.
    """
    found = [
        member
        for member in vars(module).values()
        if isinstance(member, type)
        and isinstance(vars(member).get(_METADATA_ATTR), dict)
        and vars(member)[_METADATA_ATTR].get("kind") == "contract"
    ]
    if len(found) != 1:
        raise ValueError(f"{path} declares {len(found)} @contract classes, not one")
    return found[0]
