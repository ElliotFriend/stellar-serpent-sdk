# serpent:reject SPT3019
# serpent:at HERE
# serpent:message the first event topic must be a Symbol naming the event
# serpent:doc-title event topics[0] not a Symbol
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        env.events().publish((x,), x)  # HERE
        return x
