# serpent:reject SPT1013
# serpent:at HERE
# serpent:message slicing via subscript is not supported; use .slice(lo, hi)
# serpent:doc-title subscript slice (Bytes[a:b])
from serpent import Bytes, Env, contract


@contract
class Contract:
    def compute(self, env: Env, b: Bytes) -> Bytes:
        return b[0:2]  # HERE
