# Sandbox

A scratch area for playing with serpent. Nothing in here is shipped, tested,
or reviewed -- expand, break, and rewrite freely. `counter.py` and
`hello_world.py` are read by one test (`tests/unit/test_harness_hostfns.py`)
as small whole-contract fixtures, so leave those two as they are and add new
files beside them.

## Quick start

```sh
stellar serpent build sandbox/counter.py      # writes sandbox/counter.wasm
stellar serpent inspect sandbox/counter.wasm
stellar serpent doctor
```

(`uv run --no-sync python sandbox/compile.py sandbox/counter.py` still
works; it just calls the CLI.) The diagnostics are the interesting part:
break `counter.py` on purpose and rebuild -- each rejection carries an
`SPT####` code, a location, and a rewrite that compiles. The full catalog is
`docs/subset.md`.

## Run it without building

```python
from serpent import U32
from serpent.env import Env, deploy
```

A contract's methods run as plain Python against a tier-1 `Env`; the real host
is one import away (`serpent.testing.RealEnv`, `docs/testing.md`). The worked
contracts are `examples/` -- eight of them, each compiled, run at tier 1, under
the mini host, and on the real host by the test suite; `examples/bounty_board.py`
is the one that touches every M1 surface, and it and `examples/guestbook.py`
both started life in this directory.

## Notes

- Compiling a module executes its top level (the documented build-time trust
  boundary) -- fine for your own code; don't point it at untrusted files.
- This directory is outside the mypy/pytest gates (`ruff check` does lint it).
