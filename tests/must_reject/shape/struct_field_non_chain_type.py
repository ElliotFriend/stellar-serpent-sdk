# serpent:reject SPT4012
# serpent:at HERE
# serpent:message struct fields need a chain-type annotation
# serpent:doc-title @contracttype field with a non-chain annotation
from serpent import Env, U32, contract, contracttype


@contracttype
class Point:
    x: int  # HERE


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
