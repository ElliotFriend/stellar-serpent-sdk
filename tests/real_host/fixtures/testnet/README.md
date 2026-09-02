# Tier-3 fixtures: recorded testnet simulations

Everything under this directory was recorded ONCE against the live network and
is replayed offline by `tests/real_host/test_testnet_fixtures.py`. Tier 3 is
fixture-only until a deployment is approved (U3), so no test in the suite
reaches the network, and nothing here was signed or submitted (rulings K7, E14,
D1): `simulateTransaction` is a read-only RPC, and
`src/serpent/testing/testnet.py` has no key, no signature and no submission
path -- asserted by AST in
`test_the_testnet_module_has_no_signing_or_submission_path`.

## What was recorded

| | |
| --- | --- |
| Recorded | 2026-09-02 (UTC) |
| Network | testnet, `https://soroban-testnet.stellar.org` |
| Passphrase | `Test SDF Network ; September 2015` |
| Protocol | 28 |
| RPC build | `28.0.1-273f19e4fcb183b568948bd2b810abfe87150a9c` |
| Ledger | 4473306 (all four fixtures) |
| Contract | `CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW` (`examples/shapes.py`, `Drawing`) |
| Wasm | sha256 `6a9dd13549bac20f2609ab3d74668963b5249a7943dc7f027cdf6c42bec86e33`, 4,171 bytes |
| Source account | `GAB4AXJZMMWEL2FZOVUP52IVN65YNVAKEFRPTKIACPVVEN2RUPHAALEY` -- fixed, **never funded**, and it must stay that way; simulation does not require the source account to exist |

`shapes/deployed.wasm` is the contract's own bytes, fetched from the chain. The
replay deploys THOSE bytes on the embedded host rather than building
`examples/shapes.py`, because Task 0's B1 fix changed what this tree compiles
to: HEAD's build is sha256
`01e4cdfb6bc3feed5000a8f784cb5c7aafcd1d22cf9dcb8917373dd464f01de7` (4,233
bytes) and is deliberately NOT the deployed module.

The chain's state at that ledger: `SHAPE` (instance) is
`Shape.Rect(U32(5), U32(2))`, `COLOR` (persistent) is `Color.Blue`, and the
temporary pin entry for that shape does not exist -- so `is_pinned` answers
false, and the fixture records the absence rather than inventing an entry.

## The exact commands

```bash
stellar contract fetch \
  --id CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW \
  --network testnet \
  --out-file tests/real_host/fixtures/testnet/shapes/deployed.wasm

SERPENT_TESTNET_RECORD=1 uv run --no-sync python -m serpent.testing.testnet record \
  --contract CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW \
  --out tests/real_host/fixtures/testnet/shapes \
  --wasm-sha256 6a9dd13549bac20f2609ab3d74668963b5249a7943dc7f027cdf6c42bec86e33 \
  kind area palette is_pinned
```

`SERPENT_TESTNET_RECORD=1` is required, so the recorder cannot run by accident;
`SERPENT_TESTNET_SOURCE` overrides the source account and is never needed.

## `area.json` is an ERROR fixture, and that is the point (B1)

The deployed bytes lower `shape.tag() == Symbol("Rect")` to an `obj_cmp` on two
SMALL symbols, which the host refuses:

```
HostError: Error(Value, UnexpectedType)
  ["two non-object args to obj_cmp", Rect, Rect]
```

So `area` traps on chain, and it traps identically on the embedded host running
those same bytes -- `RealHostError` with `.underlying == ("Value",
"UnexpectedType")`. Tier 1 runs HEAD's model, where Task 0 fixed the lowering,
and answers `U32(10)`. That three-way divergence is DECLARED in the test's
`B1_DIVERGENCE` table with this reason. It retires at the next approved
deployment (G): re-record these fixtures against the new bytes, empty the
table, and flip
`test_this_trees_shapes_build_differs_from_the_deployed_bytes_until_the_next_deploy`.

## Re-recording

The header test fails loudly if a fixture drifts from the contract, the
committed bytes or the protocol, which is the signal to re-record. Re-run both
commands above, check the new `deployed.wasm` sha256 against the chain's own
instance executable hash (the recorder does this for you and refuses a
mismatch), and update `DEPLOYED_SHA256` in the test plus the table above.
