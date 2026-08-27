# serpent:reject SPT1027
# serpent:at HERE
# serpent:message contract state lives in storage
# serpent:doc-title global statement
from serpent import Env, U32, contract

COUNTER = U32(0)


@contract
class Contract:
    def bump(self, env: Env) -> U32:
        global COUNTER  # HERE
        COUNTER = COUNTER + U32(1)
        return COUNTER
