"""The twin of `future_annotations_contract`, WITHOUT the future import.

Deliberately identical below the import line. See that module's docstring.
"""

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
