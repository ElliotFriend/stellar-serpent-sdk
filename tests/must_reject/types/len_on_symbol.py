# serpent:reject SPT3009
# serpent:at HERE
# serpent:message len() is only supported on Vec, Map, and Bytes
# serpent:doc-title len() on Symbol
from serpent import Env, Symbol, U32, contract


@contract
class Contract:
    def compute(self, env: Env, s: Symbol) -> U32:
        return U32(len(s))  # HERE
