"""The POSITIVE half of Task 7's `get` overload set (fed item X4, ruling E12).

`env.py`'s storage `get(key, ty, default=...)` accepts a raw literal default
(`default=0`) at runtime -- M1-C's literal adoption through `ty`, made tier-1
in M1-E -- but a single signature types the return as `object` for that
spelling, because it solves `_T` against both `ty` and `default` and joins
them. Four `@overload`s fix that. This fixture spells every ACCEPT case, kept
under the tests-wide `uv run mypy --strict` run so its cleanliness is asserted
by configuration, with no `# type: ignore` anywhere in it -- the same B5 split
`tests/fixtures/udt_style.py` uses: the NEGATIVE half (the two spellings that
must still be errors) lives in `tests/unit/test_authoring_types.py`, written
to `tmp_path` and checked in a subprocess, because a tracked file cannot be
both clean and wrong.

**The five accept spellings, one method each:**
* `default=0` -- a raw literal, adopted through `ty` (arm 1);
* `default=U32(0)` -- an already-chain-value default, passed through as-is
  (ruling E5, arm 2);
* a positional default (arm 3, the one ruling E12 added so today's
  `get(key, ty, default)` does not lose an accept);
* no `default` at all (arm 4, the bare `MissingValue` path);
* the keyword `key=`/`ty=` spelling, still arm 4 -- arm 3's `default` is
  positional-only (the trailing `/`), so a keyword call can only reach the
  last arm.

Joins `tests/unit/test_frontend_fuzz.py`'s `fixtures/` corpus, which is why it
must COMPILE (it is fuzz-mangled like every other fixture there): a plain
contract over one persistent `U32` slot.
"""

from serpent import U32, Env, Symbol, contract

TOTAL = Symbol("total")


@contract
class GetDefaultTyping:
    """One method per ACCEPT spelling of storage `get`'s default."""

    def read_raw_literal_default(self, env: Env) -> U32:
        """`default=0`: arm 1, the raw literal adopted through `ty`."""
        return env.storage().persistent().get(TOTAL, U32, default=0)

    def read_chain_value_default(self, env: Env) -> U32:
        """`default=U32(0)`: arm 2, ruling E5's pass-through."""
        return env.storage().persistent().get(TOTAL, U32, default=U32(0))

    def read_positional_default(self, env: Env) -> U32:
        """A positional default: arm 3."""
        return env.storage().persistent().get(TOTAL, U32, U32(0))

    def read_no_default(self, env: Env) -> U32:
        """No `default` at all: arm 4, the bare `MissingValue` path."""
        return env.storage().persistent().get(TOTAL, U32)

    def read_keyword_key_and_ty(self, env: Env) -> U32:
        """The `key=`/`ty=` keyword spelling -- still arm 4."""
        return env.storage().persistent().get(key=TOTAL, ty=U32)
