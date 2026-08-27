# serpent:reject SPT5001
# serpent:at HERE
# serpent:message name is too long (> 30) or uses characters outside [a-zA-Z0-9_]
# serpent:doc-title over-long parameter name
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa: U32) -> U32:  # HERE
        return aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
