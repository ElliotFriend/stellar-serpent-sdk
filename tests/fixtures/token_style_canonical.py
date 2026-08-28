"""The CANONICAL event spelling, kept under test in its own contract (§E2(v)).

`tests/fixtures/token_style.py` publishes through the authoring form
(`Transfer(from_=..., to=..., amount=...).publish(env)`) since M1-E Task 6.
Ruling D5 called `env.events().publish(topics, data)` "the supported form", not
the only one, and both spellings are supported -- so this fixture exists to keep
the canonical one covered end to end:

* `contract_event` reached from a hand-written, HETEROGENEOUS topic tuple
  (`Symbol`, `Address`, `Address`) -- D4's canonical Soroban shape. Before this
  file, that path had exactly one call site in the whole tree, and the
  `token_style.py` revert would have left it with none.
* the equivalence claim's second half: `tests/unit/test_frontend_events.py`
  asserts the desugared `HostCall` tree equals the canonical one's, and
  `tests/unit/test_emitter_end_to_end.py` runs BOTH contracts under the mini
  host and asserts the recorded events agree, topic word for topic word.

Deliberately minimal -- one storage write and one publish. It is not a second
token: `token_style.py` is the realistic shape (F.2.7), and duplicating it here
would double the maintenance for no extra coverage.

Not collected by pytest directly (no `test_*` functions).
"""

from serpent import U32, Address, Env, Symbol, contract


@contract
class TokenStyleCanonical:
    """One method, publishing the canonical way."""

    def __init__(self, env: Env, admin: Address) -> None:
        env.storage().instance().set(Symbol("ADMIN"), admin)

    def send(self, env: Env, frm: Address, to: Address, amount: U32) -> None:
        frm.require_auth()
        env.storage().persistent().set(to, amount)
        # The heterogeneous topic tuple, written out: (Symbol, Address, Address).
        env.events().publish((Symbol("transfer"), frm, to), amount)
