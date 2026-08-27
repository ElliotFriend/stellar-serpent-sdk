# serpent:reject SPT1018
# serpent:at HERE
# serpent:message the for ... else clause is not supported
# serpent:doc-title for ... else clause
from serpent import Env, U32, Vec, contract


@contract
class Contract:
    def compute(self, env: Env, v: Vec[U32]) -> U32:
        total = U32(0)
        for x in v:  # HERE
            total = x
        else:
            total = U32(0)
        return total
