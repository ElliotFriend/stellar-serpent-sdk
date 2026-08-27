# serpent:reject SPT3003
# serpent:at HERE
# serpent:message operands must share the same chain-integer type
# serpent:doc-title cross-width arithmetic (U32 + U64)
from serpent import Env, U32, U64, contract


@contract
class Contract:
    def add(self, env: Env, a: U32, b: U64) -> U64:
        return a + b  # HERE
