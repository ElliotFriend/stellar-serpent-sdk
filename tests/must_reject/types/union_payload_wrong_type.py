# serpent:reject SPT3021
# serpent:at HERE
# serpent:message this payload() read matches no variant of the union
# serpent:doc-title payload() ty that no variant declares at that index
from serpent import Bool, ContractUnion, Env, U32, contract, contractunion, variant


@contractunion
class Shape(ContractUnion):
    Circle = variant(U32)
    Rect = variant(U32, U32)


@contract
class Contract:
    def compute(self, env: Env, s: Shape) -> Bool:
        return s.payload(U32(0), Bool)  # HERE
