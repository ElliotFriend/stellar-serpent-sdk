# serpent:reject SPT4001
# serpent:at HERE
# serpent:message contract methods must take self first
# serpent:doc-title private method missing `self` as its first parameter
from serpent import Env, U32, contract


@contract
class Contract:
    def _helper(env: Env, x: U32) -> U32:  # HERE
        return x

    def compute(self, env: Env, x: U32) -> U32:
        return self._helper(env, x)
