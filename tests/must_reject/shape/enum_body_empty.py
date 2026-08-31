# serpent:reject SPT4021
# serpent:at HERE
# serpent:message a union or int enum must declare at least one case
# serpent:doc-title empty @contractenum
from serpent import ContractEnum, Env, U32, contract, contractenum


@contractenum
class Level(ContractEnum):  # HERE
    pass


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
