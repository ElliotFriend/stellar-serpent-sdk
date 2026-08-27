# serpent:reject SPT4005
# serpent:at HERE
# serpent:message a contract function needs a return annotation
# serpent:doc-title private method missing a return annotation
from serpent import Env, U32, contract


@contract
class Contract:
    def _helper(self, env: Env, x: U32):  # HERE
        return x

    def compute(self, env: Env, x: U32) -> U32:
        return self._helper(env, x)
