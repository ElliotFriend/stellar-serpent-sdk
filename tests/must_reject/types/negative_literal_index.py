# serpent:reject SPT3011
# serpent:at HERE
# serpent:message negative indices are not representable on chain
# serpent:doc-title negative literal subscript index
from serpent import Bytes, Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, b: Bytes) -> U32:
        return b[-1]  # HERE
