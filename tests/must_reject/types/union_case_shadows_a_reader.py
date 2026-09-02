# serpent:reject SPT2004
# serpent:at HERE
# serpent:message name shadows an existing declaration
# serpent:doc-title @contractunion case named after a base reader
# A case installs a descriptor under its own name, so a case named `tag` or
# `payload` replaces the reader every union value is read through: the value
# could then never be read at all. The refused set is `ContractUnion`'s own
# public attributes, so a reader added later is covered the day it is added.
from serpent import ContractUnion, Env, U32, contract, contractunion, variant


@contractunion
class Shape(ContractUnion):
    Empty = variant()
    tag = variant(U32)  # HERE


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
