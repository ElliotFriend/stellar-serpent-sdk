# serpent:reject SPT1037
# serpent:at HERE
# serpent:message this construct is not supported by the serpent subset
# serpent:doc-title non-literal module-level constant
from serpent import Env, U32, contract

LIMIT = U32(1) + U32(2)  # HERE


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
