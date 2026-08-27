# serpent:reject SPT2005
# serpent:at HERE
# serpent:message a contract may only import from serpent
# serpent:doc-title import from a non-serpent module
from serpent import Env, U32, contract
import os  # HERE


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
