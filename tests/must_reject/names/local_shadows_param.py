# serpent:reject SPT2004
# serpent:at HERE
# serpent:message name shadows an existing declaration
# serpent:doc-title local shadows a parameter
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        x = U32(5)  # HERE
        return x
