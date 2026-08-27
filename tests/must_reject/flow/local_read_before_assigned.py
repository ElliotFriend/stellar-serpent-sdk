# serpent:reject SPT7002
# serpent:at HERE
# serpent:message local may be used before it is assigned
# serpent:doc-title local read before it is definitely assigned
from serpent import Bool, Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, flag: Bool) -> U32:
        if flag:
            x = U32(1)
        return x  # HERE
