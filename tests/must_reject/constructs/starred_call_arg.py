# serpent:reject SPT1007
# serpent:at HERE
# serpent:message argument unpacking is not supported
# serpent:doc-title starred call argument
from serpent import Env, U32, Vec, contract


@contract
class Contract:
    def compute(self, env: Env, v: Vec[U32]) -> U32:
        return len(*v)  # HERE
