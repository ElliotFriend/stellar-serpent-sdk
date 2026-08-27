# serpent:reject SPT1005
# serpent:at HERE
# serpent:message lambdas and closures are not supported
# serpent:doc-title lambda expression
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        f = lambda y: y + U32(1)  # HERE
        return f(x)
