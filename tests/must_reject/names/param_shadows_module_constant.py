# serpent:reject SPT2004
# serpent:at HERE
# serpent:message name shadows an existing declaration
# serpent:doc-title parameter shadows a module constant
from serpent import Env, U32, Symbol, contract

LIMIT = U32(10)


@contract
class Contract:
    def compute(self, env: Env, LIMIT: U32) -> U32:  # HERE
        return LIMIT
