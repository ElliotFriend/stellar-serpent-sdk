"""Tier 3: recorded testnet simulations replayed against tier 1 and the real host (U3/U5/K6).

No network at all, ever: tier 3 is fixture-only until a deployment is approved
(U3). Each fixture under `fixtures/testnet/shapes/` was recorded by
`serpent.testing.testnet.record_fixture` (`SERPENT_TESTNET_RECORD=1`, ruling
E14 as amended: `simulateTransaction` accepts a never-funded source, so the
recording asked for no account and signed nothing) against the deployed shapes
contract (U5). Replay seeds the fixture's footprint entries into a fresh
`RealEnv` running the DEPLOYED bytes -- fetched from the chain and committed as
`deployed.wasm` (K6) -- and into a tier-1 `Env` running HEAD's model, invokes,
and compares the three answers.

The header tests are UNMARKED on purpose (M4): they are the ones that keep the
fixtures honest -- that they exist, that they were recorded against the bytes
committed next to them, and that the module has no signing or submission path
(E14, D1) -- and none of that needs the Rust extension. Only the three-way
replay carries `real_host` (M12: per-test, never a module-level `pytestmark`).
"""

from __future__ import annotations

import ast
import base64
import hashlib
import inspect
import re
import typing
from pathlib import Path
from typing import Any

import pytest
from stellar_sdk.strkey import StrKey
from stellar_sdk.xdr import SCVal

from serpent import U32
from serpent.emitter import build_file
from serpent.env import Env, deploy
from serpent.testing import DEFAULT_PROTOCOL, RealContractError, RealEnv, RealHostError, testnet
from serpent.testing._scval import decode_loose, from_xdr
from serpent.testing.testnet import (
    DEFAULT_SOURCE,
    Durability,
    Fixture,
    _as_json,
    fixtures_under,
    load_fixture,
)
from tests.unit.test_emitter_end_to_end import EXAMPLE_SHAPES
from tests.unit.test_examples import load_example

#: The recorded tier-3 corpus, and the module the chain was running when it was
#: recorded. Both live in the same directory so neither can be replaced alone.
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "testnet" / "shapes"
DEPLOYED = FIXTURE_DIR / "deployed.wasm"
FIXTURES = fixtures_under(FIXTURE_DIR)

#: The deployed shapes contract, and the sha256 of the bytes it runs -- both
#: read off the chain during recording and pinned here.
CONTRACT_ID = "CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW"
DEPLOYED_SHA256 = "6a9dd13549bac20f2609ab3d74668963b5249a7943dc7f027cdf6c42bec86e33"

#: The one declared three-way divergence, with its reason (B1). `area` lowers
#: `shape.tag() == Symbol("Rect")` to an `obj_cmp` on two SMALL symbols in the
#: DEPLOYED bytes, which the host refuses -- so the chain traps and so does the
#: embedded host running those same bytes, while HEAD's model (Task 0 fixed the
#: lowering) answers the area of the `Rect(5, 2)` the chain holds. The row
#: retires at the next approved deployment (G): re-record, and this table goes
#: empty.
B1_DIVERGENCE: dict[str, object] = {"area": U32(10)}

real = pytest.mark.real_host  # per-test (M12); only the replay leg needs the host, and it says so

#: Two exact identifiers a simulation-only module must not so much as mention
#: (E14, D1): `Keypair` is the sdk's signer and `send_transaction` is
#: `SorobanServer`'s submission call.
FORBIDDEN_EXACT = frozenset({"Keypair", "send_transaction"})

#: ...and two PREFIXES, because the sdk spells both verbs several ways and an
#: exact list is a list of the ones someone thought of. `Server` submits with
#: `submit_transaction` and `submit_transaction_async`, neither of which is
#: `submit`; a transaction signs with `sign`, `sign_hashx` and
#: `sign_extra_signers_payload`. Any identifier STARTING with one of these
#: fails the fence, so the next spelling the sdk grows is caught too.
FORBIDDEN_PREFIXES = ("sign", "submit")


