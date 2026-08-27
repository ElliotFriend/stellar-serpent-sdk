# serpent:reject SPT1020
# serpent:at HERE
# serpent:message range() supports only range(stop) and range(start, stop) in M1
# serpent:doc-title range() with a step argument
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env) -> U32:
        total = U32(0)
        for i in range(0, 10, 2):  # HERE
            total = U32(i)
        return total
