# serpent:reject SPT1016
# serpent:at HERE
# serpent:message this property has no host equivalent
# serpent:doc-title tier-1 introspection property (.value)
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x.value  # HERE
