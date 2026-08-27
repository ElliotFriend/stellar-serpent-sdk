# serpent:reject SPT4019
# serpent:at HERE
# serpent:message expected exactly one @contract class per module
# serpent:doc-title module declaring more than one @contract class
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x


@contract
class SecondContract:  # HERE
    def compute(self, env: Env, x: U32) -> U32:
        return x
