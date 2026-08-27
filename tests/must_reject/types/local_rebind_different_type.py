# serpent:reject SPT3017
# serpent:at HERE
# serpent:message a local's type is fixed by its first binding
# serpent:doc-title local rebound at a different type
from serpent import Bool, Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env) -> U32:
        x = U32(1)
        x = Bool(True)  # HERE
        return U32(0)
