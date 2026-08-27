# serpent:reject SPT4011
# serpent:at HERE
# serpent:message an error enum must declare at least one member
# serpent:doc-title empty @contracterror enum
from serpent import Env, U32, contract, contracterror


@contracterror
class Err:  # HERE
    pass


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
