# serpent:reject SPT2001
# serpent:at HERE
# serpent:message name is not defined
# serpent:doc-title unresolved name
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return undefined_name  # HERE
