# serpent:reject SPT3015
# serpent:at HERE
# serpent:message truthiness is only defined for numeric chain types and Bool; write the explicit test, e.g. len(v) > U32(0) or storage.has(k)
# serpent:doc-title truthiness of a non-numeric chain value
from serpent import Env, Symbol, U32, contract


@contract
class Contract:
    def compute(self, env: Env, s: Symbol) -> U32:
        if s:  # HERE
            return U32(1)
        return U32(0)
