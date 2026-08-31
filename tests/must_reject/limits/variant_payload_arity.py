# serpent:reject SPT5006
# serpent:at HERE
# serpent:message a variant payload carries at most 12 values
# serpent:doc-title variant payload wider than 12 values
# The refusal comes from `variant()` itself, in the class body, before the
# decorator runs -- so its message names no member and the diagnostic lands on
# the class statement.
from serpent import ContractUnion, Env, U32, contract, contractunion, variant


@contractunion
class Shape(ContractUnion):  # HERE
    Wide = variant(U32, U32, U32, U32, U32, U32, U32, U32, U32, U32, U32, U32, U32)


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
