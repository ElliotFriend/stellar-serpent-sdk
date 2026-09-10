# serpent:reject SPT2004
# serpent:at HERE
# serpent:message name shadows an existing declaration
# serpent:doc-title parameter shadows a declared type
from serpent import Env, U32, contract, contracttype


@contracttype
class Point:
    x: U32


@contract
class Contract:
    def compute(self, env: Env, Point: U32) -> U32:  # HERE
        return Point
