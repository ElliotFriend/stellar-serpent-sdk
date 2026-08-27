# serpent:reject SPT3006
# serpent:at HERE
# serpent:message there are no floats on chain; use // for truncating integer division
# serpent:doc-title true division (/)
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, a: U32, b: U32) -> U32:
        return a / b  # HERE
