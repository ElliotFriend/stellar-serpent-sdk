# serpent:reject SPT4010
# serpent:at HERE
# serpent:message error codes must be unique within the enum
# serpent:doc-title @contracterror with a duplicate code
from serpent import Env, U32, contract, contracterror, errorcode


@contracterror
class Err:
    NotFound = errorcode(1)
    AlsoNotFound = errorcode(1)  # HERE


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
