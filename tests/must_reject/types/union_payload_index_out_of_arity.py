# serpent:reject SPT3021
# serpent:at HERE
# serpent:message this payload() read matches no variant of the union
# serpent:doc-title payload() index at or above the union's widest variant
from serpent import ContractUnion, Env, U32, contract, contractunion, variant


@contractunion
class Shape(ContractUnion):
    Circle = variant(U32)
    Rect = variant(U32, U32)


@contract
class Contract:
    def compute(self, env: Env, s: Shape) -> U32:
        return s.payload(U32(2), U32)  # HERE
