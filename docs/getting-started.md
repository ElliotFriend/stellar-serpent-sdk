# Getting started

## Install the plugin

serpent is not on PyPI yet. Install the console script straight from the
repository, with the `spec` extra that `build` and `inspect` need:

```sh
uv tool install "serpent[spec] @ git+https://github.com/ElliotFriend/stellar-serpent-sdk"
stellar-serpent doctor
```

`doctor` reports one row per tool. The Stellar CLI finds the plugin as
`stellar-serpent` on PATH, so once `doctor`'s "plugin on PATH" row is `ok`,
every command below also works as `stellar serpent ...`.

Optional but recommended: `wasm-tools` (the version `doctor` names) gives
every build a second, independent validation.

## Write a contract

```python
--8<-- "examples/counter.py"
```

Every method takes `self` then `env: Env`; every parameter and return is a
chain type; errors are `@contracterror` members raised as exceptions; state
lives in `env.storage()`. What is and is not allowed is spelled out, with a
counter-example per rule, in [The subset](subset.md).

## Run it without building

```python
from serpent import U32
from serpent.env import Env, deploy
from counter import Counter

env = Env()
counter = deploy(Counter, env)
with env.frame():
    assert counter.increment(env, U32(5)) == U32(5)
```

That is tier 1: a model, fast, and not the chain. See [Testing](testing.md)
for the real host.

## Build

```sh
stellar serpent build counter.py
```

writes `counter.wasm` beside the source and prints its size, its sha256 (the
on-chain wasm hash), and the protocol it declares. `stellar serpent inspect
counter.wasm` reads it back.

## Deploy and invoke with the stock CLI

serpent rebuilds nothing the Stellar CLI already does:

```sh
stellar contract deploy --wasm counter.wasm --source alice --network testnet
stellar contract invoke --id C... --source alice --network testnet -- increment --step 5
stellar contract info interface --id C... --network testnet
```

A contract with an `__init__` takes its constructor arguments after the `--`
on `deploy`, e.g. `-- --admin G...`.
