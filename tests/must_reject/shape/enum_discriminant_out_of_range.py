# serpent:reject SPT4023
# serpent:at HERE
# serpent:message an int-enum discriminant must be a u32
# serpent:doc-title @contractenum discriminant outside the u32 range
from serpent import ContractEnum, Env, U32, contract, contractenum, enumvalue


@contractenum
class Level(ContractEnum):
    Low = enumvalue(-1)  # HERE


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