def _forbidden(identifiers: set[str]) -> set[str]:
    """The members of `identifiers` this fence refuses.

    Prefix matching, deliberately over-broad: a tier-3 module has no business
    spelling any word that starts with `sign` or `submit`, so the fence does
    not have to keep up with the sdk's method names.
    """
    return {
        name
        for name in identifiers
        if name in FORBIDDEN_EXACT or name.startswith(FORBIDDEN_PREFIXES)
    }


def _identifiers(tree: ast.AST) -> set[str]:
    """Every name the module BINDS, IMPORTS, READS or reaches as an attribute.

    Attributes are collected by their bare `.attr`, so `server.send_transaction`
    is caught without the test having to know what `server` is -- which is the
    point: the fence is "this text cannot submit anything", and a name that is
    never spelled cannot be called.

    **The known limit**: an attribute name BUILT AT RUNTIME is invisible here.
    `getattr(server, "send_" + "transaction")` spells neither forbidden name in
    the tree, and no AST walk can catch it. This fence is a guard against the
    module quietly growing a submission path, not a proof that no such path can
    be expressed in Python.
    """
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            seen.add(node.id)
        elif isinstance(node, ast.Attribute):
            seen.add(node.attr)
        elif isinstance(node, ast.alias):
            seen.add(node.name.rsplit(".", 1)[-1])
            if node.asname is not None:
                seen.add(node.asname)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            seen.add(node.name)
        elif isinstance(node, ast.arg | ast.keyword) and node.arg is not None:
            seen.add(node.arg)
    return seen


def test_the_testnet_module_has_no_signing_or_submission_path() -> None:
    """E14/D1, asserted on the SOURCE rather than trusted: tier 3 reads the
    chain and writes nothing to it. A recorded simulation needs no key, so the
    module holds none, and this test is what keeps it that way."""
    source = Path(testnet.__file__).read_text(encoding="utf-8")
    assert _forbidden(_identifiers(ast.parse(source))) == set()


def test_the_no_signing_fence_rejects_the_spellings_it_claims_to() -> None:
    """The fence proven to have TEETH, not merely to be quiet.

    A fence over a module that was written not to trip it passes whether or not
    it works, so the two spellings the exact-match version let through are put
    through the same predicate here and must be refused.
    """
    assert _forbidden(_identifiers(ast.parse("server.submit_transaction(tx)"))) == {
        "submit_transaction"
    }
    assert _forbidden(_identifiers(ast.parse("tx.sign_hashx(x)"))) == {"sign_hashx"}
    assert _forbidden(_identifiers(ast.parse("from stellar_sdk import Keypair"))) == {"Keypair"}
    assert _forbidden(_identifiers(ast.parse("server.send_transaction(tx)"))) == {
        "send_transaction"
    }
    # ...and the real module is clean by the SAME predicate, not a laxer one.
    source = Path(testnet.__file__).read_text(encoding="utf-8")
    assert _forbidden(_identifiers(ast.parse(source))) == set()


def test_the_default_source_is_a_valid_public_key() -> None:
    """The recorder builds an envelope for it, so `StrKey` has to accept it --
    which the brief's original zero-key did not. Never funded, deliberately:
    simulation does not need the account to exist (E14 as amended)."""
    assert StrKey.is_valid_ed25519_public_key(DEFAULT_SOURCE)


def test_the_fixtures_were_recorded_against_the_deployed_bytes() -> None:
    """The header check (M4): a fixture that has drifted from the contract, the
    bytes committed beside it or the protocol it was recorded under is a
    failure here, not a wrong answer three tests later."""
    assert FIXTURES, "no fixtures recorded"
    assert hashlib.sha256(DEPLOYED.read_bytes()).hexdigest() == DEPLOYED_SHA256
    for fixture in FIXTURES:
        assert fixture.contract_id == CONTRACT_ID
        assert fixture.wasm_sha256 == DEPLOYED_SHA256
        assert fixture.protocol == DEFAULT_PROTOCOL


