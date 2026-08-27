# serpent:reject SPT1006
# serpent:at HERE
# serpent:message assign on its own line
# serpent:doc-title walrus operator
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        if (y := x + U32(1)) > U32(0):  # HERE
            return y
        return U32(0)
