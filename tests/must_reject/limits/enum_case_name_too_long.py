# serpent:reject SPT5003
# serpent:at HERE
# serpent:message a declared case name is too long
# serpent:doc-title over-long @contractenum case name
# 61 characters: an int-enum case name never becomes a Symbol (the value is a
# bare u32), so its cap is the 60-character spec case-name limit, not 32.
from serpent import ContractEnum, Env, U32, contract, contractenum, enumvalue


@contractenum
class Level(ContractEnum):
    LLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLL = enumvalue(0)  # HERE


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
