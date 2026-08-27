# serpent:reject SPT1019
# serpent:at HERE
# serpent:message iterate a Map via map.keys()/map.values(); walk Bytes with a while loop indexed by bytes[i] up to len(b); tuples cannot be iterated
# serpent:doc-title for loop over a Map
from serpent import Env, Map, Symbol, U32, contract


@contract
class Contract:
    def compute(self, env: Env, m: Map[Symbol, U32]) -> U32:
        total = U32(0)
        for k in m:  # HERE
            total = U32(0)
        return total
