# serpent:reject SPT4002
# serpent:at HERE
# serpent:message contract functions have a fixed, positional arity; *args/**kwargs and keyword-only parameters are not supported
# serpent:doc-title module-level helper with *args
from serpent import Env, U32, contract


def _helper(env: Env, *rest: U32) -> U32:  # HERE
    return U32(0)


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
