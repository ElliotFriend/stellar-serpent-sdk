# serpent:reject SPT3020
# serpent:at HERE
# serpent:message call has the wrong arguments (missing, extra, or duplicate keyword)
# serpent:doc-title chain-type constructor called with the wrong arity
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env) -> U32:
        return U32(1, 2)  # HERE
