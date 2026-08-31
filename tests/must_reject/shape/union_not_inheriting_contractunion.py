# serpent:reject SPT4025
# serpent:at HERE
# serpent:message union classes must inherit ContractUnion
# serpent:doc-title @contractunion without its base class
from serpent import Env, U32, contract, contractunion, variant


@contractunion
class Shape:  # HERE
    Empty = variant()


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
