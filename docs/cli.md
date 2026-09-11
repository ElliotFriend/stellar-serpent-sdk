# CLI reference

```sh
uv tool install "serpent[spec] @ git+https://github.com/ElliotFriend/stellar-serpent-sdk"
```

installs `stellar-serpent`, which the Stellar CLI discovers on PATH as the
`serpent` plugin (`stellar serpent <command>` runs `stellar-serpent
<command>`), so every command below also works spelled either way.

## Exit codes (ruling E4)

| Code | Name | Meaning |
|---|---|---|
| `0` | `EXIT_OK` | success |
| `1` | `EXIT_REJECTED` | the contract does not compile (the rendered diagnostics ARE the output) or the artifact is malformed |
| `2` | `EXIT_USAGE` | argparse's own usage error |
| `3` | `EXIT_ENVIRONMENT` | a missing extra or file, an unwritable output, a reserved meta key, `wasm-tools` required but absent, or a `fail` row from `doctor` |

## `stellar-serpent`

```text
--8<-- "tests/goldens/cli/root.help.txt"
```

## `stellar-serpent build`

```text
--8<-- "tests/goldens/cli/build.help.txt"
```

## `stellar-serpent inspect`

```text
--8<-- "tests/goldens/cli/inspect.help.txt"
```

## `stellar-serpent doctor`

```text
--8<-- "tests/goldens/cli/doctor.help.txt"
```

## What `inspect` adds over `stellar contract info`

The stock CLI renders the `contractspecv0` interface; `inspect` reads facts
the interface does not carry:

- the **declared** protocol (the module's own `contractenvmetav0`, which the
  stock CLI reads with `stellar contract info env-meta`) against the **floor
  recomputed** from the host functions the module actually imports. A
  declaration **below** that floor is a `MISMATCH`: the module claims a
  protocol its own imports contradict, so it could deploy and never run. A
  declaration **above** the floor is only a note, because that is exactly what
  `build --target-protocol N` produces;
- every host-function **import with its protocol gate**, not just the
  interface's functions;
- the artifact's **sha256**, the same bytes `stellar contract deploy` would
  hash on-chain.

## What `doctor` checks

One row per tool, each `ok`, `warn`, `fail`, or `info`, with a remedy where a
row is not `ok`, in this order: `python`, `serpent` (this package's own
version), the `spec` extra (`stellar_sdk` importable), `wasm-tools`, the
Stellar CLI, the plugin on PATH, `serpent_host` (the embedded real host
extension), the pinned `env.json` tag, the default target protocol
(`build`'s protocol when `--target-protocol` is not given), and, only with
`--network`, one live RPC call for that network's protocol.
