# serpent:reject SPT4024
# serpent:at HERE
# serpent:message int-enum discriminants must be unique within the enum
# serpent:doc-title @contractenum duplicate discriminant
from serpent import ContractEnum, Env, U32, contract, contractenum, enumvalue


@contractenum
class Level(ContractEnum):
    Low = enumvalue(1)
    High = enumvalue(1)  # HERE


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
