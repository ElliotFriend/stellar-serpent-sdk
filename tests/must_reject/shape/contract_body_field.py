# serpent:reject SPT4020
# serpent:at HERE
# serpent:message this member is not valid in this kind of serpent-decorated class body
# serpent:doc-title field declared in a @contract class body
from serpent import Env, U32, contract


@contract
class Contract:
    total: U32  # HERE

    def compute(self, env: Env, x: U32) -> U32:
        return x
