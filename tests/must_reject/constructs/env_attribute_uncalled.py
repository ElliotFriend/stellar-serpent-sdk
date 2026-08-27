# serpent:reject SPT1038
# serpent:at HERE
# serpent:message env API used with an unsupported call shape
# serpent:doc-title Env attribute referenced without being called
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        bucket = env.storage  # HERE
        return x
