# serpent:reject SPT1033
# serpent:at HERE
# serpent:message this Env surface is recognized but not yet supported; it lands in M2
# serpent:doc-title recognized-but-deferred Env surface (env.logs)
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        env.logs()  # HERE
        return x
