# serpent:reject SPT1023
# serpent:at HERE
# serpent:message there is no context-manager protocol on chain
# serpent:doc-title with statement
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        with x as guard:  # HERE
            return guard
