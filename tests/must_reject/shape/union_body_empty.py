# serpent:reject SPT4021
# serpent:at HERE
# serpent:message a union or int enum must declare at least one case
# serpent:doc-title empty @contractunion
from serpent import ContractUnion, Env, U32, contract, contractunion


@contractunion
class Shape(ContractUnion):  # HERE
    pass


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
