# serpent:reject SPT3007
# serpent:at HERE
# serpent:message this unary operator is not supported
# serpent:doc-title omitted unary operator (~)
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, a: U32) -> U32:
        return ~a  # HERE
