# serpent:reject SPT1039
# serpent:at HERE
# serpent:message a map literal may not repeat a key
# serpent:doc-title map literal with a duplicate key
from serpent import Env, Map, Symbol, U32, contract


@contract
class Contract:
    def compute(self, env: Env) -> U32:
        m = Map(Symbol, U32, [(Symbol("a"), U32(1)), (Symbol("a"), U32(2))])  # HERE
        return m.get(Symbol("a"))
