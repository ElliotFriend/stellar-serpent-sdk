# serpent:reject SPT4013
# serpent:at HERE
# serpent:message a class may carry exactly one serpent decorator
# serpent:doc-title class with more than one serpent decorator
from serpent import Env, U32, contract, contracttype


@contract
@contracttype
class Contract:  # HERE
    def compute(self, env: Env, x: U32) -> U32:
        return x
