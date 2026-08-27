# serpent:reject SPT1029
# serpent:at HERE
# serpent:message assign one name at a time
# serpent:doc-title tuple/multi assignment target
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, a: U32, b: U32) -> U32:
        x, y = a, b  # HERE
        return x
