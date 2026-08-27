# serpent:reject SPT3013
# serpent:at HERE
# serpent:message this annotation cannot be expressed in the contract spec
# serpent:doc-title unmappable annotation (plain int)
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: int) -> U32:  # HERE
        return U32(0)
