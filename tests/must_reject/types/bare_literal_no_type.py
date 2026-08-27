# serpent:reject SPT3008
# serpent:at HERE
# serpent:message wrap the literal in a chain type, e.g. U32(5)
# serpent:doc-title bare literal with no chain type in scope
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env) -> U32:
        x = 5  # HERE
        return x
