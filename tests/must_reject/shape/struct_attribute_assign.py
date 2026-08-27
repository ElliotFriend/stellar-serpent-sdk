# serpent:reject SPT4016
# serpent:at HERE
# serpent:message @contracttype values are immutable; build a new one instead
# serpent:doc-title attribute assignment on a @contracttype value
from serpent import Env, U32, contract, contracttype


@contracttype
class Point:
    x: U32


@contract
class Contract:
    def compute(self, env: Env, p: Point) -> U32:
        p.x = U32(1)  # HERE
        return p.x