def test_this_trees_shapes_build_differs_from_the_deployed_bytes_until_the_next_deploy() -> None:
    """B1: Task 0 changed the Symbol-compare lowering, so HEAD's `shapes.py` no
    longer builds the deployed bytes. This inverts when Elliot approves the
    M1-end deployment (G): flip the assertion then and retire this docstring."""
    built = build_file(EXAMPLE_SHAPES).wasm
    assert hashlib.sha256(built).hexdigest() != DEPLOYED_SHA256


def test_every_committed_fixture_round_trips_through_the_recorded_json(tmp_path: Path) -> None:
    """`Fixture`'s fields, `_as_json`'s keys and `load_fixture`'s readers are
    three spellings of one field set, and nothing but this test stops them
    drifting apart. Re-serializing also reproduces the committed file BYTE FOR
    BYTE, which pins the recorded format itself."""
    assert FIXTURES, "no fixtures recorded"
    for fixture in FIXTURES:
        rewritten = _as_json(fixture)
        assert rewritten == (FIXTURE_DIR / f"{fixture.method}.json").read_text(encoding="utf-8")
        path = tmp_path / f"{fixture.method}.json"
        path.write_text(rewritten, encoding="utf-8")
        assert load_fixture(path) == fixture


def _loose(b64: str) -> Any:
    """A recorded ScVal as the bare chain value the host stores.

    Loose on purpose (M4): the pin key is a UNION and arrives as a `Vec`, a
    stored `Color` as a `U32`. Both seeding legs put back exactly what the chain
    holds, and the re-typing happens where it happens on chain -- in the
    contract's own `get(..., ty)`.

    Typed `-> Any` rather than `-> object` because `decode_loose` answers by
    ScVal kind alone and tier-1 `set` takes the chain-value union: the ScVal is
    the fixture's authority on what this value is, and narrowing it here would
    be this test asserting a type the chain never promised.
    """
    return decode_loose(SCVal.from_xdr_bytes(base64.b64decode(b64)))


def _param_types(cls: type, method: str) -> list[object]:
    """`method`'s declared parameter types in SIGNATURE order, less `env`."""
    hints = typing.get_type_hints(getattr(cls, method))
    names = [
        name
        for name in inspect.signature(getattr(cls, method)).parameters
        if name not in ("self", "env")
    ]
    return [hints[name] for name in names]


def _return_ty(cls: type, method: str) -> object:
    """What `method` declares it returns; a missing annotation means Void."""
    return typing.get_type_hints(getattr(cls, method)).get("return", type(None))


def _tier1_bucket(env: Env, durability: Durability) -> Any:
    """Tier 1's storage bucket for a recorded entry's durability."""
    storage = env.storage()
    if durability == "persistent":
        return storage.persistent()
    if durability == "temporary":
        return storage.temporary()
    return storage.instance()


def _outcome(call: Any) -> object:
    """A value, or a normalized failure both host legs can be compared by.

    A trap is `("trap", underlying)` -- the innermost `Error(Type, Code)` the
    host wrote to its diagnostics, which is the only level that classifies
    anything (B5) -- and a contract's own code is `("error", code)`, the same
    shape `tests/real_host/test_examples_real.py` normalizes to.
    """
    try:
        return call()
    except RealContractError as exc:
        return ("error", exc.code)
    except RealHostError as exc:
        return ("trap", exc.underlying)
    except Exception as exc:  # tier 1's @contracterror members are exception classes
        code = getattr(type(exc), "code", None)
        if code is None:
            raise
        return ("error", code)


