# serpent:reject SPT1026
# serpent:at HERE
# serpent:message use storage.del_(key), Vec.del_(i), or Map.del_(k)
# serpent:doc-title del statement
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        y = x
        del y  # HERE
        return x
