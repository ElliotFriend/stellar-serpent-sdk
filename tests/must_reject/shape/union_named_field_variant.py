# serpent:reject SPT4022
# serpent:at HERE
# serpent:message union cases are declared with variant(...)
# serpent:doc-title @contractunion variant with named fields
# A union carries TUPLE variants: `variant(U32, U32)`, positional and in
# declaration order. Rust's struct-variant shape (`Circle { radius: u32 }`) has
# no spelling here, and the closest one -- a named field in the class body --
# is a declaration form the body check admits (an error enum needs it), so the
# decorator is what refuses it.
from serpent import ContractUnion, Env, U32, contract, contractunion, variant


@contractunion
class Shape(ContractUnion):
    Empty = variant()
    radius: U32 = U32(0)  # HERE


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
