# serpent:reject SPT4006
# serpent:at HERE
# serpent:message __init__ compiles to the constructor and must return None
# serpent:doc-title __init__ not annotated -> None
from serpent import Env, U32, contract


@contract
class Contract:
    def __init__(self, env: Env) -> U32:  # HERE
        return U32(0)

    def compute(self, env: Env, x: U32) -> U32:
        return x
