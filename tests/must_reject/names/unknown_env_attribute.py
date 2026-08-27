# serpent:reject SPT2006
# serpent:at HERE
# serpent:message unknown Env attribute
# serpent:doc-title unknown attribute on Env
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        env.frobnicate()  # HERE
        return x
