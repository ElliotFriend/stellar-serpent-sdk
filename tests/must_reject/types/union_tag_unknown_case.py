# serpent:reject SPT3022
# serpent:at HERE
# serpent:message this tag() comparison names no variant of the union
# serpent:doc-title tag() compared against a Symbol naming no variant
from serpent import ContractUnion, Env, Symbol, U32, contract, contractunion, variant


@contractunion
class Shape(ContractUnion):
    Circle = variant(U32)
    Rect = variant(U32, U32)


@contract
class Contract:
    def compute(self, env: Env, s: Shape) -> U32:
        if s.tag() == Symbol("Cirlce"):  # HERE
            return U32(1)
        return U32(0)
