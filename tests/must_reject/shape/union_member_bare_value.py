# serpent:reject SPT4022
# serpent:at HERE
# serpent:message union cases are declared with variant(...)
# serpent:doc-title @contractunion case declared as a bare value
from serpent import ContractUnion, Env, U32, contract, contractunion, variant


@contractunion
class Shape(ContractUnion):
    Empty = variant()
    Circle = 3  # HERE


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
