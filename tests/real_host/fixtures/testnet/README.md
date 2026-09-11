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

Two deployed contracts, each with its own recorded corpus under this
directory.

### `shapes`

| | |
| --- | --- |
| Recorded | 2026-09-11 (UTC) |
| Network | testnet, `https://soroban-testnet.stellar.org` |
| Passphrase | `Test SDF Network ; September 2015` |
| Protocol | 28 |
| RPC build | `28.0.1-273f19e4fcb183b568948bd2b810abfe87150a9c` |
| Ledger | 4622863-4622864 (`kind`, `palette`, `is_pinned` at 4622863; `area` at 4622864) |
| Contract | `CD3KZQVZSUIM6YDGZAC2VSXNN7COV7AR7U5J5N725BAMECARV4LENHYY` (`examples/shapes.py`, `Drawing`) |
| Wasm | sha256 `7ba2afb0c81ac3cf05a1dd4edfa48b98bfefab6e51901ad7676a27230f84483e`, 4,281 bytes |
| Methods recorded | `kind`, `palette`, `is_pinned`, `area` |
| Source account | `GAB4AXJZMMWEL2FZOVUP52IVN65YNVAKEFRPTKIACPVVEN2RUPHAALEY` -- fixed, **never funded**, and it must stay that way; simulation does not require the source account to exist |

`shapes/deployed.wasm` is the contract's own bytes, fetched from the chain.
The replay deploys THOSE bytes on the embedded host. This is the M1-end
redeploy (see the historical note below): the chain was seeded with
`draw_rect(--w 5 --h 2)` then `pin`, so `SHAPE` (instance) is
`Shape.Rect(U32(5), U32(2))`, `COLOR` (persistent, default) is `Color.Red`,
and the temporary pin entry for that shape exists -- so `is_pinned` answers
true and `area` answers `U32(10)`.

### `bounty_board`

| | |
| --- | --- |
| Recorded | 2026-09-11 (UTC) |
| Network | testnet, `https://soroban-testnet.stellar.org` |
| Passphrase | `Test SDF Network ; September 2015` |
| Protocol | 28 |
| RPC build | `28.0.1-273f19e4fcb183b568948bd2b810abfe87150a9c` |
| Ledger | 4622864 (both fixtures) |
| Contract | `CBBIB2C6C3ULRHJTTPK7FPDU6RFHAJM2IQ5RHHWVDP4C7GXBZ5VF2FEW` (`examples/bounty_board.py`, `BountyBoard`) |
| Wasm | sha256 `93477b8326f8e9f804355117f4b789d4bd806a5206f05493b7368557c86cfdfa`, 6,493 bytes |
| Methods recorded | `total_posted`, `open_ids` |
| Constructor | `admin = GCKJRYNE624USL4G4ICA2KQ4KF5ZHYOLTWWFLPIOAH45PP7AFTP4UKHB` (the deploying identity, `pyserpent`) |
| Source account | `GAB4AXJZMMWEL2FZOVUP52IVN65YNVAKEFRPTKIACPVVEN2RUPHAALEY` -- fixed, **never funded** |

`bounty_board/deployed.wasm` is the contract's own bytes, fetched from the
chain. The chain was seeded with one `post(poster=GCKJ...UKHB, reward=50,
priority=High)`, so `total_posted` answers `U32(1)` and `open_ids` answers
`[1]`.

## The exact commands

```bash
stellar contract fetch \
  --id CD3KZQVZSUIM6YDGZAC2VSXNN7COV7AR7U5J5N725BAMECARV4LENHYY \
  --network testnet \
  --out-file tests/real_host/fixtures/testnet/shapes/deployed.wasm

SERPENT_TESTNET_RECORD=1 uv run --no-sync python -m serpent.testing.testnet record \
  --contract CD3KZQVZSUIM6YDGZAC2VSXNN7COV7AR7U5J5N725BAMECARV4LENHYY \
  --out tests/real_host/fixtures/testnet/shapes \
  --wasm-sha256 7ba2afb0c81ac3cf05a1dd4edfa48b98bfefab6e51901ad7676a27230f84483e \
  kind palette is_pinned area

stellar contract fetch \
  --id CBBIB2C6C3ULRHJTTPK7FPDU6RFHAJM2IQ5RHHWVDP4C7GXBZ5VF2FEW \
  --network testnet \
  --out-file tests/real_host/fixtures/testnet/bounty_board/deployed.wasm

SERPENT_TESTNET_RECORD=1 uv run --no-sync python -m serpent.testing.testnet record \
  --contract CBBIB2C6C3ULRHJTTPK7FPDU6RFHAJM2IQ5RHHWVDP4C7GXBZ5VF2FEW \
  --out tests/real_host/fixtures/testnet/bounty_board \
  --wasm-sha256 93477b8326f8e9f804355117f4b789d4bd806a5206f05493b7368557c86cfdfa \
  total_posted open_ids
```

`SERPENT_TESTNET_RECORD=1` is required, so the recorder cannot run by accident;
`SERPENT_TESTNET_SOURCE` overrides the source account and is never needed.

## `area.json` was once an ERROR fixture (B1, retired 2026-09-11)

Between 2026-09-02 and the M1-end redeploy, the deployed shapes bytes lowered
`shape.tag() == Symbol("Rect")` to an `obj_cmp` on two SMALL symbols, which the
host refused:

```
HostError: Error(Value, UnexpectedType)
  ["two non-object args to obj_cmp", Rect, Rect]
```

So `area` used to trap on chain, and trapped identically on the embedded host
running those same bytes -- `RealHostError` with `.underlying == ("Value",
"UnexpectedType")`. Tier 1 ran HEAD's model, where Task 0 had already fixed
the lowering, and answered `U32(10)`. That three-way divergence was DECLARED
in the test's `SHAPES.divergences` table with this reason. The M1-end
redeploy of 2026-09-11 rebuilt and redeployed `examples/shapes.py` with the
fix included, so the deployed bytes now agree with tier 1: `area` answers
`U32(10)` on all three legs, and `SHAPES.divergences` is empty.

## Re-recording

The header test fails loudly if a fixture drifts from the contract, the
committed bytes or the protocol, which is the signal to re-record. Re-run the
matching pair of commands above for the set that drifted, check the new
`deployed.wasm` sha256 against the chain's own instance executable hash (the
recorder does this for you and refuses a mismatch), and update that set's
`deployed_sha256` in the test plus the table above.