def _testnet_outcome(fixture: Fixture, return_ty: object) -> object:
    """The recorded answer, in the same vocabulary as `_outcome`.

    The error arm reads the pair out of the RPC's `HostError: Error(T, C)`
    headline rather than storing it separately, so the comparison is against
    the text that was actually recorded. A CONTRACT-typed chain error renders
    its code as `#N` there and as a decimal in `.underlying`, so that arm is
    refused rather than mistranslated: no fixture needs it yet, and guessing
    would be worse than saying so.
    """
    if fixture.result.ok:
        assert fixture.result.value_xdr is not None
        return from_xdr(base64.b64decode(fixture.result.value_xdr), return_ty)
    assert fixture.result.error_text is not None
    match = re.search(r"Error\((\w+), (\w+)\)", fixture.result.error_text.splitlines()[0])
    assert match is not None, f"unparseable RPC error headline: {fixture.result.error_text!r}"
    return ("trap", (match.group(1), match.group(2)))


@real
@pytest.mark.parametrize("fixture", FIXTURES, ids=[fixture.method for fixture in FIXTURES])
def test_the_real_host_and_tier_1_agree_with_testnet(fixture: Fixture) -> None:
    """Three answers to one call, from three places, compared (U5, K6, K7).

    Same bytes on the two host legs' terms: the real leg deploys the DEPLOYED
    wasm rather than HEAD's build, because Task 0's B1 fix changed what
    `shapes.py` compiles to and the fixture was recorded against the older
    module. Tier 1 runs HEAD's model, which is the leg that is allowed to
    differ and does, for exactly one method (`B1_DIVERGENCE`).

    Seeding puts both hosts into the ledger state the simulation READ, entry by
    entry, keys and values decoded loosely -- the bare word the chain stores,
    re-typed by the contract's own `get(..., ty)` on every leg (D6/M4).
    """
    shapes = load_example(EXAMPLE_SHAPES)
    return_ty = _return_ty(shapes.Drawing, fixture.method)
    args = [
        from_xdr(base64.b64decode(arg), ty)
        for arg, ty in zip(fixture.args_xdr, _param_types(shapes.Drawing, fixture.method))
    ]

    # ONE seeding sequence, replayed on both legs: the instance sub-map's pairs
    # go to the instance bucket, every other entry to the bucket the chain had
    # it in. Two loops over the same list would be two chances to seed the legs
    # differently, which is the one thing a differential must not do.
    seeding: list[tuple[Durability, str, str]] = [
        ("instance", key_xdr, value_xdr) for key_xdr, value_xdr in fixture.instance
    ] + [(entry.durability, entry.key_xdr, entry.value_xdr) for entry in fixture.seeded]

    real_env = RealEnv(sequence=fixture.ledger)
    contract = real_env.deploy_wasm(DEPLOYED.read_bytes())
    for durability, key_xdr, value_xdr in seeding:
        contract.storage(durability).set(_loose(key_xdr), _loose(value_xdr))
    # `invoke_raw` + an explicit decode, not `invoke`: `deploy_wasm` has no
    # class behind it, so nothing there could know the declared return type.
    real_answer = _outcome(
        lambda: from_xdr(
            real_env.invoke_raw(contract.address.strkey, fixture.method, args), return_ty
        )
    )

    env = Env(sequence=fixture.ledger)
    instance: Any = deploy(shapes.Drawing, env)
    with env.frame():
        for durability, key_xdr, value_xdr in seeding:
            _tier1_bucket(env, durability).set(_loose(key_xdr), _loose(value_xdr))
        tier1_answer = _outcome(lambda: getattr(instance, fixture.method)(env, *args))

    testnet_answer = _testnet_outcome(fixture, return_ty)

    if fixture.result.ok:
        assert real_answer == tier1_answer == testnet_answer
    else:
        assert fixture.method in B1_DIVERGENCE, (
            f"{fixture.method} failed on chain and no divergence is declared for it: "
            f"{fixture.result.error_text}"
        )
        assert real_answer == testnet_answer
        assert tier1_answer == B1_DIVERGENCE[fixture.method]
