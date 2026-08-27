# serpent:reject SPT1022
# serpent:at HERE
# serpent:message a contract cannot catch its own errors
# serpent:doc-title try/except
from serpent import Env, U32, contract


@contract
class Contract:
    def safe_div(self, env: Env, a: U32, b: U32) -> U32:
        try:  # HERE
            return a // b
        except Exception:
            return U32(0)
