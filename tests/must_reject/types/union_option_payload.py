# serpent:reject SPT4012
# serpent:at HERE
# serpent:message struct fields need a chain-type annotation
# serpent:doc-title @contractunion variant with an Option payload
# A variant payload is an `ScVec` element, and an element has no empty
# spelling: `Vec` cannot hold a `None`. The idiomatic union answer is a case
# of its own for the absent value (`Nothing = variant()`), which is why this
# is refused where it is written rather than left to fail at tier 1.
from serpent import ContractUnion, Env, U32, contract, contractunion, variant


@contractunion
class Maybe(ContractUnion):
    Nothing = variant()
    Some = variant(U32 | None)  # HERE


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
