"""The same contract as `no_future_annotations_contract`, under PEP 563.

`from __future__ import annotations` makes every annotation in this module a
*string* at runtime. The decorators resolve annotations through
`typing.get_type_hints`, so the `_serpent_type_` metadata recorded here must be
byte-for-byte identical to the metadata recorded for the twin module that does
not use the future import. `test_decorators.py` asserts exactly that.

Not named `test_*`, so pytest does not collect it; it is imported by the test.
"""

from __future__ import annotations

from serpent.decorators import contract, contractevent, contracttype
from serpent.env import Env, Event
from serpent.types import U32, Address, String


@contracttype
class Settings:
    counter_limit: U32
    display_name: String


@contractevent
class Credited(Event):
    owner: Address
    amount: U32


@contract
class Contract:
    def __init__(self, env: Env, admin: Address) -> None: ...

    def bump(self, env: Env, by: U32) -> U32:
        return by

    def configure(self, env: Env, settings: Settings) -> None: ...
