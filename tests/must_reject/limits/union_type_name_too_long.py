# serpent:reject SPT5002
# serpent:at HERE
# serpent:message type name is too long (> 60)
# serpent:doc-title over-long @contractunion type name
# 61 characters. The mechanism is `limits._check_type_name`, which visits every
# decorated type -- so the two M1-E2 kinds joined it the moment
# `loader._DECORATOR_KINDS` named them.
from serpent import ContractUnion, Env, U32, contract, contractunion, variant


@contractunion
class SSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSS(ContractUnion):  # HERE
    Empty = variant()


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
