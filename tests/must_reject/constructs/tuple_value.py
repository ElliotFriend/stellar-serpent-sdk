# serpent:reject SPT1014
# serpent:at HERE
# serpent:message tuple structs are not supported
# serpent:doc-title tuple value outside event topics
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, a: U32, b: U32) -> U32:
        pair = (a, b)  # HERE
        return a
