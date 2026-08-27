# serpent:reject SPT7004
# serpent:at HERE
# serpent:message unreachable code after a return or raise
# serpent:doc-title statement unreachable after a terminal return
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
        return x  # HERE
