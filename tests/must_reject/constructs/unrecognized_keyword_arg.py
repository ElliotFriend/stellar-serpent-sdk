# serpent:reject SPT1035
# serpent:at HERE
# serpent:message keyword arguments are only accepted where the recognized API names the parameter
# serpent:doc-title keyword argument outside the recognition table
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env) -> U32:
        return U32(value=5)  # HERE
