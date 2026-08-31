# serpent:reject SPT5003
# serpent:at HERE
# serpent:message a declared case name is too long
# serpent:doc-title over-long @contractunion variant name
# Exactly 33 characters: one past `val.SCSYMBOL_LIMIT`, because a variant name
# BECOMES a runtime Symbol (ruling E8), so a 33-character name would decode in
# the spec and name a value that cannot exist on chain.
from serpent import ContractUnion, Env, U32, contract, contractunion, variant


@contractunion
class Shape(ContractUnion):
    VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV = variant(U32)  # HERE


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
