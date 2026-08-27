# serpent:reject SPT1017
# serpent:at HERE
# serpent:message this builtin is not supported
# serpent:doc-title rejected python builtin (str)
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        str(x)  # HERE
        return x
