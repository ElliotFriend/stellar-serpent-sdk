# serpent:reject SPT2003
# serpent:at HERE
# serpent:message annotation refers to a name that is not defined
# serpent:doc-title annotation naming an undefined name
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: Frobnicate) -> U32:  # HERE
        return x
