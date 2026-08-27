# serpent:reject SPT4009
# serpent:at HERE
# serpent:message error code is out of the allowed range
# serpent:doc-title @contracterror code out of range
from serpent import Env, U32, contract, contracterror, errorcode


@contracterror
class Err:
    TooBig = errorcode(0xFFFFFF00)  # HERE


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
