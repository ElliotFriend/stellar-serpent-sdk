# serpent:reject SPT1002
# serpent:at HERE
# serpent:message there is no event loop on chain
# serpent:doc-title async def
from serpent import Env, U32, contract


@contract
class Contract:
    async def compute(self, env: Env, x: U32) -> U32:  # HERE
        return x
