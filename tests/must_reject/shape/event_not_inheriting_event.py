# serpent:reject SPT4014
# serpent:at HERE
# serpent:message event classes must inherit from serpent.Event
# serpent:doc-title @contractevent class not inheriting Event
from serpent import Env, U32, contract, contractevent


@contractevent
class Transfer:  # HERE
    amount: U32


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
