"""Phase 0's spike-1 contract, re-authored in serpent's authoring surface.

This is F.2.9's fixture: `spikes/spike1/contract_src.py` says the same thing
in the throwaway spike's `serpent_stub` style, and `spikes/` is FROZEN (R5) --
so the contract is re-AUTHORED here rather than imported, edited, or copied
wholesale. The spike file is not touched by this task at all.

**Why it exists.** The spike's 877-byte artifact is the only end-to-end anchor
available to the frontend before sub-plan D exists: it was built, validated,
deployed to testnet, fetched back byte-identical, and exercised on chain
(`spikes/spike1/ACCEPTANCE.md` rows 1-9). Its import set -- eight host
functions -- is therefore a recorded fact about a module that really ran. If
the frontend's `host_fns_used` for the same contract matches that set, the
frontend's host-function accounting is anchored to something real instead of
only to itself. `tests/unit/test_frontend_goldens.py` is where that assertion
lives, with the eight names copied in from the spike's own recorded files.

**The authoring deltas from the spike's spelling**, each forced by serpent's
surface rather than chosen:

* Methods take `self` first (the amended spec-Sec.2 style, A23) -- the spike
  predates that amendment and wrote `def setup(env: Env, ...)`.
* Error-enum members are `errorcode(N)`, not a bare `int` (A20/S10); a bare
  `LimitExceeded = 7` is a compile reject
  (`tests/must_reject/shape/error_member_bare_int.py`).
* `@contract`/`@contracttype`/`@contracterror` and the chain types come from
  the `serpent` package root, not from `spikes/spike1/serpent_stub.py`.

Nothing else changes: the same two methods with the same names and signatures,
the same 13-character `counter_limit` field (which is what forces a runtime
`symbol_new_from_linear_memory` -- a SymbolSmall cannot hold 13 characters),
the same `display_name: String` (which forces the string literal and the data
section), the same `SETTINGS`/`COUNT` storage keys, and the same
`LimitExceeded = 7` raise above the limit.

Not collected by pytest directly (no `test_*` functions); read as text and
compiled by `tests/unit/test_frontend_goldens.py`, and imported by nothing --
but still under the tests-wide `uv run mypy --strict` gate, like
`token_style.py`, which is what keeps the re-authoring honest Python.
"""

from serpent import (
    U32,
    Env,
    String,
    Symbol,
    contract,
    contracterror,
    contracttype,
    errorcode,
)


@contracterror
class Error:
    LimitExceeded = errorcode(7)


@contracttype
class Settings:
    counter_limit: U32  # 13 chars -> forces SymbolObject via linear memory
    display_name: String  # forces a string literal + data section


@contract
class Spike:
    def setup(self, env: Env, counter_limit: U32) -> None:
        """Store settings with a long-named field and a string literal."""
        settings = Settings(
            counter_limit=counter_limit,
            display_name=String("serpent phase zero"),
        )
        env.storage().instance().set(Symbol("SETTINGS"), settings)

    def bump(self, env: Env) -> U32:
        """Increment a persistent counter; raise LimitExceeded above the limit."""
        settings = env.storage().instance().get(Symbol("SETTINGS"), Settings)
        count = env.storage().persistent().get(Symbol("COUNT"), U32, default=U32(0))
        count = count + U32(1)
        if count > settings.counter_limit:
            raise Error.LimitExceeded
        env.storage().persistent().set(Symbol("COUNT"), count)
        return count
