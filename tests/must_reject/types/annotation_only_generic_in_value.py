# serpent:reject SPT3014
# serpent:at HERE
# serpent:message this is an annotation-only form; it cannot appear as a value
# serpent:doc-title annotation-only generic form used as a value
from serpent import Env, U32, Vec, contract


@contract
class Contract:
    def compute(self, env: Env) -> U32:
        x = Vec[U32]  # HERE
        return U32(0)
