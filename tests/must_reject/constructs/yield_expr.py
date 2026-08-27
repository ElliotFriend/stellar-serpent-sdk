# serpent:reject SPT1008
# serpent:at HERE
# serpent:message generators are not supported
# serpent:doc-title yield expression
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        y = yield x  # HERE
        return y
