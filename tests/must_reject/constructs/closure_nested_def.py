# serpent:reject SPT1001
# serpent:at HERE
# serpent:message nested functions and closures are not supported
# serpent:doc-title nested function definition
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        def helper(y: U32) -> U32:  # HERE
            return y + U32(1)

        return helper(x)
