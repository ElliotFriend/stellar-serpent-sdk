# serpent:reject SPT4004
# serpent:at HERE
# serpent:message every parameter needs a chain-type annotation
# serpent:doc-title missing parameter annotation
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, amount) -> U32:  # HERE
        return amount
