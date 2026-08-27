# serpent:reject SPT4003
# serpent:at HERE
# serpent:message default parameter values are not supported on a contract function
# serpent:doc-title module-level helper with a default parameter value
from serpent import Env, U32, contract


def _helper(env: Env, x: U32 = U32(0)) -> U32:  # HERE
    return x


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
