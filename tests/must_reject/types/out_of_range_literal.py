# serpent:reject SPT3004
# serpent:at HERE
# serpent:message literal is out of range for the target type
# serpent:doc-title out-of-range literal coercion
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env) -> U32:
        return U32(2**32)  # HERE
