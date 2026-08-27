# serpent:reject SPT1025
# serpent:at HERE
# serpent:message assert has no on-chain meaning
# serpent:doc-title assert statement
from serpent import Env, U32, contract


@contract
class Contract:
    def check(self, env: Env, x: U32) -> U32:
        assert x != U32(0)  # HERE
        return x
