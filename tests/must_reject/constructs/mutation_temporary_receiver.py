# serpent:reject SPT1034
# serpent:at HERE
# serpent:message host container operations are functional; mutate only a local this method owns, on a statement of its own -- `v.push_back(x)` -- and C rebinds it (v = vec_push_back(v, x))
# serpent:doc-title container mutation on a temporary receiver
from serpent import Env, U32, Vec, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        Vec(U32, [x]).push_back(x)  # HERE
        return x
