# serpent:reject SPT1010
# serpent:at HERE
# serpent:message compare two values at a time
# serpent:doc-title chained comparison
from serpent import Bool, Env, U32, contract


@contract
class Contract:
    def in_range(self, env: Env, x: U32) -> Bool:
        return Bool(U32(0) < x < U32(100))  # HERE
