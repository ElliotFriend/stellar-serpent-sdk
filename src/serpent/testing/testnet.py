"""Tier 3: testnet simulations, recorded once as fixtures and replayed offline.

Tier 1 is the model, tier 2b (`_real.py`) is the embedded host, and this is the
third leg: what the REAL network answers for a contract that is really
deployed. The fixture source is the deployed `examples/shapes.py` contract (U5),
whose bytes were fetched from the chain and committed beside the fixtures (K6),
and whose every entry point `simulateTransaction` can evaluate without signing
or submitting anything (K7).

The tier is FIXTURE-ONLY until a deployment is approved (U3): it reaches the
network exactly once, when a human records, and never again. `record_fixture`
writes a JSON file and `tests/real_host/test_testnet_fixtures.py` replays it
with no network at all, because a test that needs the internet to pass is not a
test the suite can rely on.

**This module simulates. It does not sign, and it does not submit** (ruling
E14, D1). `simulateTransaction` is a read-only RPC: it takes an unsigned
transaction envelope, runs the invocation against the current ledger snapshot,
and hands back the return value, the footprint and the diagnostics without
changing anything. So there is no `Keypair` here, no signature, and no
submission call -- and `test_the_testnet_module_has_no_signing_or_submission_path`
asserts that by AST over this file's source rather than trusting the claim.

**`DEFAULT_SOURCE` is a fixed, never-funded public key.** Simulation does not
check that the source account exists (probe-verified against
`soroban-testnet.stellar.org`, protocol 28, during the plan review for this
task), so recording needs no account, no friendbot call and no secret. The key
below was derived from a publicly published seed phrase and carries no secret
worth protecting; it MUST NEVER BE FUNDED, because the moment it holds a
balance anyone with that public phrase can move it. `SOURCE_ENV_VAR` is the
override for a caller who would rather name their own account.

What a fixture records, and why each part is needed to replay it (M4):

* the RESULT -- the returned ScVal, or the RPC's error text for a simulation
  that trapped. An error fixture is a first-class fixture, not a failure to
  record: the deployed shapes contract's `area` traps on chain today because
  the deployed bytes carry the B1 Symbol-compare bug, and reproducing that trap
  on the embedded host running the SAME bytes is exactly what the tier is for;
* the FOOTPRINT's contract-data entries, fetched from the ledger and stored as
  XDR so a replay can seed a fresh host into the state the chain was in. The
  contract INSTANCE entry is split out (`Fixture.instance`) because its value
  is not a stored word but a `ContractInstance` whose `storage` sub-map holds
  the instance-durability entries one by one. `CONTRACT_CODE` footprint keys
  are skipped: that is the module itself, not state, and the replay supplies it
  by deploying `deployed.wasm`;
* the HEADER -- contract id, the deployed wasm's sha256, the protocol version,
  the ledger the simulation saw, the RPC build and the date -- so a fixture
  that has drifted from the chain or from the committed bytes is a loud test
  failure and not a silent wrong answer.

Keys and values are recorded and replayed as XDR and decoded LOOSELY
(`_scval.decode_loose`): a union arrives as a `Vec`, an int enum as a `U32`.
That is not a shortcut, it is what the host stores -- the contract re-types the
bare word through its own `get(..., ty)` on both legs, exactly as on chain
(D6, and `_scval`'s own docstring).
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from stellar_sdk import Account, Address, SorobanServer, TransactionBuilder
from stellar_sdk import xdr as stellar_xdr

from serpent.testing._scval import encode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from stellar_sdk.soroban_rpc import SimulateTransactionResponse

#: Testnet's Soroban RPC endpoint and network passphrase.
TESTNET_RPC = "https://soroban-testnet.stellar.org"
TESTNET_PASSPHRASE = "Test SDF Network ; September 2015"

#: `"1"` enables the recorder. Every other value, and an unset variable, refuse
#: to touch the network: the recorded fixtures are the artifact, and a stray
#: `python -m serpent.testing.testnet` in CI must do nothing.
RECORD_ENV_VAR = "SERPENT_TESTNET_RECORD"

#: An override for the simulation source account, for a caller who would rather
#: name their own. Simulation accepts a never-funded key, so this is never
#: needed to record (E14 as amended).
SOURCE_ENV_VAR = "SERPENT_TESTNET_SOURCE"

#: The default simulation source: a fixed public key that is NEVER FUNDED and
#: must never be. Derived from a publicly published seed phrase, so it holds no
#: secret and none is needed -- `simulateTransaction` does not require the
#: source account to exist (probe-verified, E14 as amended).
DEFAULT_SOURCE = "GAB4AXJZMMWEL2FZOVUP52IVN65YNVAKEFRPTKIACPVVEN2RUPHAALEY"

#: The base fee on an envelope that is only ever simulated. Never paid.
SIMULATION_BASE_FEE = 100

#: The envelope's timeout. Also never used: nothing is submitted.
SIMULATION_TIMEOUT = 300

Durability = Literal["persistent", "temporary", "instance"]

_DURABILITY: dict[stellar_xdr.ContractDataDurability, Durability] = {
    stellar_xdr.ContractDataDurability.PERSISTENT: "persistent",
    stellar_xdr.ContractDataDurability.TEMPORARY: "temporary",
}


@dataclass(frozen=True)
class SeededEntry:
    """One plain contract-data entry the simulation read, as XDR.

    `durability` is the bucket to seed it into; `live_until` is the absolute
    ledger the chain says it lives until, recorded for the record rather than
    replayed (seeding through `RealStorage.set` writes a fresh TTL, which is
    the host's own business and not a fixture's to dictate).
    """

    durability: Durability
    key_xdr: str
    value_xdr: str
    live_until: int | None


@dataclass(frozen=True)
class FixtureResult:
    """What the simulation answered: a returned ScVal, or an error text.

    Exactly one of the two is set. The error arm keeps the RPC's own text
    verbatim, diagnostic event log and all, because that text is the evidence
    -- it is where `Error(Value, UnexpectedType)` and "two non-object args to
    obj_cmp" are written, and paraphrasing it would throw away the only thing
    that identifies WHICH trap this was (B1).
    """

    ok: bool
    value_xdr: str | None = None
    error_text: str | None = None

    def __post_init__(self) -> None:
        if self.ok and (self.value_xdr is None or self.error_text is not None):
            raise ValueError("an ok FixtureResult carries a value_xdr and no error_text")
        if not self.ok and (self.error_text is None or self.value_xdr is not None):
            raise ValueError("an error FixtureResult carries an error_text and no value_xdr")


@dataclass(frozen=True)
class Fixture:
    """One recorded simulation: the header, the state, the call, the answer."""

    contract_id: str
    wasm_sha256: str
    protocol: int
    ledger: int
    rpc_version: str
    recorded_at: str
    method: str
    args_xdr: tuple[str, ...]
    instance: tuple[tuple[str, str], ...]
    seeded: tuple[SeededEntry, ...]
    result: FixtureResult
    events_xdr: tuple[str, ...]


# --- simulating -------------------------------------------------------------


def simulate(
    *,
    server: SorobanServer,
    source_account: str,
    contract_id: str,
    method: str,
    args: Sequence[object],
) -> SimulateTransactionResponse:
    """Simulate `contract_id.method(*args)` against whatever the RPC's ledger
    snapshot currently holds.

    The envelope is built and handed straight to `simulate_transaction`: it is
    never signed and never goes anywhere (E14). `args` are tier-1 chain values,
    encoded through the one marshalling layer (`_scval.encode`) so tier 3 speaks
    the same ScVal conventions as the other two tiers.
    """
    transaction = (
        TransactionBuilder(
            Account(source_account, 0), TESTNET_PASSPHRASE, base_fee=SIMULATION_BASE_FEE
        )
        .append_invoke_contract_function_op(contract_id, method, [encode(arg) for arg in args])
        .set_timeout(SIMULATION_TIMEOUT)
        .build()
    )
    return server.simulate_transaction(transaction)


# --- recording --------------------------------------------------------------


def record_fixture(
    *,
    server: SorobanServer,
    source_account: str,
    contract_id: str,
    method: str,
    args: Sequence[object] = (),
    out: Path,
    wasm_sha256: str | None = None,
) -> Fixture:
    """Simulate once, fetch the state it read, and write `out/<method>.json`.

    `wasm_sha256` is CHECKED, not trusted: the deployed executable's hash is
    read off the contract's own instance entry, so passing a wrong one is a
    `ValueError` here rather than a fixture that claims bytes it was not
    recorded against. Passing `None` records the chain's answer.

    The instance entry is fetched unconditionally rather than only when the
    footprint names it. An ERROR simulation returns no `transaction_data` at
    all -- measured: the deployed `area`'s response has `transaction_data is
    None` -- so a footprint-only recorder would record the B1 trap with no
    state behind it, and the replay would reproduce the trap for the wrong
    reason.
    """
    response = simulate(
        server=server,
        source_account=source_account,
        contract_id=contract_id,
        method=method,
        args=args,
    )
    keys = _footprint_contract_data(response, contract_id)
    instance_key = _instance_ledger_key(contract_id)
    if instance_key.to_xdr() not in {key.to_xdr() for key in keys}:
        keys.append(instance_key)
    instance, seeded, executable_hash = _fetch_state(server, keys)
    if executable_hash is None:
        raise ValueError(f"{contract_id} has no instance entry on this network")
    if wasm_sha256 is not None and wasm_sha256 != executable_hash:
        raise ValueError(
            f"{contract_id} runs wasm {executable_hash}, not the {wasm_sha256} this "
            "recording was asked for"
        )
    fixture = Fixture(
        contract_id=contract_id,
        wasm_sha256=executable_hash,
        protocol=server.get_network().protocol_version,
        ledger=response.latest_ledger,
        rpc_version=server.get_version_info().version,
        recorded_at=datetime.datetime.now(tz=datetime.UTC).isoformat(timespec="seconds"),
        method=method,
        args_xdr=tuple(base64.b64encode(encode(arg).to_xdr_bytes()).decode() for arg in args),
        instance=instance,
        seeded=seeded,
        result=_result_of(response),
        events_xdr=tuple(response.events or ()),
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{method}.json").write_text(_as_json(fixture), encoding="utf-8")
    return fixture


def _result_of(response: SimulateTransactionResponse) -> FixtureResult:
    """The response's answer, either arm.

    A response with an `error` is recorded as one; so is a response with no
    error and no result, which would be an RPC contract violation worth
    recording as an error rather than crashing the recorder.
    """
    if response.error is not None:
        return FixtureResult(ok=False, error_text=response.error)
    if not response.results:
        return FixtureResult(ok=False, error_text="the RPC returned neither a result nor an error")
    return FixtureResult(ok=True, value_xdr=response.results[0].xdr)


def _footprint_contract_data(
    response: SimulateTransactionResponse, contract_id: str
) -> list[stellar_xdr.LedgerKey]:
    """This contract's contract-data footprint keys, read and written alike.

    `CONTRACT_CODE` keys are dropped (the module, not state) and so is any
    contract-data key belonging to some OTHER contract, which a cross-contract
    call would put in the footprint and which this fixture has no business
    seeding.
    """
    if response.transaction_data is None:
        return []
    data = stellar_xdr.SorobanTransactionData.from_xdr(response.transaction_data)
    footprint = data.resources.footprint
    wanted = Address(contract_id).to_xdr_sc_address().to_xdr()
    keys: list[stellar_xdr.LedgerKey] = []
    for key in list(footprint.read_only) + list(footprint.read_write):
        if key.type is not stellar_xdr.LedgerEntryType.CONTRACT_DATA:
            continue
        contract_data = key.contract_data
        if contract_data is None or contract_data.contract.to_xdr() != wanted:
            continue
        keys.append(key)
    return keys


def _instance_ledger_key(contract_id: str) -> stellar_xdr.LedgerKey:
    """The ledger key of a contract's own instance entry.

    Always persistent, and always keyed by the `SCV_LEDGER_KEY_CONTRACT_INSTANCE`
    marker rather than by a value of the contract's choosing.
    """
    return stellar_xdr.LedgerKey(
        stellar_xdr.LedgerEntryType.CONTRACT_DATA,
        contract_data=stellar_xdr.LedgerKeyContractData(
            contract=Address(contract_id).to_xdr_sc_address(),
            key=stellar_xdr.SCVal(stellar_xdr.SCValType.SCV_LEDGER_KEY_CONTRACT_INSTANCE),
            durability=stellar_xdr.ContractDataDurability.PERSISTENT,
        ),
    )


def _fetch_state(
    server: SorobanServer, keys: Sequence[stellar_xdr.LedgerKey]
) -> tuple[tuple[tuple[str, str], ...], tuple[SeededEntry, ...], str | None]:
    """Read `keys` off the ledger; split the instance entry from the rest.

    A key the ledger does NOT hold is simply absent from the answer, and that
    absence is itself part of the recorded state: the shapes contract's
    `is_pinned` footprint names a temporary entry that does not exist, and a
    fixture that invented one would replay a `True` the chain answered `False`.
    So a missing entry is recorded as nothing at all, deliberately.
    """
    if not keys:
        return (), (), None
    response = server.get_ledger_entries(list(keys))
    instance: tuple[tuple[str, str], ...] = ()
    executable_hash: str | None = None
    seeded: list[SeededEntry] = []
    for result in response.entries or ():
        contract_data = stellar_xdr.LedgerEntryData.from_xdr(result.xdr).contract_data
        if contract_data is None:
            continue
        if contract_data.key.type is stellar_xdr.SCValType.SCV_LEDGER_KEY_CONTRACT_INSTANCE:
            instance, executable_hash = _instance_state(contract_data.val)
            continue
        seeded.append(
            SeededEntry(
                durability=_DURABILITY[contract_data.durability],
                key_xdr=contract_data.key.to_xdr(),
                value_xdr=contract_data.val.to_xdr(),
                live_until=result.live_until_ledger,
            )
        )
    seeded.sort(key=lambda entry: (entry.durability, entry.key_xdr))
    return instance, tuple(seeded), executable_hash


def _instance_state(value: stellar_xdr.SCVal) -> tuple[tuple[tuple[str, str], ...], str | None]:
    """A contract instance entry as `(storage pairs, executable wasm sha256)`.

    The storage sub-map is optional in the XDR (a contract with no instance
    state has none), and the executable is a hash only for a wasm contract: a
    Stellar-asset contract has no module to fetch, and answering `None` there
    lets the caller say so.
    """
    instance = value.instance
    if instance is None:
        return (), None
    storage = instance.storage
    pairs = (
        ()
        if storage is None
        else tuple((entry.key.to_xdr(), entry.val.to_xdr()) for entry in storage.sc_map)
    )
    executable = instance.executable
    if executable.type is not stellar_xdr.ContractExecutableType.CONTRACT_EXECUTABLE_WASM:
        return pairs, None
    wasm_hash = executable.wasm_hash
    return pairs, None if wasm_hash is None else wasm_hash.hash.hex()


# --- the fixture files ------------------------------------------------------


def _as_json(fixture: Fixture) -> str:
    """A fixture as the committed JSON: two-space indent, keys in field order,
    a trailing newline. Written by hand rather than by `dataclasses.asdict` so
    the file shape is a stated format and not a refactor away from changing."""
    body: dict[str, Any] = {
        "contract_id": fixture.contract_id,
        "wasm_sha256": fixture.wasm_sha256,
        "protocol": fixture.protocol,
        "ledger": fixture.ledger,
        "rpc_version": fixture.rpc_version,
        "recorded_at": fixture.recorded_at,
        "method": fixture.method,
        "args_xdr": list(fixture.args_xdr),
        "instance": [list(pair) for pair in fixture.instance],
        "seeded": [
            {
                "durability": entry.durability,
                "key_xdr": entry.key_xdr,
                "value_xdr": entry.value_xdr,
                "live_until": entry.live_until,
            }
            for entry in fixture.seeded
        ],
        "result": (
            {"ok": True, "value_xdr": fixture.result.value_xdr}
            if fixture.result.ok
            else {"ok": False, "error_text": fixture.result.error_text}
        ),
        "events_xdr": list(fixture.events_xdr),
    }
    return json.dumps(body, indent=2) + "\n"


def load_fixture(path: Path) -> Fixture:
    """One recorded fixture. Every field is required: a fixture missing one is
    a `KeyError` here, not a default quietly standing in for a measurement."""
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        # TRY004's inverse case: a malformed fixture FILE is bad data, not a
        # caller passing the wrong type of argument.
        raise ValueError(f"{path} is not a recorded fixture")  # noqa: TRY004
    result = body["result"]
    return Fixture(
        contract_id=body["contract_id"],
        wasm_sha256=body["wasm_sha256"],
        protocol=body["protocol"],
        ledger=body["ledger"],
        rpc_version=body["rpc_version"],
        recorded_at=body["recorded_at"],
        method=body["method"],
        args_xdr=tuple(body["args_xdr"]),
        instance=tuple((pair[0], pair[1]) for pair in body["instance"]),
        seeded=tuple(
            SeededEntry(
                durability=entry["durability"],
                key_xdr=entry["key_xdr"],
                value_xdr=entry["value_xdr"],
                live_until=entry["live_until"],
            )
            for entry in body["seeded"]
        ),
        result=(
            FixtureResult(ok=True, value_xdr=result["value_xdr"])
            if result["ok"]
            else FixtureResult(ok=False, error_text=result["error_text"])
        ),
        events_xdr=tuple(body["events_xdr"]),
    )


def fixtures_under(directory: Path) -> list[Fixture]:
    """Every fixture in `directory`, in filename order.

    Only `*.json`: the recorded bytes (`deployed.wasm`) and the README live in
    the same directory on purpose, so the fixtures and the module they were
    recorded against cannot be separated.
    """
    if not directory.is_dir():
        return []
    return [load_fixture(path) for path in sorted(directory.glob("*.json"))]


# --- the recorder's command line --------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m serpent.testing.testnet record --contract C... --out DIR METHOD...`

    Gated on `RECORD_ENV_VAR`: recording is the only thing in the whole test
    tier that touches the network, so it is opt-in by an environment variable
    and not by remembering not to run it. Read-only either way -- there is
    nothing here that could write to the chain even if it ran by accident.
    """
    parser = argparse.ArgumentParser(prog="python -m serpent.testing.testnet")
    subcommands = parser.add_subparsers(dest="command", required=True)
    recorder = subcommands.add_parser("record", help="record simulations as fixtures")
    recorder.add_argument("--contract", required=True, help="the deployed contract's C... strkey")
    recorder.add_argument("--out", required=True, type=Path, help="the fixture directory")
    recorder.add_argument("--rpc", default=TESTNET_RPC, help="the Soroban RPC endpoint")
    recorder.add_argument("--wasm-sha256", default=None, help="the bytes to insist on")
    recorder.add_argument("methods", nargs="+", help="the no-argument methods to record")
    arguments = parser.parse_args(argv)
    if os.environ.get(RECORD_ENV_VAR) != "1":
        print(
            f"{RECORD_ENV_VAR}=1 is required to record: this reaches the network, and the "
            "committed fixtures are the artifact (K7).",
            file=sys.stderr,
        )
        return 2
    source_account = os.environ.get(SOURCE_ENV_VAR) or DEFAULT_SOURCE
    server = SorobanServer(arguments.rpc)
    for method in arguments.methods:
        fixture = record_fixture(
            server=server,
            source_account=source_account,
            contract_id=arguments.contract,
            method=method,
            out=arguments.out,
            wasm_sha256=arguments.wasm_sha256,
        )
        answer = "ok" if fixture.result.ok else "error"
        print(
            f"{method}: {answer} at ledger {fixture.ledger}, "
            f"{len(fixture.instance)} instance pair(s), {len(fixture.seeded)} entry(ies)"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - the recorder's own entry point
    raise SystemExit(main())
