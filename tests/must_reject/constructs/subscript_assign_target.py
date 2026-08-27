# serpent:reject SPT1030
# serpent:at HERE
# serpent:message use Vec.put(i, v) or Map.set(k, v)
# serpent:doc-title subscript assignment target
from serpent import Env, U32, Vec, contract


@contract
class Contract:
    def compute(self, env: Env, v: Vec[U32], x: U32) -> U32:
        v[0] = x  # HERE
        return x
