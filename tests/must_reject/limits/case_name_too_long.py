# serpent:reject SPT5003
# serpent:at HERE
# serpent:message error case name is too long (> 60)
# serpent:doc-title over-long error-enum case name
from serpent import Env, U32, contract, contracterror, errorcode


@contracterror
class Err:
    CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC = errorcode(1)  # HERE


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
