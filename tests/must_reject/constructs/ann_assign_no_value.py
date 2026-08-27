# serpent:reject SPT1036
# serpent:at HERE
# serpent:message uninitialized locals are not supported; give x: T a value
# serpent:doc-title annotated local with no value
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        y: U32  # HERE
        return x
