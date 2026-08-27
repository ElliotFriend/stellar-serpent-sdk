# serpent:reject SPT2002
# serpent:at HERE
# serpent:message contract state lives in storage, not on self
# serpent:doc-title `self` used as a value
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        y = self  # HERE
        return x
