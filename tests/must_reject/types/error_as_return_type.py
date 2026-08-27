# serpent:reject SPT3001
# serpent:at HERE
# serpent:message Error is never a returnable value (S8)
# serpent:doc-title Error enum as a return type
from serpent import Env, U32, contract, contracterror, errorcode


@contracterror
class Err:
    NotFound = errorcode(1)


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> Err:  # HERE
        raise Err.NotFound
