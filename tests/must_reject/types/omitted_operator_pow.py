# serpent:reject SPT3005
# serpent:at HERE
# serpent:message this operator is not supported
# serpent:doc-title omitted operator (**)
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, a: U32, b: U32) -> U32:
        return a ** b  # HERE
