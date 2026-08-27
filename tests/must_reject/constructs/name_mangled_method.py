# serpent:reject SPT1037
# serpent:at HERE
# serpent:message this construct is not supported by the serpent subset
# serpent:doc-title name-mangled method
from serpent import Env, U32, contract


@contract
class Contract:
    def __helper(self, env: Env, x: U32) -> U32:  # HERE
        return x

    def compute(self, env: Env, x: U32) -> U32:
        return x
